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


# --- Dignity guard (war sensitivity, NOT topic filtering) --------------------
# Wartime Ukrainian channel. War news is welcome material, with nuance:
#   * strikes on Russian military/infrastructure targets (oil depots, plants) MAY
#     be played up positively/playfully — it's good news for morale;
#   * attacks on Ukraine and our losses are engaged only with dignity + hope;
#   * human casualties / deaths (ANY side) are NEVER a punchline.
# Keyword matching can't tell respectful from flippant, so an LLM judge checks the
# tone; we regenerate, or as a last resort swap in a supportive line, if it mocks.
SENSITIVE_TERMS = [
    "вибух", "обстріл", "обстрел", "ракет", "дрон", "шахед", "бомб", "снаряд",
    "зруйнов", "руїн", "тривог", "сирен", "жертв", "загибл", "загину",
    "поранен", "окуп", "війн", "война", "war", "евакуа",
]
_SENSITIVE_RE = re.compile("|".join(SENSITIVE_TERMS), re.IGNORECASE)

SUPPORTIVE_INTRO = (
    "День може бути непростим, та українці незламні. Бережіть себе й тримайтеся "
    "разом — попереду світло."
)
SUPPORTIVE_VIBE = (
    "Навіть у складні дні твоя сила нікуди не зникає. Ми незламні — тримайся, "
    "підтримуй близьких і вір у краще."
)
RESPECT_CORRECTION = (
    "У тексті є неприпустимий жарт чи знецінення людських жертв/смертей/"
    "страждань, або легковажність щодо атак на Україну. Перепиши JSON за "
    "правилами: удари по російських військових/інфраструктурних цілях (нафтобази, "
    "заводи) можна обігрувати позитивно й з азартом; атаки на Україну та наші "
    "втрати — лише з гідністю, теплом і вірою в незламність; НІКОЛИ не жартуй над "
    "людськими жертвами чи смертями з будь-якого боку. Легкі побутові теми лиши "
    "дотепними. Той самий формат. Лише JSON."
)


def _mentions_sensitive(text: str) -> bool:
    return bool(text) and bool(_SENSITIVE_RE.search(text))


async def _judge_respectful(client: AsyncOpenAI, model: str, context: dict) -> bool:
    """LLM check: True if difficult topics are handled with dignity, not mockery."""
    listing = "\n".join(
        [context.get("global_summary", ""), context.get("affirmation", "")]
        + list((context.get("vibes") or {}).values())
    )
    judge_prompt = (
        "Ти модеруєш україномовний зодіак-канал воєнного часу. Постав respectful=false "
        "ЛИШЕ якщо є насмішка/знецінення ЛЮДСЬКИХ жертв, смертей, поранених чи "
        "похоронів (з будь-якого боку), АБО іронія/знущання над стражданнями "
        "українців чи атаками на Україну.\n"
        "Інакше respectful=true. Зокрема, ДОПУСТИМО (respectful=true):\n"
        "- азартне/жартівливе обігрування ударів по російській техніці чи "
        "інфраструктурі (нафтобази, заводи, склади), якщо НЕ йдеться про людські "
        "смерті. Приклад: «Нехай твоя пристрасть палає, як російські нафтобази» = "
        "respectful=true;\n"
        "- згадка атак на Україну з гідністю й вірою в незламність. Приклад: «Ніч "
        "була неспокійною, але ми незламні» = respectful=true.\n"
        "НЕДОПУСТимо (respectful=false). Приклади: жарт над загиблими = false; "
        "«фоткайтесь на тлі зруйнованих будівель Києва» = false.\n"
        'Поверни лише JSON {"respectful": true/false}.\n\nТексти:\n' + listing
    )
    try:
        resp = await complete_json(
            client, model,
            messages=[{"role": "user", "content": judge_prompt}],
            temperature=0,
        )
        return bool(resp.get("respectful", True))
    except Exception as exc:  # noqa: BLE001 - don't nuke good content on a flake
        logger.warning("Respect judge failed (%s); assuming respectful", exc)
        return True


def _scrub_disrespectful(context: dict) -> dict:
    """Last resort: swap sensitive-topic fields for supportive resilience lines."""
    if _mentions_sensitive(context.get("global_summary", "")):
        logger.warning("Dignity scrub: replacing intro with supportive fallback.")
        context["global_summary"] = SUPPORTIVE_INTRO
    if _mentions_sensitive(context.get("affirmation", "")):
        context["affirmation"] = ""
    vibes = context.get("vibes", {})
    for sign, vibe in list(vibes.items()):
        if _mentions_sensitive(vibe):
            logger.warning("Dignity scrub: replacing vibe for %s.", sign)
            vibes[sign] = SUPPORTIVE_VIBE
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

    # Dignity guard: difficult topics are welcome, but only handled with respect
    # and optimism (resilience, hope) — never irony or mockery. Runs once on every
    # daily generation because resilient/flippant phrasing doesn't always contain
    # the trigger keywords. If the treatment is disrespectful, regenerate once;
    # if still off, swap any keyword-bearing field for a supportive resilience line.
    if not await _judge_respectful(client, model, context):
        logger.warning("Dignity guard triggered; regenerating with respect correction.")
        try:
            retried = await complete_json(
                client,
                model,
                messages=messages
                + [
                    {"role": "assistant", "content": json.dumps(payload, ensure_ascii=False)},
                    {"role": "user", "content": RESPECT_CORRECTION},
                ],
                temperature=0.7,
            )
            if retried.get("vibes"):
                context = {
                    "affirmation": retried.get("affirmation", ""),
                    "global_summary": retried.get("global_summary", polished_summary),
                    "vibes": retried.get("vibes", {}) or vibes,
                }
        except Exception as exc:
            logger.warning("Respect regeneration failed: %s", exc)
        if not await _judge_respectful(client, model, context):
            context = _scrub_disrespectful(context)

    return context


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
