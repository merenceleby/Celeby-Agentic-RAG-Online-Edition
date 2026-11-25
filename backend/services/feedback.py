import time
from typing import Optional

from services.db import connect


class Feedback:
    def submit(self, message_id: int, rating: int, comment: Optional[str] = None) -> None:
        with connect() as c:
            if not c.execute("SELECT 1 FROM messages WHERE id=? AND role='assistant'",
                             (message_id,)).fetchone():
                raise ValueError("Message not found")
            c.execute(
                "INSERT INTO feedback(message_id,rating,comment,created_at) VALUES(?,?,?,?) "
                "ON CONFLICT(message_id) DO UPDATE SET rating=excluded.rating, "
                "comment=excluded.comment, created_at=excluded.created_at",
                (message_id, rating, (comment or "").strip()[:1000] or None, time.time()))

    def summary(self) -> dict:
        with connect() as c:
            r = c.execute("SELECT COALESCE(SUM(rating=1),0) up, COALESCE(SUM(rating=-1),0) down "
                          "FROM feedback").fetchone()
        return {"helpful": r["up"], "not_helpful": r["down"]}

    def recent(self, limit: int = 50) -> list[dict]:
        with connect() as c:
            rows = c.execute(
                "SELECT f.message_id, f.rating, f.comment, f.created_at, m.content AS answer, "
                "(SELECT u.content FROM messages u WHERE u.chat_id=m.chat_id AND u.id<m.id "
                " AND u.role='user' ORDER BY u.id DESC LIMIT 1) AS question "
                "FROM feedback f JOIN messages m ON m.id=f.message_id "
                "ORDER BY f.created_at DESC LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in rows]

    def reset(self) -> None:
        with connect() as c:
            c.execute("DELETE FROM feedback")
