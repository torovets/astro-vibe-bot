import json
import logging
import time
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

from db import load_today_context, save_today_context
from news import fetch_news_blob
from prompts.loader import load_prompt

logger = logging.getLogger(__name__)

FALLBACK_VIBE = "Вайб формується. Перевір пізніше."

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
) -> dict:
    news_blob = await fetch_news_blob(rss_url, telegram_source=telegram_source)

    signs_payload = {
        sign: {
            "traits": data.get("traits", []),
            "specificity": data.get("specificity", ""),
        }
        for sign, data in signs.items()
    }

    system_prompt = load_prompt("channel_system")
    user_prompt = (
        "Повідомлення з джерела новин:\n"
        f"{news_blob}\n\n"
        "Конфіг знаків:\n"
        f"{json.dumps(signs_payload, ensure_ascii=False)}\n\n"
        "Поверни JSON з ключами: affirmation (коротке 1 речення), "
        "global_summary (рядок) і vibes (обʼєкт: знак -> текст вайбу). "
        "Лише JSON."
    )

    try:
        payload = await complete_json(
            client,
            model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.6,
        )
    except Exception as exc:
        logger.warning("Vibes generation failed after retries: %s", exc)
        payload = {}

    vibes = payload.get("vibes", {}) or {}
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
        summary_user_prompt = load_prompt("intro").format(
            news_blob=news_blob,
            raw_global_summary=raw_global_summary,
        )
        try:
            polished_summary = await complete_text(
                client,
                model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": summary_user_prompt},
                ],
                temperature=0.7,
            )
        except Exception as exc:
            logger.warning("Intro polish failed after retries: %s", exc)
            polished_summary = raw_global_summary
    else:
        polished_summary = raw_global_summary

    return {
        "affirmation": payload.get("affirmation", ""),
        "global_summary": polished_summary,
        "vibes": vibes,
    }


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
