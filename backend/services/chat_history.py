import json
import time
from typing import Optional

from services.db import connect


class ChatHistory:
    def create_chat(self, title: str) -> int:
        now = time.time()
        with connect() as c:
            return c.execute("INSERT INTO chats(title,created_at,updated_at) VALUES(?,?,?)",
                             (title.strip()[:60] or "New chat", now, now)).lastrowid

    def exists(self, chat_id: int) -> bool:
        with connect() as c:
            return c.execute("SELECT 1 FROM chats WHERE id=?", (chat_id,)).fetchone() is not None

    def add_message(self, chat_id: int, role: str, content: str,
                    sources: Optional[list] = None, metadata: Optional[dict] = None) -> int:
        now = time.time()
        with connect() as c:
            mid = c.execute(
                "INSERT INTO messages(chat_id,role,content,sources,metadata,created_at) VALUES(?,?,?,?,?,?)",
                (chat_id, role, content,
                 json.dumps(sources, ensure_ascii=False) if sources is not None else None,
                 json.dumps(metadata, ensure_ascii=False) if metadata is not None else None, now)).lastrowid
            c.execute("UPDATE chats SET updated_at=? WHERE id=?", (now, chat_id))
        return mid

    def list_chats(self, limit: int = 100) -> list[dict]:
        with connect() as c:
            rows = c.execute("SELECT id,title,updated_at,(SELECT COUNT(*) FROM messages m WHERE m.chat_id=chats.id) "
                             "AS message_count FROM chats ORDER BY updated_at DESC LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in rows]

    def get_chat(self, chat_id: int) -> Optional[dict]:
        with connect() as c:
            chat = c.execute("SELECT id,title FROM chats WHERE id=?", (chat_id,)).fetchone()
            if not chat:
                return None
            rows = c.execute(
                "SELECT m.id,m.role,m.content,m.sources,m.metadata,f.rating FROM messages m "
                "LEFT JOIN feedback f ON f.message_id=m.id WHERE m.chat_id=? ORDER BY m.id",
                (chat_id,)).fetchall()
        msgs = [{"id": r["id"], "role": r["role"], "content": r["content"],
                 "sources": json.loads(r["sources"]) if r["sources"] else [],
                 "metadata": json.loads(r["metadata"]) if r["metadata"] else {},
                 "rating": r["rating"]} for r in rows]
        return {"id": chat["id"], "title": chat["title"], "messages": msgs}

    def delete_chat(self, chat_id: int) -> None:
        with connect() as c:
            c.execute("DELETE FROM chats WHERE id=?", (chat_id,))

    def recent_answers(self, limit: int = 30) -> list[dict]:
        """Kaynaklı son cevaplar (Lite Eval için)."""
        with connect() as c:
            rows = c.execute(
                "SELECT m.id, m.content, m.sources, "
                "(SELECT u.content FROM messages u WHERE u.chat_id=m.chat_id AND u.id<m.id "
                " AND u.role='user' ORDER BY u.id DESC LIMIT 1) AS question "
                "FROM messages m WHERE m.role='assistant' AND m.sources IS NOT NULL AND m.sources!='[]' "
                "ORDER BY m.id DESC LIMIT ?", (limit,)).fetchall()
        return [{"message_id": r["id"], "question": r["question"] or "", "answer": r["content"],
                 "sources": json.loads(r["sources"])} for r in rows]
