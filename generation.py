import json
import logging
import os
import re
import time
from collections import Counter
from datetime import datetime
from zoneinfo import ZoneInfo

import openai
from openai import AsyncOpenAI
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from db import load_recent_intros, load_today_context, save_today_context
from news import fetch_news_blob
from prompts.loader import load_prompt

logger = logging.getLogger(__name__)

FALLBACK_VIBE = "Вайб формується. Перевір пізніше."

# Tone variants for A/B testing (Workstream B, idea #6). Selected via the
# CHANNEL_TONE env var; defaults to "sharp".
TONE_VARIANTS = {
    "sharp": "Цей випуск — гостра іронія з легким підʼюджуванням читача: сміливо, дотепно, але по-доброму.",
    "savage": "Цей випуск — саркастична іронія на межі: гостро, провокативно, чіпко, але без приниження читача.",
}
DEFAULT_TONE = "savage"


def build_channel_system(tone: str | None = None) -> str:
    """Load the channel system prompt with the chosen tone directive injected."""
    tone = tone or os.getenv("CHANNEL_TONE", DEFAULT_TONE)
    directive = TONE_VARIANTS.get(tone, TONE_VARIANTS[DEFAULT_TONE])
    return load_prompt("channel_system").format(tone_directive=directive)


def _first_word(text: str) -> str:
    """First meaningful word, ignoring leading emoji/punctuation/sign labels."""
    cleaned = re.sub(r"^[^\wа-яіїєґА-ЯІЇЄҐ]+", "", text.strip())
    match = re.match(r"[\wа-яіїєґА-ЯІЇЄҐ’'-]+", cleaned)
    return match.group(0).lower() if match else ""


def _needs_variety_retry(vibes: dict) -> bool:
    """True if any vibe opens with «Сьогодні» or first words are too repetitive."""
    first_words = [_first_word(v) for v in vibes.values() if v]
    if not first_words:
        return False
    if any(w == "сьогодні" for w in first_words):
        return True
    most_common = Counter(first_words).most_common(1)[0][1]
    return most_common / len(first_words) > 0.30


# --- Safety guard (war sensitivity) ------------------------------------------
# This is a Ukrainian channel during wartime. Content must NEVER reference, joke
# about, or aestheticize war, explosions, shelling, air-raid alerts, casualties
# or destruction — not even ironically. The system prompt forbids it, but a
# single bad generation must never reach the channel, so we also enforce it in
# code: scan output, regenerate once with a hard correction, and as a last
# resort scrub offending text with a neutral fallback.
UNSAFE_TERMS = [
    "вибух", "обстріл", "обстрел", "ракет", "дрон", "шахед", "бомб", "снаряд",
    "зруйнов", "руїн", "тривог", "сирен", "жертв", "загибл", "загину",
    "поранен", "окуп", "війн", "война", "war", "евакуа",
]
_UNSAFE_RE = re.compile("|".join(UNSAFE_TERMS), re.IGNORECASE)

SAFE_FALLBACK_INTRO = "Бережіть себе й тих, хто поруч. Нехай цей день буде добрим до вас."
SAFE_FALLBACK_VIBE = (
    "Гарний день, щоб подбати про себе й близьких — маленька турбота "
    "повертається сторицею."
)
SAFETY_CORRECTION = (
    "СТОП. У тексті є згадки про війну, вибухи, обстріли, повітряні тривоги, "
    "жертви чи руйнування — або іронія на цю тему. Це категорично заборонено. "
    "Повністю ІГНОРУЙ будь-які новини про війну/вибухи/обстріли/тривоги/жертви/"
    "руйнування — не згадуй і не обігруй їх навіть жартома. Пиши лише про "
    "нейтральні побутові теми (погода, культура, робота, стосунки, спорт, "
    "технології, повсякдення). Перепиши JSON у тому ж форматі. Лише JSON."
)


def _contains_unsafe(text: str) -> bool:
    return bool(text) and bool(_UNSAFE_RE.search(text))


def _filter_news(blob: str) -> str:
    """Drop war/tragedy items so they never become 'fuel' for the model."""
    if not blob:
        return blob
    safe_lines = [ln for ln in blob.splitlines() if not _contains_unsafe(ln)]
    removed = blob.count("\n") + 1 - len(safe_lines)
    if removed > 0:
        logger.info("Filtered %d unsafe news item(s) before generation.", removed)
    return "\n".join(safe_lines)


def _payload_has_unsafe(payload: dict) -> bool:
    fields = [payload.get("affirmation", ""), payload.get("global_summary", "")]
    fields += list((payload.get("vibes") or {}).values())
    return any(_contains_unsafe(t) for t in fields)


def _scrub_context(context: dict) -> dict:
    """Last-resort scrub: replace any still-unsafe field with a neutral fallback."""
    if _contains_unsafe(context.get("global_summary", "")):
        logger.warning("Safety scrub: replacing unsafe intro with fallback.")
        context["global_summary"] = SAFE_FALLBACK_INTRO
    if _contains_unsafe(context.get("affirmation", "")):
        logger.warning("Safety scrub: dropping unsafe affirmation.")
        context["affirmation"] = ""
    vibes = context.get("vibes", {})
    for sign, vibe in list(vibes.items()):
        if _contains_unsafe(vibe):
            logger.warning("Safety scrub: replacing unsafe vibe for %s.", sign)
            vibes[sign] = SAFE_FALLBACK_VIBE
    return context


_OPENAI_ERRORS = (
    openai.APIError,
    openai.APITimeoutError,
    openai.RateLimitError,
)


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=1, max=10),
    retry=retry_if_exception_type(_OPENAI_ERRORS + (json.JSONDecodeError,)),
    reraise=True,
)
async def complete_json(
    client: AsyncOpenAI,
    model: str,
    messages: list[dict],
    temperature: float = 0.6,
) -> dict:
    """Call the chat completions API expecting a JSON object, with retries."""
    start = time.perf_counter()
    response = await client.chat.completions.create(
        model=model,
        messages=messages,
        response_format={"type": "json_object"},
        temperature=temperature,
    )
    elapsed = time.perf_counter() - start
    logger.info("OpenAI JSON call took %.2fs", elapsed)
    return json.loads(response.choices[0].message.content)


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=1, max=10),
    retry=retry_if_exception_type(_OPENAI_ERRORS),
    reraise=True,
)
async def complete_text(
    client: AsyncOpenAI,
    model: str,
    messages: list[dict],
    temperature: float = 0.7,
) -> str:
    """Call the chat completions API expecting plain text, with retries."""
    start = time.perf_counter()
    response = await client.chat.completions.create(
        model=model,
        messages=messages,
        temperature=temperature,
    )
    elapsed = time.perf_counter() - start
    logger.info("OpenAI text call took %.2fs", elapsed)
    return response.choices[0].message.content.strip()


async def generate_daily_context(
    client: AsyncOpenAI,
    signs: dict,
    rss_url: str | None,
    model: str,
    telegram_source: dict | None = None,
    tone: str | None = None,
    news_blob: str | None = None,
) -> dict:
    if news_blob is None:
        news_blob = await fetch_news_blob(rss_url, telegram_source=telegram_source)
    news_blob = _filter_news(news_blob)

    signs_payload = {
        sign: {
            "traits": data.get("traits", []),
            "specificity": data.get("specificity", ""),
        }
        for sign, data in signs.items()
    }

    system_prompt = build_channel_system(tone)
    user_prompt = (
        "Повідомлення з джерела новин:\n"
        f"{news_blob}\n\n"
        "Конфіг знаків:\n"
        f"{json.dumps(signs_payload, ensure_ascii=False)}\n\n"
        "Поверни JSON з ключами: affirmation (коротке 1 речення), "
        "global_summary (рядок) і vibes (обʼєкт: знак -> текст вайбу). "
        "Кожен вайб — 2–3 речення, починається інакше, жоден не зі слова «Сьогодні». "
        "Лише JSON."
    )

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]
    try:
        payload = await complete_json(client, model, messages=messages, temperature=0.9)
    except Exception as exc:
        logger.warning("Vibes generation failed after retries: %s", exc)
        payload = {}

    vibes = payload.get("vibes", {}) or {}

    # Anti-repetition guard: one corrective retry if openers are robotic.
    if vibes and _needs_variety_retry(vibes):
        logger.info("Variety guard triggered; retrying vibes generation once.")
        retry_messages = messages + [
            {"role": "assistant", "content": json.dumps(payload, ensure_ascii=False)},
            {
                "role": "user",
                "content": (
                    "Забагато вайбів починаються однаково або зі слова «Сьогодні». "
                    "Перепиши JSON: кожен вайб має починатися інакше, з різних слів "
                    "і конструкцій. Лише JSON."
                ),
            },
        ]
        try:
            retried = await complete_json(
                client, model, messages=retry_messages, temperature=0.95
            )
            if retried.get("vibes"):
                payload = retried
                vibes = retried.get("vibes", {}) or {}
        except Exception as exc:
            logger.warning("Variety retry failed: %s", exc)

    # Safety guard: never reference war/tragedy. Regenerate once if violated.
    if _payload_has_unsafe(payload):
        logger.warning("Safety guard triggered (war/tragedy content); regenerating.")
        safety_messages = messages + [
            {"role": "assistant", "content": json.dumps(payload, ensure_ascii=False)},
            {"role": "user", "content": SAFETY_CORRECTION},
        ]
        try:
            retried = await complete_json(
                client, model, messages=safety_messages, temperature=0.6
            )
            if retried.get("vibes"):
                payload = retried
                vibes = retried.get("vibes", {}) or {}
        except Exception as exc:
            logger.warning("Safety regeneration failed: %s", exc)

    missing = [sign for sign in signs.keys() if sign not in vibes or not vibes[sign]]
    if missing:
        logger.warning(
            "Vibes JSON missing %d signs; filling with fallback: %s",
            len(missing),
            ", ".join(missing),
        )
        for sign in missing:
            vibes[sign] = FALLBACK_VIBE

    # Post-process global_summary into a zodiac-forecast intro
    raw_global_summary = payload.get("global_summary", "")
    if raw_global_summary:
        recent = load_recent_intros(n_days=1)
        yesterday_hint = (
            f"Учора інтро починалося так: «{recent[0]}» — почни сьогодні інакше."
            if recent
            else ""
        )
        summary_user_prompt = load_prompt("intro").format(
            news_blob=news_blob,
            raw_global_summary=raw_global_summary,
            yesterday_hint=yesterday_hint,
        )
        intro_messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": summary_user_prompt},
        ]
        try:
            polished_summary = await complete_text(
                client, model, messages=intro_messages, temperature=0.7
            )
            # Safety guard on the intro (this is what lands on the cover image).
            if _contains_unsafe(polished_summary):
                logger.warning("Safety guard triggered on intro; regenerating.")
                polished_summary = await complete_text(
                    client,
                    model,
                    messages=intro_messages
                    + [
                        {"role": "assistant", "content": polished_summary},
                        {"role": "user", "content": SAFETY_CORRECTION},
                    ],
                    temperature=0.6,
                )
        except Exception as exc:
            logger.warning("Intro polish failed after retries: %s", exc)
            polished_summary = raw_global_summary
    else:
        polished_summary = raw_global_summary

    context = {
        "affirmation": payload.get("affirmation", ""),
        "global_summary": polished_summary,
        "vibes": vibes,
    }
    # Final hard backstop: nothing unsafe is ever returned/cached/published.
    return _scrub_context(context)


async def get_or_generate_context(
    client: AsyncOpenAI,
    signs: dict,
    rss_url: str | None,
    model: str,
    timezone: ZoneInfo,
    telegram_source: dict | None = None,
) -> dict:
    today_key = datetime.now(timezone).date().isoformat()
    cached = load_today_context(today_key)
    if cached:
        return cached
    context = await generate_daily_context(
        client, signs, rss_url, model, telegram_source=telegram_source
    )
    save_today_context(today_key, context)
    return context


def build_personal_prompt(sign: str, sign_data: dict, context: dict, question: str) -> str:
    from telegram_io import display_sign_with_emoji

    traits = ", ".join(sign_data.get("traits", []))
    specificity = sign_data.get("specificity", "")
    vibe = context.get("vibes", {}).get(sign, "")
    global_summary = context.get("global_summary", "")
    return (
        f"Знак користувача: {display_sign_with_emoji(sign)}\n"
        f"Риси: {traits}\n"
        f"Специфіка: {specificity}\n"
        f"Вайб дня: {vibe}\n"
        f"Глобальний підсумок: {global_summary}\n\n"
        f"Питання користувача: {question}\n\n"
        "Відповідай як практичний астрологічний коуч у 3–5 реченнях. "
        "Будь конкретним, повʼязуй пораду з вайбом і рисами, уникай категоричних тверджень. "
        "Відповідай лише українською."
    )
