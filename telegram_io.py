import logging
import os
from datetime import datetime

import yaml
from aiogram import Bot
from aiogram.types import BufferedInputFile
from zoneinfo import ZoneInfo

from db import get_all_users, load_today_cover, save_today_cover
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


def build_channel_sign_messages(
    context: dict, signs: dict, include_intro: bool = True
) -> list[str]:
    vibes = context.get("vibes", {})
    global_summary = context.get("global_summary", "")
    affirmation = context.get("affirmation", "")
    messages: list[str] = []
    first = True
    for sign in signs.keys():
        vibe = vibes.get(sign, "Вайб формується. Перевір пізніше.")
        lines: list[str] = []
        if first and include_intro:
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


async def send_daily_cover(
    bot: Bot, client, channel_id: str, context: dict, today_key: str
) -> bool:
    """Render+send the daily cover image. Returns True on success.

    The cover carries the affirmation (title) and intro (body). Background is
    cached per day so /broadcast_now retries do not re-pay the image API.
    Never raises — returns False so the caller can fall back to text-only.
    """
    import render  # local import: Pillow is only needed when covers are used

    try:
        affirmation = context.get("affirmation", "")
        intro = context.get("global_summary", "")
        png = load_today_cover(today_key)
        if png is None:
            prompt = render.build_background_prompt(intro)
            background = await render.generate_background(client, prompt)
            buf = render.render_card(affirmation, intro, background)
            png = buf.getvalue()
            save_today_cover(today_key, png)
        await bot.send_photo(
            channel_id,
            BufferedInputFile(png, filename="vibe.png"),
        )
        return True
    except Exception as exc:  # noqa: BLE001 - cover must never block the broadcast
        logger.warning("Daily cover failed (%s); falling back to text intro", exc)
        return False


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
        today_key = datetime.now(timezone).date().isoformat()
        cover_sent = await send_daily_cover(bot, client, channel_id, context, today_key)
        # When the cover carries the intro, don't repeat it in the first sign post.
        for channel_message in build_channel_sign_messages(
            context, signs, include_intro=not cover_sent
        ):
            try:
                await bot.send_message(channel_id, channel_message)
                sent += 1
            except Exception as exc:
                failed += 1
                logger.warning(
                    "Failed to send to channel=%s: %s", channel_id, exc
                )

    logger.info("Broadcast complete: %d sent, %d failed", sent, failed)
