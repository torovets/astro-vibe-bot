import asyncio
import json
import os
import sqlite3
from datetime import datetime
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

import feedparser
import yaml
from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command, CommandStart
from aiogram.types import Message
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from dotenv import load_dotenv
from openai import OpenAI

DB_PATH = os.path.join(os.path.dirname(__file__), "data.db")
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
SIGN_NAME_EN = {ua: en for en, ua in SIGN_NAME_UA.items()}


def load_signs() -> dict:
    with open(SIGNS_PATH, "r", encoding="utf-8") as handle:
        return yaml.safe_load(handle) or {}


def init_db() -> None:
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                chat_id INTEGER NOT NULL,
                username TEXT,
                sign TEXT
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS daily_context (
                date TEXT PRIMARY KEY,
                context_json TEXT NOT NULL
            )
            """
        )


def upsert_user(user_id: int, chat_id: int, username: str | None) -> None:
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            """
            INSERT INTO users (user_id, chat_id, username)
            VALUES (?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                chat_id = excluded.chat_id,
                username = excluded.username
            """,
            (user_id, chat_id, username),
        )


def set_user_sign(user_id: int, sign: str) -> None:
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            "UPDATE users SET sign = ? WHERE user_id = ?",
            (sign, user_id),
        )


def get_user_sign(user_id: int) -> str | None:
    with sqlite3.connect(DB_PATH) as conn:
        row = conn.execute(
            "SELECT sign FROM users WHERE user_id = ?",
            (user_id,),
        ).fetchone()
    return row[0] if row and row[0] else None


def get_all_users() -> list[tuple[int, int, str | None]]:
    with sqlite3.connect(DB_PATH) as conn:
        rows = conn.execute(
            "SELECT user_id, chat_id, sign FROM users"
        ).fetchall()
    return [(row[0], row[1], row[2]) for row in rows]


def load_today_context(today_key: str) -> dict | None:
    with sqlite3.connect(DB_PATH) as conn:
        row = conn.execute(
            "SELECT context_json FROM daily_context WHERE date = ?",
            (today_key,),
        ).fetchone()
    if not row:
        return None
    return json.loads(row[0])


def save_today_context(today_key: str, context: dict) -> None:
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            """
            INSERT INTO daily_context (date, context_json)
            VALUES (?, ?)
            ON CONFLICT(date) DO UPDATE SET
                context_json = excluded.context_json
            """,
            (today_key, json.dumps(context)),
        )


def normalize_sign(sign: str) -> str:
    normalized = sign.strip().title()
    return SIGN_NAME_EN.get(normalized, normalized)


def display_sign(sign: str) -> str:
    return SIGN_NAME_UA.get(sign, sign)


def extract_invite_hash(channel: str) -> str | None:
    if not channel:
        return None
    if "t.me/+" in channel:
        return channel.split("t.me/+", 1)[1].split("?", 1)[0]
    parsed = urlparse(channel)
    if "joinchat" in parsed.path:
        return parsed.path.split("joinchat/", 1)[1].split("/", 1)[0]
    return None


async def fetch_telegram_messages(
    api_id: int | None,
    api_hash: str | None,
    channel: str | None,
    limit: int,
    session_path: str,
) -> list[str]:
    if not api_id or not api_hash or not channel:
        return []
    try:
        from telethon import TelegramClient
        from telethon.tl.functions.messages import ImportChatInviteRequest
    except Exception:
        return []

    client = TelegramClient(session_path, api_id, api_hash)
    await client.start()
    try:
        entity = None
        invite_hash = extract_invite_hash(channel)
        if invite_hash:
            try:
                result = await client(ImportChatInviteRequest(invite_hash))
                if getattr(result, "chats", None):
                    entity = result.chats[0]
            except Exception:
                entity = None
        if entity is None:
            entity = await client.get_entity(channel)
        messages: list[str] = []
        async for message in client.iter_messages(entity, limit=limit):
            text = (message.message or "").strip()
            if not text:
                continue
            messages.append(" ".join(text.split()))
        return messages
    finally:
        await client.disconnect()


async def generate_daily_context(
    client: OpenAI,
    signs: dict,
    rss_url: str | None,
    model: str,
    telegram_source: dict | None = None,
) -> dict:
    news_blob = "Немає налаштованого джерела новин."
    if telegram_source and telegram_source.get("channel"):
        messages = await fetch_telegram_messages(
            api_id=telegram_source.get("api_id"),
            api_hash=telegram_source.get("api_hash"),
            channel=telegram_source.get("channel"),
            limit=telegram_source.get("limit", 20),
            session_path=telegram_source.get("session_path", "telethon.session"),
        )
        if messages:
            news_blob = "\n".join([f"- {message}" for message in messages])
            print(
                "[debug] Telegram news context:\n"
                + news_blob
                + "\n[/debug]"
            )
        elif rss_url:
            feed = feedparser.parse(rss_url)
            items = []
            for entry in feed.entries[:10]:
                title = (entry.get("title") or "").strip()
                summary = (entry.get("summary") or "").strip()
                if title or summary:
                    items.append(f"- {title}: {summary}")
            news_blob = "\n".join(items) if items else "Важливих новин немає."
    elif rss_url:
        feed = feedparser.parse(rss_url)
        items = []
        for entry in feed.entries[:10]:
            title = (entry.get("title") or "").strip()
            summary = (entry.get("summary") or "").strip()
            if title or summary:
                items.append(f"- {title}: {summary}")
        news_blob = "\n".join(items) if items else "Важливих новин немає."

    signs_payload = {
        sign: {
            "traits": data.get("traits", []),
            "specificity": data.get("specificity", ""),
        }
        for sign, data in signs.items()
    }

    system_prompt = (
        "Ти редактор астрологічних прогнозів. Стисло підсумуй новини дня у "
        "підбадьорливий «Вайб дня» для кожного знаку зодіаку. Використовуй "
        "надані риси знаків, щоб персоналізувати текст. Кожен вайб 2–3 речення. "
        "Відповідай лише українською."
    )
    user_prompt = (
        "Повідомлення з джерела новин:\n"
        f"{news_blob}\n\n"
        "Конфіг знаків:\n"
        f"{json.dumps(signs_payload, ensure_ascii=False)}\n\n"
        "Поверни JSON з ключами: affirmation (коротке 1 речення), "
        "global_summary (рядок) і vibes (обʼєкт: знак -> текст вайбу). "
        "Лише JSON."
    )

    response = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        response_format={"type": "json_object"},
        temperature=0.6,
    )
    payload = json.loads(response.choices[0].message.content)
    return {
        "affirmation": payload.get("affirmation", ""),
        "global_summary": payload.get("global_summary", ""),
        "vibes": payload.get("vibes", {}),
    }


async def get_or_generate_context(
    client: OpenAI,
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


def build_all_signs_message(context: dict, signs: dict, timezone: ZoneInfo) -> str:
    vibes = context.get("vibes", {})
    global_summary = context.get("global_summary", "")
    affirmation = context.get("affirmation", "")
    lines = [affirmation or "Сьогодні ти на правильному шляху.", ""]
    if global_summary:
        lines.append(global_summary)
        lines.append("")
    for sign in signs.keys():
        vibe = vibes.get(sign, "Вайб формується. Перевір пізніше.")
        lines.append(f"{display_sign(sign)}: {vibe}")
        lines.append("")
    return "\n".join(lines).strip()


async def broadcast_daily_vibes(
    bot: Bot,
    client: OpenAI,
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
    for _, chat_id, sign in get_all_users():
        if not sign:
            await bot.send_message(
                chat_id,
                "Вкажи свій знак зодіаку: /set_sign <sign>, щоб отримувати вайб дня.",
            )
            continue
        vibe = vibes.get(sign, "Вайб формується. Перевір пізніше.")
        message = f"Вайб дня для {display_sign(sign)}:\n{vibe}"
        if global_summary:
            message += f"\n\nГлобальний контекст: {global_summary}"
        await bot.send_message(chat_id, message)

    if channel_id:
        channel_message = build_all_signs_message(context, signs, timezone)
        await bot.send_message(channel_id, channel_message)


def build_personal_prompt(sign: str, sign_data: dict, context: dict, question: str) -> str:
    traits = ", ".join(sign_data.get("traits", []))
    specificity = sign_data.get("specificity", "")
    vibe = context.get("vibes", {}).get(sign, "")
    global_summary = context.get("global_summary", "")
    return (
        f"Знак користувача: {display_sign(sign)}\n"
        f"Риси: {traits}\n"
        f"Специфіка: {specificity}\n"
        f"Вайб дня: {vibe}\n"
        f"Глобальний підсумок: {global_summary}\n\n"
        f"Питання користувача: {question}\n\n"
        "Відповідай як практичний астрологічний коуч у 3–5 реченнях. "
        "Будь конкретним, повʼязуй пораду з вайбом і рисами, уникай категоричних тверджень. "
        "Відповідай лише українською."
    )


async def main() -> None:
    load_dotenv()
    telegram_token = os.getenv("TELEGRAM_BOT_TOKEN")
    openai_key = os.getenv("OPENAI_API_KEY")
    rss_url = os.getenv("RSS_FEED_URL")
    timezone_name = os.getenv("TIMEZONE", "UTC")
    model = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
    channel_id = os.getenv("BROADCAST_CHANNEL")
    telegram_api_id = os.getenv("TELEGRAM_API_ID")
    telegram_api_hash = os.getenv("TELEGRAM_API_HASH")
    telegram_channel = os.getenv("TELEGRAM_NEWS_CHANNEL")
    telegram_limit = int(os.getenv("TELEGRAM_NEWS_LIMIT", "20"))
    telethon_session = os.getenv("TELETHON_SESSION", "telethon.session")

    if not telegram_token or not openai_key:
        raise RuntimeError("Missing TELEGRAM_BOT_TOKEN or OPENAI_API_KEY.")

    timezone = ZoneInfo(timezone_name)

    init_db()
    signs = load_signs()
    client = OpenAI(api_key=openai_key)
    telegram_source = {
        "api_id": int(telegram_api_id) if telegram_api_id else None,
        "api_hash": telegram_api_hash,
        "channel": telegram_channel,
        "limit": telegram_limit,
        "session_path": telethon_session,
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
        await message.answer(f"Вайб дня для {display_sign(sign)}:\n{vibe}")

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
        response = client.chat.completions.create(
            model=model,
            messages=[
                {
                    "role": "system",
                    "content": "Ти лаконічний астрологічний радник. Відповідай лише українською.",
                },
                {"role": "user", "content": prompt},
            ],
            temperature=0.7,
        )
        await message.answer(response.choices[0].message.content.strip())

    scheduler = AsyncIOScheduler(timezone=timezone)
    scheduler.add_job(
        lambda: asyncio.create_task(
            broadcast_daily_vibes(
                bot,
                client,
                signs,
                rss_url,
                model,
                timezone,
                channel_id,
                telegram_source,
            )
        ),
        "cron",
        hour=8,
        minute=0,
    )
    scheduler.start()

    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
