import logging
import os

import yaml
from aiogram import Bot
from zoneinfo import ZoneInfo

from db import get_all_users
from generation import get_or_generate_context

logger = logging.getLogger(__name__)

SIGNS_PATH = os.path.join(os.path.dirname(__file__), "config", "signs.yaml")

SIGN_NAME_UA = {
    "Aries": "Овен",
    "Taurus": "Телець",
    "Gemini": "Близнюки",
    "Cancer": "Рак",
    "Leo": "Лев",
    "Virgo": "Діва",
    "Libra": "Терези",
    "Scorpio": "Скорпіон",
    "Sagittarius": "Стрілець",
    "Capricorn": "Козеріг",
    "Aquarius": "Водолій",
    "Pisces": "Риби",
}
SIGN_EMOJI = {
    "Aries": "♈",
    "Taurus": "♉",
    "Gemini": "♊",
    "Cancer": "♋",
    "Leo": "♌",
    "Virgo": "♍",
    "Libra": "♎",
    "Scorpio": "♏",
    "Sagittarius": "♐",
    "Capricorn": "♑",
    "Aquarius": "♒",
    "Pisces": "♓",
}
SIGN_NAME_EN = {ua: en for en, ua in SIGN_NAME_UA.items()}


def load_signs() -> dict:
    with open(SIGNS_PATH, "r", encoding="utf-8") as handle:
        return yaml.safe_load(handle) or {}


def normalize_sign(sign: str) -> str:
    normalized = sign.strip().title()
    return SIGN_NAME_EN.get(normalized, normalized)


def display_sign(sign: str) -> str:
    return SIGN_NAME_UA.get(sign, sign)


def display_sign_with_emoji(sign: str) -> str:
    return f"{SIGN_EMOJI.get(sign, '')} {display_sign(sign)}".strip()


def build_channel_sign_messages(context: dict, signs: dict) -> list[str]:
    vibes = context.get("vibes", {})
    global_summary = context.get("global_summary", "")
    affirmation = context.get("affirmation", "")
    messages: list[str] = []
    first = True
    for sign in signs.keys():
        vibe = vibes.get(sign, "Вайб формується. Перевір пізніше.")
        lines: list[str] = []
        if first:
            if affirmation:
                lines.append(affirmation)
            if global_summary:
                lines.append(global_summary)
            if lines:
                lines.append("")
            first = False
        lines.append(f"{display_sign_with_emoji(sign)}: {vibe}")
        messages.append("\n".join(lines).strip())
    return messages


async def broadcast_daily_vibes(
    bot: Bot,
    client,
    signs: dict,
    rss_url: str | None,
    model: str,
    timezone: ZoneInfo,
    channel_id: str | None,
    telegram_source: dict | None,
) -> None:
    context = await get_or_generate_context(
        client,
        signs,
        rss_url,
        model,
        timezone,
        telegram_source=telegram_source,
    )
    vibes = context.get("vibes", {})
    global_summary = context.get("global_summary", "")
    sent = 0
    failed = 0
    for _, chat_id, sign in get_all_users():
        if not sign:
            message = (
                "Вкажи свій знак зодіаку: /set_sign <sign>, щоб отримувати вайб дня."
            )
        else:
            vibe = vibes.get(sign, "Вайб формується. Перевір пізніше.")
            message = f"Вайб дня для {display_sign_with_emoji(sign)}:\n{vibe}"
            if global_summary:
                message += f"\n\nГлобальний контекст: {global_summary}"
        try:
            await bot.send_message(chat_id, message)
            sent += 1
        except Exception as exc:
            failed += 1
            logger.warning("Failed to send to chat_id=%s: %s", chat_id, exc)

    if channel_id:
        for channel_message in build_channel_sign_messages(context, signs):
            try:
                await bot.send_message(channel_id, channel_message)
                sent += 1
            except Exception as exc:
                failed += 1
                logger.warning(
                    "Failed to send to channel=%s: %s", channel_id, exc
                )

    logger.info("Broadcast complete: %d sent, %d failed", sent, failed)
