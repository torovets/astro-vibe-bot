import json
import os
import sqlite3

DB_PATH = os.path.join(os.path.dirname(__file__), "data.db")


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
