import json
import os
import sqlite3
import uuid
from typing import Any, Dict, List, Optional


class ChatStore:
    """Lightweight SQLite-backed chat persistence."""

    def __init__(self, db_path: str = "api/data/chat.db") -> None:
        self.db_path = db_path
        self._ensure_db()

    def _get_conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, detect_types=sqlite3.PARSE_DECLTYPES)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON;")
        conn.execute("PRAGMA journal_mode = WAL;")
        return conn

    def _ensure_db(self) -> None:
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        with self._get_conn() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS conversations (
                    id TEXT PRIMARY KEY,
                    title TEXT NULL,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
                );
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    conversation_id TEXT NOT NULL,
                    role TEXT NOT NULL CHECK(role IN ('user', 'assistant')),
                    content TEXT NOT NULL,
                    metadata TEXT NULL,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY(conversation_id) REFERENCES conversations(id) ON DELETE CASCADE
                );
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_messages_conversation_created
                ON messages(conversation_id, created_at, id);
                """
            )

    def _row_to_message(self, row: sqlite3.Row) -> Dict[str, Any]:
        return {
            "id": row["id"],
            "conversation_id": row["conversation_id"],
            "role": row["role"],
            "content": row["content"],
            "metadata": json.loads(row["metadata"]) if row["metadata"] else None,
            "created_at": row["created_at"],
        }

    def conversation_exists(self, conversation_id: str) -> bool:
        with self._get_conn() as conn:
            cur = conn.execute(
                "SELECT 1 FROM conversations WHERE id = ? LIMIT 1", (conversation_id,)
            )
            return cur.fetchone() is not None

    def create_conversation(self, title: Optional[str] = None) -> Dict[str, Any]:
        conversation_id = str(uuid.uuid4())
        with self._get_conn() as conn:
            conn.execute(
                "INSERT INTO conversations (id, title) VALUES (?, ?)",
                (conversation_id, title),
            )
        return self.get_conversation(conversation_id)

    def get_conversation(self, conversation_id: str) -> Optional[Dict[str, Any]]:
        with self._get_conn() as conn:
            cur = conn.execute(
                """
                SELECT id, title, created_at, updated_at
                FROM conversations
                WHERE id = ?
                """,
                (conversation_id,),
            )
            row = cur.fetchone()
            if row is None:
                return None
            return {
                "id": row["id"],
                "title": row["title"],
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
            }

    def delete_conversation(self, conversation_id: str) -> None:
        with self._get_conn() as conn:
            conn.execute("DELETE FROM conversations WHERE id = ?", (conversation_id,))

    def append_message(
        self,
        conversation_id: str,
        role: str,
        content: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> int:
        metadata_json = json.dumps(metadata) if metadata else None
        with self._get_conn() as conn:
            cur = conn.execute(
                """
                INSERT INTO messages (conversation_id, role, content, metadata)
                VALUES (?, ?, ?, ?)
                """,
                (conversation_id, role, content, metadata_json),
            )
            conn.execute(
                "UPDATE conversations SET updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (conversation_id,),
            )
            return cur.lastrowid

    def list_conversations(self, limit: int = 20, offset: int = 0) -> List[Dict[str, Any]]:
        with self._get_conn() as conn:
            cur = conn.execute(
                """
                SELECT
                    c.id,
                    c.title,
                    c.created_at,
                    c.updated_at,
                    (
                        SELECT content
                        FROM messages m
                        WHERE m.conversation_id = c.id
                        ORDER BY m.created_at DESC, m.id DESC
                        LIMIT 1
                    ) AS last_message
                FROM conversations c
                ORDER BY c.updated_at DESC, c.created_at DESC
                LIMIT ? OFFSET ?;
                """,
                (limit, offset),
            )
            return [
                {
                    "id": row["id"],
                    "title": row["title"],
                    "created_at": row["created_at"],
                    "updated_at": row["updated_at"],
                    "last_message": row["last_message"],
                }
                for row in cur.fetchall()
            ]

    def get_messages(
        self, conversation_id: str, limit: int = 200, offset: int = 0
    ) -> List[Dict[str, Any]]:
        with self._get_conn() as conn:
            cur = conn.execute(
                """
                SELECT id, conversation_id, role, content, metadata, created_at
                FROM messages
                WHERE conversation_id = ?
                ORDER BY created_at ASC, id ASC
                LIMIT ? OFFSET ?;
                """,
                (conversation_id, limit, offset),
            )
            return [self._row_to_message(row) for row in cur.fetchall()]
