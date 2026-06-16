import json
import os
import sqlite3

# DB location is configurable so it can live on a persistent disk in production.
# On Render the app directory is ephemeral (wiped each deploy); set DATABASE_PATH
# to a path on the mounted disk (e.g. /var/data/data.db) so forecasts, users and
# rubric rotation survive restarts/deploys. Falls back to a local file for dev.
DB_PATH = os.getenv("DATABASE_PATH") or os.path.join(os.path.dirname(__file__), "data.db")


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
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS rubric_history (
                date TEXT NOT NULL,
                rubric TEXT NOT NULL,
                subject TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS daily_background (
                date TEXT PRIMARY KEY,
                png BLOB NOT NULL
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


# --- Workstream B: recent intros for opener variety ---------------------------

def load_recent_intros(n_days: int = 3) -> list[str]:
    """Return the global_summary (intro) of the most recent cached days.

    Used to pass yesterday's opener into the intro prompt so the model avoids
    repeating it. Newest first. Empty list when there is no history.
    """
    with sqlite3.connect(DB_PATH) as conn:
        try:
            rows = conn.execute(
                "SELECT context_json FROM daily_context ORDER BY date DESC LIMIT ?",
                (n_days,),
            ).fetchall()
        except sqlite3.OperationalError:
            return []
    intros: list[str] = []
    for (raw,) in rows:
        try:
            summary = json.loads(raw).get("global_summary", "")
        except (json.JSONDecodeError, AttributeError):
            summary = ""
        if summary:
            intros.append(summary)
    return intros


def load_recent_vibes(n_days: int = 2) -> dict[str, list[str]]:
    """Return recent days' vibe text per English sign key, newest first.

    Used to feed each sign's recent forecast back into generation so the same
    sign doesn't repeat its theme/opener day over day. Empty when no history.
    """
    with sqlite3.connect(DB_PATH) as conn:
        try:
            rows = conn.execute(
                "SELECT context_json FROM daily_context ORDER BY date DESC LIMIT ?",
                (n_days,),
            ).fetchall()
        except sqlite3.OperationalError:
            return {}
    out: dict[str, list[str]] = {}
    for (raw,) in rows:
        try:
            vibes = json.loads(raw).get("vibes", {}) or {}
        except (json.JSONDecodeError, AttributeError):
            continue
        for sign, text in vibes.items():
            if text:
                out.setdefault(sign, []).append(text)
    return out


# --- Workstream C: rubric rotation -------------------------------------------

def record_rubric(rubric: str, subject: str, date_key: str | None = None) -> None:
    from datetime import date as _date

    date_key = date_key or _date.today().isoformat()
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            "INSERT INTO rubric_history (date, rubric, subject) VALUES (?, ?, ?)",
            (date_key, rubric, subject),
        )


def get_used_subjects(rubric: str) -> list[str]:
    with sqlite3.connect(DB_PATH) as conn:
        rows = conn.execute(
            "SELECT subject FROM rubric_history WHERE rubric = ? ORDER BY rowid",
            (rubric,),
        ).fetchall()
    return [row[0] for row in rows]


def clear_rubric(rubric: str) -> None:
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("DELETE FROM rubric_history WHERE rubric = ?", (rubric,))


def next_subject(rubric: str, candidates: list[str]) -> str | None:
    """Pick the first candidate not yet used for this rubric.

    When every candidate has been used, the cycle restarts: history for the
    rubric is cleared and the first candidate is returned.
    """
    if not candidates:
        return None
    used = set(get_used_subjects(rubric))
    for candidate in candidates:
        if candidate not in used:
            return candidate
    # Full cycle complete -> restart rotation.
    clear_rubric(rubric)
    return candidates[0]


# --- Workstream D: daily AI background cache (shared by cover + sign cards) ---

def load_today_background(today_key: str) -> bytes | None:
    with sqlite3.connect(DB_PATH) as conn:
        row = conn.execute(
            "SELECT png FROM daily_background WHERE date = ?",
            (today_key,),
        ).fetchone()
    return row[0] if row else None


def save_today_background(today_key: str, png: bytes) -> None:
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            """
            INSERT INTO daily_background (date, png)
            VALUES (?, ?)
            ON CONFLICT(date) DO UPDATE SET png = excluded.png
            """,
            (today_key, png),
        )
