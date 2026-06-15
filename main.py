import asyncio
import logging
import os
from datetime import datetime
from zoneinfo import ZoneInfo

from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command, CommandStart
from aiogram.types import Message
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from dotenv import load_dotenv
from openai import AsyncOpenAI

from db import (
    get_user_sign,
    init_db,
    set_user_sign,
    upsert_user,
)
from generation import (
    build_personal_prompt,
    complete_text,
    get_or_generate_context,
)
from prompts.loader import load_prompt
from rubrics import post_hook, post_spotlight
from telegram_io import (
    broadcast_daily_vibes,
    build_channel_sign_messages,
    display_sign,
    display_sign_with_emoji,
    load_signs,
    normalize_sign,
    send_daily_cover,
    send_sign_cards,
)

logger = logging.getLogger(__name__)


def parse_admin_ids(raw_value: str | None) -> set[int]:
    if not raw_value:
        return set()
    tokens = raw_value.replace(",", " ").split()
    ids: set[int] = set()
    for token in tokens:
        if token.isdigit():
            ids.add(int(token))
    return ids


async def main() -> None:
    load_dotenv()
    telegram_token = os.getenv("TELEGRAM_BOT_TOKEN")
    openai_key = os.getenv("OPENAI_API_KEY")
    rss_url = os.getenv("RSS_FEED_URL")
    timezone_name = os.getenv("TIMEZONE", "UTC")
    model = os.getenv("OPENAI_MODEL", "gpt-5.4-mini")
    channel_id = os.getenv("BROADCAST_CHANNEL")
    admin_ids = parse_admin_ids(os.getenv("ADMIN_USER_IDS"))
    telegram_api_id = os.getenv("TELEGRAM_API_ID")
    telegram_api_hash = os.getenv("TELEGRAM_API_HASH")
    telegram_channel = os.getenv("TELEGRAM_NEWS_CHANNEL")
    telegram_limit = int(os.getenv("TELEGRAM_NEWS_LIMIT", "20"))
    telethon_session = os.getenv("TELETHON_SESSION", "telethon.session")
    telethon_session_string = os.getenv("TELETHON_SESSION_STRING")

    if not telegram_token or not openai_key:
        raise RuntimeError("Missing TELEGRAM_BOT_TOKEN or OPENAI_API_KEY.")

    timezone = ZoneInfo(timezone_name)

    init_db()
    signs = load_signs()
    client = AsyncOpenAI(api_key=openai_key)
    telegram_source = {
        "api_id": int(telegram_api_id) if telegram_api_id else None,
        "api_hash": telegram_api_hash,
        "channel": telegram_channel,
        "limit": telegram_limit,
        "session_path": telethon_session,
        "session_string": telethon_session_string,
    }

    bot = Bot(token=telegram_token)
    dp = Dispatcher()

    @dp.message(CommandStart())
    async def handle_start(message: Message) -> None:
        upsert_user(message.from_user.id, message.chat.id, message.from_user.username)
        await message.answer(
            "Ласкаво просимо до Astro Vibe Bot! Вкажи знак зодіаку командою "
            "/set_sign <sign>, щоб отримувати вайб дня та персональні прогнози."
        )

    @dp.message(Command("set_sign"))
    async def handle_set_sign(message: Message) -> None:
        upsert_user(message.from_user.id, message.chat.id, message.from_user.username)
        parts = (message.text or "").split(maxsplit=1)
        if len(parts) < 2:
            await message.answer("Використання: /set_sign Aries")
            return
        sign = normalize_sign(parts[1])
        if sign not in signs:
            await message.answer(
                "Невідомий знак. Обери один із: "
                + ", ".join(display_sign(s) for s in sorted(signs.keys()))
            )
            return
        set_user_sign(message.from_user.id, sign)
        await message.answer(f"Знак збережено: {display_sign(sign)}.")

    @dp.message(Command("vibe"))
    async def handle_vibe(message: Message) -> None:
        upsert_user(message.from_user.id, message.chat.id, message.from_user.username)
        sign = get_user_sign(message.from_user.id)
        if not sign:
            await message.answer("Вкажи знак: /set_sign <sign>.")
            return
        context = await get_or_generate_context(
            client,
            signs,
            rss_url,
            model,
            timezone,
            telegram_source=telegram_source,
        )
        vibe = context.get("vibes", {}).get(sign, "Вайб формується. Перевір пізніше.")
        await message.answer(f"Вайб дня для {display_sign_with_emoji(sign)}:\n{vibe}")

    @dp.message(Command("broadcast_now"))
    async def handle_broadcast_now(message: Message) -> None:
        upsert_user(message.from_user.id, message.chat.id, message.from_user.username)
        if admin_ids and message.from_user.id not in admin_ids:
            await message.answer("Недостатньо прав для цієї команди.")
            return
        if not channel_id:
            await message.answer("BROADCAST_CHANNEL не налаштовано.")
            return
        context = await get_or_generate_context(
            client,
            signs,
            rss_url,
            model,
            timezone,
            telegram_source=telegram_source,
        )
        today_key = datetime.now(timezone).date().isoformat()
        cover_sent = await send_daily_cover(bot, client, channel_id, context, today_key)
        cards = await send_sign_cards(bot, client, channel_id, context, signs, today_key)
        if cards is None:
            for channel_message in build_channel_sign_messages(
                context, signs, include_intro=not cover_sent
            ):
                await bot.send_message(channel_id, channel_message)
            status = "текстом (зображення недоступні, див. лог)"
        else:
            status = "обкладинка + 12 карток знаків"
        await message.answer(f"Надіслано в канал: {status}.")

    @dp.message(Command("post_cover"))
    async def handle_post_cover(message: Message) -> None:
        upsert_user(message.from_user.id, message.chat.id, message.from_user.username)
        if admin_ids and message.from_user.id not in admin_ids:
            await message.answer("Недостатньо прав для цієї команди.")
            return
        if not channel_id:
            await message.answer("BROADCAST_CHANNEL не налаштовано.")
            return
        context = await get_or_generate_context(
            client,
            signs,
            rss_url,
            model,
            timezone,
            telegram_source=telegram_source,
        )
        today_key = datetime.now(timezone).date().isoformat()
        cover_sent = await send_daily_cover(
            bot, client, channel_id, context, today_key, force=True
        )
        if cover_sent:
            await message.answer("Нову обкладинку згенеровано і надіслано в канал.")
        else:
            await message.answer("Не вдалося згенерувати обкладинку (див. лог).")

    @dp.message(Command("post_spotlight"))
    async def handle_post_spotlight(message: Message) -> None:
        upsert_user(message.from_user.id, message.chat.id, message.from_user.username)
        if admin_ids and message.from_user.id not in admin_ids:
            await message.answer("Недостатньо прав для цієї команди.")
            return
        if not channel_id:
            await message.answer("BROADCAST_CHANNEL не налаштовано.")
            return
        sign_message = await post_spotlight(bot, client, signs, model, channel_id)
        await message.answer("Портрет знаку надіслано.\n\n" + sign_message[:200])

    @dp.message(Command("post_hook"))
    async def handle_post_hook(message: Message) -> None:
        upsert_user(message.from_user.id, message.chat.id, message.from_user.username)
        if admin_ids and message.from_user.id not in admin_ids:
            await message.answer("Недостатньо прав для цієї команди.")
            return
        if not channel_id:
            await message.answer("BROADCAST_CHANNEL не налаштовано.")
            return
        hook_message = await post_hook(bot, client, model, channel_id)
        await message.answer("Психологічний пост надіслано.\n\n" + hook_message[:200])

    @dp.message(F.text & ~F.text.startswith("/"))
    async def handle_personal_query(message: Message) -> None:
        upsert_user(message.from_user.id, message.chat.id, message.from_user.username)
        sign = get_user_sign(message.from_user.id)
        if not sign:
            await message.answer("Спочатку вкажи знак: /set_sign <sign>.")
            return
        context = await get_or_generate_context(
            client,
            signs,
            rss_url,
            model,
            timezone,
            telegram_source=telegram_source,
        )
        prompt = build_personal_prompt(sign, signs[sign], context, message.text)
        answer = await complete_text(
            client,
            model,
            messages=[
                {"role": "system", "content": load_prompt("personal_advisor")},
                {"role": "user", "content": prompt},
            ],
            temperature=0.7,
        )
        await message.answer(answer)

    scheduler = AsyncIOScheduler(timezone=timezone)
    scheduler.add_job(
        broadcast_daily_vibes,
        "cron",
        hour=9,
        minute=0,
        args=[
            bot,
            client,
            signs,
            rss_url,
            model,
            timezone,
            channel_id,
            telegram_source,
        ],
    )
    scheduler.add_job(
        post_spotlight,
        "cron",
        day_of_week="wed",
        hour=18,
        minute=0,
        args=[bot, client, signs, model, channel_id],
    )
    scheduler.add_job(
        post_hook,
        "cron",
        day_of_week="sat",
        hour=18,
        minute=0,
        args=[bot, client, model, channel_id],
    )
    scheduler.start()

    logger.info("Bot started; polling for updates")
    await dp.start_polling(bot)


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    asyncio.run(main())
