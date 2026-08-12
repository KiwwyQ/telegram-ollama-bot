"""
Persistent storage backed by SQLite (via aiosqlite).

Data is stored on disk so it survives process restarts (Render keeps the same
disk for the lifetime of a service instance; for true cross-restart durability
you can mount a Render Disk or back the DB with an external Postgres, but SQLite
is sufficient for free-tier single-instance deployments).

Three tables:
  * users  - per-Telegram-user settings (API key, model, language, personality)
  * groups - per-group settings (model, personality) shared by the whole group
  * memory - per-chat conversation history (JSON blob of message dicts)
"""
import json
import os
from datetime import datetime, timezone
from typing import Optional

import aiosqlite

from config import Config


class Storage:
    def __init__(self, config: Config):
        self.config = config
        self.db_path = config.DB_PATH

    async def init(self) -> None:
        parent = os.path.dirname(self.db_path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS users (
                    user_id    INTEGER PRIMARY KEY,
                    ollama_key TEXT,
                    model      TEXT,
                    language   TEXT,
                    personality TEXT,
                    fmt        TEXT,
                    created_at TEXT,
                    updated_at TEXT
                )
                """
            )
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS groups (
                    chat_id    INTEGER PRIMARY KEY,
                    model      TEXT,
                    personality TEXT,
                    updated_at TEXT
                )
                """
            )
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS memory (
                    chat_id  INTEGER PRIMARY KEY,
                    messages TEXT
                )
                """
            )
            # Per-user lightweight usage counters for friendly limit warnings.
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS usage (
                    user_id    INTEGER PRIMARY KEY,
                    count      INTEGER,
                    day        TEXT,
                    updated_at TEXT
                )
                """
            )
            await db.commit()

    # ----------------------------------------------------------------- users
    async def get_user(self, user_id: int) -> Optional[dict]:
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute("SELECT * FROM users WHERE user_id=?", (user_id,)) as cur:
                row = await cur.fetchone()
        return dict(row) if row else None

    async def set_user_fields(self, user_id: int, **fields) -> None:
        existing = await self.get_user(user_id) or {}
        merged = {
            "ollama_key": existing.get("ollama_key"),
            "model": existing.get("model"),
            "language": existing.get("language", "en"),
            "personality": existing.get("personality"),
            "fmt": existing.get("fmt", "none"),
            "created_at": existing.get("created_at"),
        }
        merged.update({k: fields[k] for k in fields if k in merged})
        now = datetime.now(timezone.utc).isoformat()
        if not merged["created_at"]:
            merged["created_at"] = now
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                INSERT INTO users (user_id, ollama_key, model, language, personality, fmt, created_at, updated_at)
                VALUES (?,?,?,?,?,?,?,?)
                ON CONFLICT(user_id) DO UPDATE SET
                    ollama_key=excluded.ollama_key,
                    model=excluded.model,
                    language=excluded.language,
                    personality=excluded.personality,
                    fmt=excluded.fmt,
                    updated_at=excluded.updated_at
                """,
                (
                    user_id,
                    merged["ollama_key"],
                    merged["model"],
                    merged["language"],
                    merged["personality"],
                    merged["fmt"],
                    merged["created_at"],
                    now,
                ),
            )
            await db.commit()

    async def delete_key(self, user_id: int) -> None:
        await self.set_user_fields(user_id, ollama_key=None)

    # ----------------------------------------------------------------- groups
    async def get_group(self, chat_id: int) -> Optional[dict]:
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute("SELECT * FROM groups WHERE chat_id=?", (chat_id,)) as cur:
                row = await cur.fetchone()
        return dict(row) if row else None

    async def set_group_fields(self, chat_id: int, **fields) -> None:
        existing = await self.get_group(chat_id) or {}
        merged = {
            "model": existing.get("model"),
            "personality": existing.get("personality"),
        }
        merged.update({k: fields[k] for k in fields if k in merged})
        now = datetime.now(timezone.utc).isoformat()
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                INSERT INTO groups (chat_id, model, personality, updated_at)
                VALUES (?,?,?,?)
                ON CONFLICT(chat_id) DO UPDATE SET
                    model=excluded.model,
                    personality=excluded.personality,
                    updated_at=excluded.updated_at
                """,
                (chat_id, merged["model"], merged["personality"], now),
            )
            await db.commit()

    # ----------------------------------------------------------------- memory
    async def get_memory(self, chat_id: int) -> list:
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute("SELECT messages FROM memory WHERE chat_id=?", (chat_id,)) as cur:
                row = await cur.fetchone()
        if not row or not row[0]:
            return []
        try:
            data = json.loads(row[0])
            return data if isinstance(data, list) else []
        except Exception:
            return []

    async def set_memory(self, chat_id: int, messages: list) -> None:
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                INSERT INTO memory (chat_id, messages) VALUES (?,?)
                ON CONFLICT(chat_id) DO UPDATE SET messages=excluded.messages
                """,
                (chat_id, json.dumps(messages)),
            )
            await db.commit()

    # ----------------------------------------------------------------- usage
    async def bump_usage(self, user_id: int) -> int:
        """Increment and return today's request count for a user (best effort)."""
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute("SELECT * FROM usage WHERE user_id=?", (user_id,)) as cur:
                row = await cur.fetchone()
            if row and row["day"] == today:
                count = (row["count"] or 0) + 1
            else:
                count = 1
            await db.execute(
                """
                INSERT INTO usage (user_id, count, day, updated_at) VALUES (?,?,?,?)
                ON CONFLICT(user_id) DO UPDATE SET
                    count=excluded.count, day=excluded.day, updated_at=excluded.updated_at
                """,
                (user_id, count, today, datetime.now(timezone.utc).isoformat()),
            )
            await db.commit()
        return count
