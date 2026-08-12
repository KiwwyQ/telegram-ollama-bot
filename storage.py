"""
Remote database storage (MySQL or PostgreSQL via SQLAlchemy async).

IMPORTANT: this bot does NOT use SQLite. Render's filesystem is ephemeral, so any
local DB file would be lost on every restart/redeploy. All persistent data
(keys, memory, personality, language, model preference, usage) lives in a remote
database accessed through a SQLAlchemy async engine.

Configuration: set DATABASE_URL to a full async SQLAlchemy URL, e.g.
  MySQL:    mysql+aiomysql://user:pass@host:3306/dbname
  Postgres: postgresql+asyncpg://user:pass@host:5432/dbname

The same code works for both MySQL and PostgreSQL; the only dialect-specific bit
is the large-text column type used for chat memory (LONGTEXT vs TEXT).
"""
import json
import os
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from config import Config


class Storage:
    def __init__(self, config: Config):
        self.config = config
        self.engine = None
        self._sessionmaker = None

    async def init(self) -> None:
        url = os.environ.get("DATABASE_URL") or self.config.DATABASE_URL
        if not url:
            raise RuntimeError(
                "DATABASE_URL is required. SQLite is intentionally not used "
                "(Render's filesystem is ephemeral). Set DATABASE_URL to a remote "
                "MySQL or PostgreSQL connection string."
            )
        # pool_pre_ping + pool_recycle keep long-lived connections healthy across
        # Render's connection drops.
        self.engine = create_async_engine(
            url,
            pool_pre_ping=True,
            pool_recycle=280,
            future=True,
            echo=False,
        )
        self._sessionmaker = async_sessionmaker(self.engine, expire_on_commit=False)
        await self._create_tables()

    async def close(self) -> None:
        if self.engine:
            await self.engine.dispose()

    # ----------------------------------------------------------- schema setup
    async def _create_tables(self) -> None:
        dialect = self.engine.dialect.name  # 'mysql' or 'postgresql'
        big_text = "LONGTEXT" if dialect == "mysql" else "TEXT"
        async with self.engine.begin() as conn:
            await conn.execute(text(f"""
                CREATE TABLE IF NOT EXISTS users (
                    user_id    BIGINT PRIMARY KEY,
                    ollama_key VARCHAR(512),
                    model      VARCHAR(128),
                    language   VARCHAR(16),
                    personality TEXT,
                    fmt        VARCHAR(16),
                    created_at VARCHAR(32),
                    updated_at VARCHAR(32)
                )
            """))
            await conn.execute(text(f"""
                CREATE TABLE IF NOT EXISTS groups (
                    chat_id    BIGINT PRIMARY KEY,
                    model      VARCHAR(128),
                    personality TEXT,
                    updated_at VARCHAR(32)
                )
            """))
            await conn.execute(text(f"""
                CREATE TABLE IF NOT EXISTS memory (
                    chat_id  BIGINT PRIMARY KEY,
                    messages {big_text}
                )
            """))
            await conn.execute(text(f"""
                CREATE TABLE IF NOT EXISTS usage (
                    user_id    BIGINT PRIMARY KEY,
                    cnt        INT,
                    day        VARCHAR(32),
                    updated_at VARCHAR(32)
                )
            """))

    # ----------------------------------------------------------------- users
    async def get_user(self, user_id: int) -> Optional[dict]:
        async with self._sessionmaker() as s:
            res = await s.execute(text("SELECT * FROM users WHERE user_id=:uid"), {"uid": user_id})
            row = res.mappings().first()
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
        async with self._sessionmaker() as s:
            if existing:
                await s.execute(
                    text(
                        "UPDATE users SET ollama_key=:ollama_key, model=:model, "
                        "language=:language, personality=:personality, fmt=:fmt, "
                        "updated_at=:updated_at WHERE user_id=:user_id"
                    ),
                    {
                        "user_id": user_id,
                        "ollama_key": merged["ollama_key"],
                        "model": merged["model"],
                        "language": merged["language"],
                        "personality": merged["personality"],
                        "fmt": merged["fmt"],
                        "updated_at": now,
                    },
                )
            else:
                await s.execute(
                    text(
                        "INSERT INTO users (user_id, ollama_key, model, language, "
                        "personality, fmt, created_at, updated_at) "
                        "VALUES (:user_id, :ollama_key, :model, :language, "
                        ":personality, :fmt, :created_at, :updated_at)"
                    ),
                    {
                        "user_id": user_id,
                        "ollama_key": merged["ollama_key"],
                        "model": merged["model"],
                        "language": merged["language"],
                        "personality": merged["personality"],
                        "fmt": merged["fmt"],
                        "created_at": merged["created_at"],
                        "updated_at": now,
                    },
                )
            await s.commit()

    async def delete_key(self, user_id: int) -> None:
        await self.set_user_fields(user_id, ollama_key=None)

    # ----------------------------------------------------------------- groups
    async def get_group(self, chat_id: int) -> Optional[dict]:
        async with self._sessionmaker() as s:
            res = await s.execute(text("SELECT * FROM groups WHERE chat_id=:cid"), {"cid": chat_id})
            row = res.mappings().first()
        return dict(row) if row else None

    async def set_group_fields(self, chat_id: int, **fields) -> None:
        existing = await self.get_group(chat_id) or {}
        merged = {
            "model": existing.get("model"),
            "personality": existing.get("personality"),
        }
        merged.update({k: fields[k] for k in fields if k in merged})
        now = datetime.now(timezone.utc).isoformat()
        async with self._sessionmaker() as s:
            if existing:
                await s.execute(
                    text("UPDATE groups SET model=:model, personality=:personality, updated_at=:updated_at WHERE chat_id=:chat_id"),
                    {"chat_id": chat_id, "model": merged["model"], "personality": merged["personality"], "updated_at": now},
                )
            else:
                await s.execute(
                    text("INSERT INTO groups (chat_id, model, personality, updated_at) VALUES (:chat_id, :model, :personality, :updated_at)"),
                    {"chat_id": chat_id, "model": merged["model"], "personality": merged["personality"], "updated_at": now},
                )
            await s.commit()

    # ----------------------------------------------------------------- memory
    async def get_memory(self, chat_id: int) -> list:
        async with self._sessionmaker() as s:
            res = await s.execute(text("SELECT messages FROM memory WHERE chat_id=:cid"), {"cid": chat_id})
            row = res.mappings().first()
        if not row or not row["messages"]:
            return []
        try:
            data = json.loads(row["messages"])
            return data if isinstance(data, list) else []
        except Exception:
            return []

    async def set_memory(self, chat_id: int, messages: list) -> None:
        payload = json.dumps(messages)
        async with self._sessionmaker() as s:
            existing = await s.execute(
                text("SELECT 1 FROM memory WHERE chat_id=:cid"), {"cid": chat_id}
            )
            if existing.first():
                await s.execute(
                    text("UPDATE memory SET messages=:msgs WHERE chat_id=:cid"),
                    {"cid": chat_id, "msgs": payload},
                )
            else:
                await s.execute(
                    text("INSERT INTO memory (chat_id, messages) VALUES (:cid, :msgs)"),
                    {"cid": chat_id, "msgs": payload},
                )
            await s.commit()

    # ----------------------------------------------------------------- usage
    async def bump_usage(self, user_id: int) -> int:
        """Increment and return today's request count for a user (best effort)."""
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        now = datetime.now(timezone.utc).isoformat()
        async with self._sessionmaker() as s:
            row = (await s.execute(text("SELECT * FROM usage WHERE user_id=:uid"), {"uid": user_id})).mappings().first()
            if row and row["day"] == today:
                count = (row["cnt"] or 0) + 1
                await s.execute(
                    text("UPDATE usage SET cnt=:c, updated_at=:u WHERE user_id=:uid"),
                    {"c": count, "u": now, "uid": user_id},
                )
            elif row:
                count = 1
                await s.execute(
                    text("UPDATE usage SET cnt=:c, day=:d, updated_at=:u WHERE user_id=:uid"),
                    {"c": count, "d": today, "u": now, "uid": user_id},
                )
            else:
                count = 1
                await s.execute(
                    text("INSERT INTO usage (user_id, cnt, day, updated_at) VALUES (:uid, :c, :d, :u)"),
                    {"uid": user_id, "c": count, "d": today, "u": now},
                )
            await s.commit()
        return count
