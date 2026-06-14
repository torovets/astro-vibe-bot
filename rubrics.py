"""Weekly channel rubrics (Workstream C).

Two rubrics:
  * Sign spotlight  -> ironic portrait of one zodiac sign (rotates 12 signs).
  * Psychology hook -> relationship-psychology post (rotates topics).

Both honour the channel voice in ``prompts/channel_system.txt`` and reuse the
retry-wrapped ``generation.complete_text`` helper.
"""

import logging
import os

import yaml

import db
from generation import build_channel_system, complete_text
from prompts.loader import load_prompt
from telegram_io import SIGN_EMOJI, SIGN_NAME_UA

logger = logging.getLogger(__name__)

RUBRICS_PATH = os.path.join(os.path.dirname(__file__), "config", "rubrics.yaml")

RUBRIC_SPOTLIGHT = "sign_spotlight"
RUBRIC_PSYCH = "psych_hook"

# Fixed sign order so the spotlight rotation is deterministic.
SIGN_ORDER = list(SIGN_NAME_UA.keys())


def load_psych_topics() -> list[str]:
    with open(RUBRICS_PATH, "r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}
    return list(data.get("psych_topics", []))


async def generate_sign_spotlight(client, sign: str, sign_data: dict, model: str) -> str:
    """Generate an ironic portrait of one zodiac sign."""
    system = build_channel_system()
    user = load_prompt("sign_spotlight").format(
        sign=SIGN_NAME_UA.get(sign, sign),
        traits=", ".join(sign_data.get("traits", [])),
        specificity=sign_data.get("specificity", ""),
        stereotype=sign_data.get("stereotype", ""),
        love_style=sign_data.get("love_style", ""),
        money_style=sign_data.get("money_style", ""),
    )
    return await complete_text(
        client,
        model,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        temperature=0.85,
    )


async def generate_psych_hook(client, topic: str, model: str) -> str:
    """Generate a relationship-psychology post on ``topic``."""
    system = build_channel_system()
    user = load_prompt("psych_hook").format(topic=topic)
    return await complete_text(
        client,
        model,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        temperature=0.8,
    )


def spotlight_header(sign: str) -> str:
    return f"{SIGN_EMOJI.get(sign, '✨')} Портрет знаку: {SIGN_NAME_UA.get(sign, sign)}"


def psych_header(topic: str) -> str:
    return f"🧠 Психологія стосунків: {topic}"


async def post_spotlight(bot, client, signs: dict, model: str, channel_id: str | None) -> str:
    """Pick the next un-spotlighted sign, generate, send, and record it."""
    candidates = [sign for sign in SIGN_ORDER if sign in signs]
    sign = db.next_subject(RUBRIC_SPOTLIGHT, candidates)
    if not sign:
        raise RuntimeError("No signs available for spotlight rotation.")
    body = await generate_sign_spotlight(client, sign, signs[sign], model)
    message = f"{spotlight_header(sign)}\n\n{body}"
    if channel_id and bot is not None:
        await bot.send_message(channel_id, message)
    db.record_rubric(RUBRIC_SPOTLIGHT, sign)
    logger.info("Posted sign spotlight: %s", sign)
    return message


async def post_hook(bot, client, model: str, channel_id: str | None) -> str:
    """Pick the next un-used psych topic, generate, send, and record it."""
    topic = db.next_subject(RUBRIC_PSYCH, load_psych_topics())
    if not topic:
        raise RuntimeError("No psych topics configured.")
    body = await generate_psych_hook(client, topic, model)
    message = f"{psych_header(topic)}\n\n{body}"
    if channel_id and bot is not None:
        await bot.send_message(channel_id, message)
    db.record_rubric(RUBRIC_PSYCH, topic)
    logger.info("Posted psych hook: %s", topic)
    return message
