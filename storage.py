"""Небольшое локальное хранилище пользовательских планов.

В MVP сохраняются только технические идентификаторы MAX и ответы на
кнопочные вопросы. Паспорт, СНИЛС, банковские и медицинские данные бот
не запрашивает и не хранит.
"""

from __future__ import annotations

import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from collections.abc import Iterator
from typing import Any


DEFAULT_DB_PATH = Path(__file__).resolve().parent / "data" / "opora.sqlite3"


class Storage:
    """SQLite-хранилище, не требующее отдельного сервиса или контейнера."""

    def __init__(self, path: str | Path | None = None) -> None:
        configured_path = os.getenv("MAX_BOT_DB_PATH")
        self.path = Path(path or configured_path or DEFAULT_DB_PATH)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        return connection

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        connection = self._connect()
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _initialize(self) -> None:
        with self._connection() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS profiles (
                    user_id INTEGER PRIMARY KEY,
                    chat_id INTEGER NOT NULL,
                    answers_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS plans (
                    user_id INTEGER NOT NULL,
                    chat_id INTEGER NOT NULL,
                    benefit_id TEXT NOT NULL,
                    title TEXT NOT NULL,
                    category TEXT NOT NULL,
                    region TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'new',
                    created_at TEXT NOT NULL,
                    PRIMARY KEY (user_id, benefit_id)
                );

                CREATE TABLE IF NOT EXISTS reminders (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    chat_id INTEGER NOT NULL,
                    benefit_id TEXT NOT NULL,
                    reminder_at INTEGER NOT NULL,
                    reminder_sent INTEGER NOT NULL DEFAULT 0,
                    UNIQUE (user_id, benefit_id)
                );
                """
            )

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    def save_profile(
        self,
        *,
        user_id: int,
        chat_id: int,
        answers: dict[str, str],
    ) -> None:
        with self._connection() as connection:
            connection.execute(
                """
                INSERT INTO profiles (user_id, chat_id, answers_json, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                    chat_id = excluded.chat_id,
                    answers_json = excluded.answers_json,
                    updated_at = excluded.updated_at
                """,
                (
                    user_id,
                    chat_id,
                    json.dumps(answers, ensure_ascii=False),
                    self._now(),
                ),
            )

    def save_plan(
        self,
        *,
        user_id: int,
        chat_id: int,
        benefit_id: str,
        title: str,
        category: str,
        region: str,
    ) -> None:
        with self._connection() as connection:
            connection.execute(
                """
                INSERT INTO plans (
                    user_id, chat_id, benefit_id, title, category, region,
                    status, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, 'new', ?)
                ON CONFLICT(user_id, benefit_id) DO UPDATE SET
                    chat_id = excluded.chat_id,
                    title = excluded.title,
                    category = excluded.category,
                    region = excluded.region
                """,
                (
                    user_id,
                    chat_id,
                    benefit_id,
                    title,
                    category,
                    region,
                    self._now(),
                ),
            )

    def list_plan(self, user_id: int) -> list[dict[str, Any]]:
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT user_id, chat_id, benefit_id, title, category, region,
                       status, created_at
                FROM plans
                WHERE user_id = ?
                ORDER BY status = 'done', created_at DESC
                """,
                (user_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def set_status(self, *, user_id: int, benefit_id: str, status: str) -> None:
        with self._connection() as connection:
            connection.execute(
                """
                UPDATE plans
                SET status = ?
                WHERE user_id = ? AND benefit_id = ?
                """,
                (status, user_id, benefit_id),
            )

    def set_reminder(
        self,
        *,
        user_id: int,
        chat_id: int,
        benefit_id: str,
        reminder_at: int,
    ) -> None:
        with self._connection() as connection:
            connection.execute(
                """
                INSERT INTO reminders (
                    user_id, chat_id, benefit_id, reminder_at, reminder_sent
                )
                VALUES (?, ?, ?, ?, 0)
                ON CONFLICT(user_id, benefit_id) DO UPDATE SET
                    chat_id = excluded.chat_id,
                    reminder_at = excluded.reminder_at,
                    reminder_sent = 0
                """,
                (user_id, chat_id, benefit_id, reminder_at),
            )

    def due_reminders(self, now: int) -> list[dict[str, Any]]:
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT id, user_id, chat_id, benefit_id, reminder_at
                FROM reminders
                WHERE reminder_at <= ? AND reminder_sent = 0
                ORDER BY reminder_at
                """,
                (now,),
            ).fetchall()
        return [dict(row) for row in rows]

    def mark_reminder_sent(self, reminder_id: int) -> None:
        with self._connection() as connection:
            connection.execute(
                "UPDATE reminders SET reminder_sent = 1 WHERE id = ?",
                (reminder_id,),
            )
