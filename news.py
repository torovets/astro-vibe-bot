import logging
from urllib.parse import urlparse

import feedparser

logger = logging.getLogger(__name__)


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
    session_string: str | None = None,
) -> list[str]:
    if not api_id or not api_hash or not channel:
        return []
    try:
        from telethon import TelegramClient
        from telethon.sessions import StringSession
        from telethon.tl.functions.messages import ImportChatInviteRequest
    except Exception:
        return []

    if session_string:
        client = TelegramClient(StringSession(session_string), api_id, api_hash)
    else:
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


def _rss_blob(rss_url: str | None) -> str:
    if not rss_url:
        return "Немає налаштованого джерела новин."
    feed = feedparser.parse(rss_url)
    items = []
    for entry in feed.entries[:10]:
        title = (entry.get("title") or "").strip()
        summary = (entry.get("summary") or "").strip()
        if title or summary:
            items.append(f"- {title}: {summary}")
    return "\n".join(items) if items else "Важливих новин немає."


async def fetch_news_blob(
    rss_url: str | None,
    telegram_source: dict | None = None,
) -> str:
    """Fetch a news blob, preferring Telegram (Telethon) with RSS fallback.

    Returns a newline-joined bullet list of news items, or a sensible
    placeholder string when no source is configured / produced content.
    """
    if telegram_source and telegram_source.get("channel"):
        messages = await fetch_telegram_messages(
            api_id=telegram_source.get("api_id"),
            api_hash=telegram_source.get("api_hash"),
            channel=telegram_source.get("channel"),
            limit=telegram_source.get("limit", 20),
            session_path=telegram_source.get("session_path", "telethon.session"),
            session_string=telegram_source.get("session_string"),
        )
        if messages:
            news_blob = "\n".join([f"- {message}" for message in messages])
            logger.info("Fetched %d Telegram news messages", len(messages))
            return news_blob
        logger.info("No Telegram news messages; falling back to RSS")
        blob = _rss_blob(rss_url)
        logger.info("RSS fallback produced %d chars", len(blob))
        return blob

    blob = _rss_blob(rss_url)
    logger.info("Fetched news from RSS (%d chars)", len(blob))
    return blob
