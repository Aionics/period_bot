"""
Хранилище: одна строка на девушку, все данные — одним зашифрованным блоком.

В открытом виде лежит только номер чата Telegram (без него бот не сможет написать).
Имени, ника и фото бот не сохраняет вообще. Даты, отметки и настройки зашифрованы
ключом CRYPT_KEY из .env (Fernet: AES-128 + проверка целостности).
"""

import json
import os
import sqlite3
from contextlib import closing

from cryptography.fernet import Fernet, InvalidToken


class Store:
    def __init__(self, path: str, key: str):
        if not key:
            raise RuntimeError("Не задан CRYPT_KEY в .env")
        self.fernet = Fernet(key.encode())
        self.path = path
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        with closing(self._conn()) as conn, conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute(
                "CREATE TABLE IF NOT EXISTS users (chat_id INTEGER PRIMARY KEY, blob BLOB NOT NULL)"
            )

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=10)
        conn.execute("PRAGMA synchronous=FULL")
        # Удалённые данные затираются нулями, а не остаются в файле базы
        conn.execute("PRAGMA secure_delete=ON")
        return conn

    def get(self, chat_id: int) -> dict | None:
        with closing(self._conn()) as conn, conn:
            row = conn.execute("SELECT blob FROM users WHERE chat_id=?", (chat_id,)).fetchone()
        if not row:
            return None
        try:
            return json.loads(self.fernet.decrypt(row[0]))
        except InvalidToken:
            raise RuntimeError("Не получается расшифровать данные — CRYPT_KEY в .env поменялся?")

    def put(self, chat_id: int, data: dict) -> None:
        blob = self.fernet.encrypt(json.dumps(data, ensure_ascii=False).encode())
        with closing(self._conn()) as conn, conn:
            conn.execute(
                "INSERT INTO users (chat_id, blob) VALUES (?, ?) "
                "ON CONFLICT(chat_id) DO UPDATE SET blob=excluded.blob",
                (chat_id, blob),
            )

    def delete(self, chat_id: int) -> None:
        conn = self._conn()
        try:
            with conn:
                conn.execute("DELETE FROM users WHERE chat_id=?", (chat_id,))
            # Переносим изменения из журнала в основной файл, чтобы старых копий не осталось
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        finally:
            conn.close()

    def chat_ids(self) -> list[int]:
        with closing(self._conn()) as conn, conn:
            return [r[0] for r in conn.execute("SELECT chat_id FROM users")]

    def count(self) -> int:
        with closing(self._conn()) as conn, conn:
            return conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
