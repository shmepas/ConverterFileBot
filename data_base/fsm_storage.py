"""SQLite-backed aiogram FSM storage for single-instance deployments."""
import asyncio
import json
import os
import time
from collections.abc import Mapping
from contextlib import asynccontextmanager
from typing import Any

import aiosqlite
from aiogram.fsm.storage.base import BaseEventIsolation, BaseStorage, StateType, StorageKey

from data_base import db


class SQLiteFSMStorage(BaseStorage):
    """Persist bot conversation state and data across process restarts.

    State is scoped by all fields in aiogram's StorageKey, including bot ID,
    chat, user, topic, business connection, and FSM destiny. Sessions expire
    after ``ttl_seconds`` without activity.
    """

    def __init__(self, path: str | os.PathLike[str] | None = None,
                 ttl_seconds: int = 24 * 60 * 60) -> None:
        self.path = os.fspath(path) if path is not None else None
        self.ttl_seconds = max(60, int(ttl_seconds))
        self._initialized = False
        self._init_lock = asyncio.Lock()

    def _db_path(self) -> str:
        return self.path or db.DB_PATH

    @staticmethod
    def _storage_key(key: StorageKey) -> str:
        return json.dumps(
            [
                key.bot_id,
                key.chat_id,
                key.user_id,
                key.thread_id,
                key.business_connection_id,
                key.destiny,
            ],
            ensure_ascii=True,
            separators=(",", ":"),
        )

    async def _ensure_schema(self) -> None:
        if self._initialized:
            return
        async with self._init_lock:
            if self._initialized:
                return
            cutoff = time.time() - self.ttl_seconds
            async with aiosqlite.connect(self._db_path(), timeout=10) as connection:
                await connection.execute(
                    "CREATE TABLE IF NOT EXISTS fsm_sessions ("
                    "storage_key TEXT PRIMARY KEY, state TEXT, data_json TEXT NOT NULL "
                    "DEFAULT '{}', updated_at REAL NOT NULL)"
                )
                await connection.execute(
                    "CREATE INDEX IF NOT EXISTS idx_fsm_sessions_updated "
                    "ON fsm_sessions(updated_at)"
                )
                await connection.execute(
                    "DELETE FROM fsm_sessions WHERE updated_at < ?", (cutoff,)
                )
                await connection.commit()
            self._initialized = True

    async def _get_row(self, key: StorageKey) -> tuple[str | None, str] | None:
        await self._ensure_schema()
        storage_key = self._storage_key(key)
        now = time.time()
        cutoff = now - self.ttl_seconds
        async with aiosqlite.connect(self._db_path(), timeout=10) as connection:
            connection.row_factory = aiosqlite.Row
            async with connection.execute(
                "SELECT state, data_json FROM fsm_sessions "
                "WHERE storage_key = ? AND updated_at >= ?",
                (storage_key, cutoff),
            ) as cursor:
                row = await cursor.fetchone()
            if row is None:
                await connection.execute(
                    "DELETE FROM fsm_sessions WHERE storage_key = ?", (storage_key,)
                )
                await connection.commit()
                return None
            await connection.execute(
                "UPDATE fsm_sessions SET updated_at = ? WHERE storage_key = ?",
                (now, storage_key),
            )
            await connection.commit()
            return row["state"], row["data_json"]

    async def set_state(self, key: StorageKey, state: StateType = None) -> None:
        await self._ensure_schema()
        state_value = getattr(state, "state", state)
        if state_value is not None and not isinstance(state_value, str):
            raise TypeError("FSM state must be a string, State object, or None")
        now = time.time()
        cutoff = now - self.ttl_seconds
        async with aiosqlite.connect(self._db_path(), timeout=10) as connection:
            await connection.execute(
                "INSERT INTO fsm_sessions (storage_key, state, data_json, updated_at) "
                "VALUES (?, ?, '{}', ?) ON CONFLICT(storage_key) DO UPDATE SET "
                "state = excluded.state, "
                "data_json = CASE WHEN fsm_sessions.updated_at >= ? "
                "THEN fsm_sessions.data_json ELSE '{}' END, "
                "updated_at = excluded.updated_at",
                (self._storage_key(key), state_value, now, cutoff),
            )
            await connection.commit()

    async def get_state(self, key: StorageKey) -> str | None:
        row = await self._get_row(key)
        return row[0] if row else None

    async def set_data(self, key: StorageKey, data: Mapping[str, Any]) -> None:
        await self._ensure_schema()
        if not isinstance(data, Mapping):
            raise TypeError("FSM data must be a mapping")
        serialized = json.dumps(data, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
        now = time.time()
        cutoff = now - self.ttl_seconds
        async with aiosqlite.connect(self._db_path(), timeout=10) as connection:
            await connection.execute(
                "INSERT INTO fsm_sessions (storage_key, state, data_json, updated_at) "
                "VALUES (?, NULL, ?, ?) ON CONFLICT(storage_key) DO UPDATE SET "
                "state = CASE WHEN fsm_sessions.updated_at >= ? "
                "THEN fsm_sessions.state ELSE NULL END, "
                "data_json = excluded.data_json, updated_at = excluded.updated_at",
                (self._storage_key(key), serialized, now, cutoff),
            )
            await connection.commit()

    async def get_data(self, key: StorageKey) -> dict[str, Any]:
        row = await self._get_row(key)
        if row is None:
            return {}
        try:
            data = json.loads(row[1])
        except (TypeError, json.JSONDecodeError):
            data = {}
        return data if isinstance(data, dict) else {}

    async def close(self) -> None:
        """No persistent connection is held between operations."""
        self._initialized = False


class KeyedEventIsolation(BaseEventIsolation):
    """Serialize updates per FSM key without retaining locks for departed users."""

    def __init__(self) -> None:
        self._guard = asyncio.Lock()
        self._locks: dict[StorageKey, tuple[asyncio.Lock, int]] = {}

    @asynccontextmanager
    async def lock(self, key: StorageKey):
        async with self._guard:
            lock, users = self._locks.get(key, (asyncio.Lock(), 0))
            self._locks[key] = (lock, users + 1)

        acquired = False
        try:
            await lock.acquire()
            acquired = True
            yield
        finally:
            if acquired:
                lock.release()
            async with self._guard:
                current_lock, users = self._locks[key]
                if users == 1:
                    del self._locks[key]
                else:
                    self._locks[key] = (current_lock, users - 1)

    async def close(self) -> None:
        async with self._guard:
            self._locks.clear()
