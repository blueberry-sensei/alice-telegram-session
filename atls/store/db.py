"""
Store — SQLite WAL, đồng bộ, bọc bằng khoá luồng.

Vì sao đồng bộ trong một runtime async: mọi truy vấn ở đây là microsecond trên file
local. Kéo một driver async vào chỉ để tránh chặn 200µs là đổi một dependency thật
lấy một lợi ích tưởng tượng. Chỗ nào chặn lâu thật (gọi CLI, gọi HTTP) mới cần async.

WAL cho phép nhiều reader song song với một writer — đúng hình dạng tải của ATLS.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path

SCHEMA = Path(__file__).with_name("schema.sql")


@dataclass(frozen=True)
class StoredMessage:
    id: int
    chat_id: str
    ts: float
    role: str
    sender_name: str
    text: str
    addressed: bool
    tokens: int
    media: dict | None = None

    def as_line(self) -> str:
        """Một dòng để dán vào prompt. Vai trò nằm ở tên, không ở nhãn kỹ thuật —
        model đọc `[Bệ hạ]: ...` tự nhiên hơn `role=human name=...`."""
        who = "Alice" if self.role == "agent" else (self.sender_name or "người lạ")
        body = self.text
        if self.media:
            kind = self.media.get("kind", "file")
            name = self.media.get("name") or self.media.get("path") or ""
            body = f"{body} [gửi kèm {kind}: {name}]".strip()
        return f"[{who}]: {body}"


@dataclass(frozen=True)
class Summary:
    id: int
    chat_id: str
    from_msg_id: int
    to_msg_id: int
    covered: int
    text: str
    tokens: int


@dataclass(frozen=True)
class SessionRow:
    id: int
    chat_id: str
    agent: str
    agent_session_id: str
    started: bool
    created_at: float
    last_used_at: float
    turns: int


class Store:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._path = path
        # `check_same_thread=False` + một khoá tự quản: dispatcher chạy worker trên
        # thread pool, và mỗi worker sẽ đụng store. Một connection dùng chung rẻ hơn
        # và tránh được "database is locked" do nhiều connection tranh writer.
        self._conn = sqlite3.connect(str(path), check_same_thread=False, timeout=30)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        self._init_schema()

    def _init_schema(self) -> None:
        with self._lock:
            self._conn.executescript(SCHEMA.read_text(encoding="utf-8"))
            self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # ── chats ────────────────────────────────────────────────────────────────

    def upsert_chat(self, chat_id: str, kind: str, title: str) -> None:
        now = time.time()
        with self._lock:
            self._conn.execute(
                """INSERT INTO chats (chat_id, kind, title, first_seen, last_seen)
                   VALUES (?, ?, ?, ?, ?)
                   ON CONFLICT(chat_id) DO UPDATE SET
                       kind = excluded.kind,
                       title = CASE WHEN excluded.title != '' THEN excluded.title
                                    ELSE chats.title END,
                       last_seen = excluded.last_seen""",
                (chat_id, kind, title, now, now),
            )
            self._conn.commit()

    def chat_config(self, chat_id: str) -> dict:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM chats WHERE chat_id = ?", (chat_id,)
            ).fetchone()
        return dict(row) if row else {}

    def set_chat_enabled(self, chat_id: str, enabled: bool) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE chats SET enabled = ? WHERE chat_id = ?",
                (1 if enabled else 0, chat_id),
            )
            self._conn.commit()

    def list_chats(self) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM chats ORDER BY last_seen DESC"
            ).fetchall()
        return [dict(r) for r in rows]

    # ── messages ─────────────────────────────────────────────────────────────

    def add_message(
        self,
        *,
        chat_id: str,
        role: str,
        text: str,
        tokens: int,
        update_id: int | None = None,
        tg_message_id: int | None = None,
        sender_id: str = "",
        sender_name: str = "",
        reply_to: int | None = None,
        addressed: bool = False,
        media: dict | None = None,
        ts: float | None = None,
    ) -> int | None:
        """Ghi một tin. Trả `None` khi `update_id` đã tồn tại (Telegram gửi trùng).

        Người gọi PHẢI coi `None` là "đã xử lý rồi, bỏ qua" — đây là toàn bộ cơ chế
        chống xử lý hai lần của hệ thống.
        """
        with self._lock:
            cur = self._conn.execute(
                """INSERT OR IGNORE INTO messages
                   (update_id, chat_id, tg_message_id, ts, role, sender_id,
                    sender_name, text, reply_to, addressed, media, tokens)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    update_id, chat_id, tg_message_id, ts or time.time(), role,
                    sender_id, sender_name, text, reply_to, 1 if addressed else 0,
                    json.dumps(media, ensure_ascii=False) if media else None, tokens,
                ),
            )
            self._conn.commit()
            return cur.lastrowid if cur.rowcount else None

    def _row_to_message(self, row: sqlite3.Row) -> StoredMessage:
        return StoredMessage(
            id=row["id"],
            chat_id=row["chat_id"],
            ts=row["ts"],
            role=row["role"],
            sender_name=row["sender_name"],
            text=row["text"],
            addressed=bool(row["addressed"]),
            tokens=row["tokens"],
            media=json.loads(row["media"]) if row["media"] else None,
        )

    def messages_after(self, chat_id: str, after_id: int, limit: int = 500) -> list[StoredMessage]:
        """Tin có `id > after_id`, thứ tự CŨ → MỚI."""
        with self._lock:
            rows = self._conn.execute(
                """SELECT * FROM messages WHERE chat_id = ? AND id > ?
                   ORDER BY id ASC LIMIT ?""",
                (chat_id, after_id, limit),
            ).fetchall()
        return [self._row_to_message(r) for r in rows]

    def recent_messages(self, chat_id: str, limit: int) -> list[StoredMessage]:
        """`limit` tin gần nhất, trả về theo thứ tự CŨ → MỚI."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM messages WHERE chat_id = ? ORDER BY id DESC LIMIT ?",
                (chat_id, limit),
            ).fetchall()
        return [self._row_to_message(r) for r in reversed(rows)]

    def search(self, query: str, *, chat_id: str | None = None, limit: int = 20) -> list[StoredMessage]:
        """Tìm toàn văn trong archive — đường trả lời "tuần trước ông này nói gì".

        FTS5 nhận cú pháp riêng (`AND`, `"..."`, `*`); chuỗi người dùng gõ có thể chứa
        ký tự làm vỡ parser. Bọc mỗi từ trong nháy kép rồi nối lại là cách rẻ nhất để
        vừa an toàn vừa giữ nghĩa "tìm mọi từ này".
        """
        terms = [t for t in query.replace('"', " ").split() if t]
        if not terms:
            return []
        match = " ".join(f'"{t}"' for t in terms)
        sql = """SELECT m.* FROM messages_fts f JOIN messages m ON m.id = f.rowid
                 WHERE messages_fts MATCH ?"""
        params: list = [match]
        if chat_id:
            sql += " AND m.chat_id = ?"
            params.append(chat_id)
        sql += " ORDER BY m.id DESC LIMIT ?"
        params.append(limit)
        with self._lock:
            rows = self._conn.execute(sql, params).fetchall()
        return [self._row_to_message(r) for r in rows]

    def count_messages(self, chat_id: str) -> int:
        with self._lock:
            row = self._conn.execute(
                "SELECT COUNT(*) c FROM messages WHERE chat_id = ?", (chat_id,)
            ).fetchone()
        return int(row["c"])

    # ── summaries ────────────────────────────────────────────────────────────

    def latest_summary(self, chat_id: str) -> Summary | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM summaries WHERE chat_id = ? ORDER BY to_msg_id DESC LIMIT 1",
                (chat_id,),
            ).fetchone()
        if not row:
            return None
        return Summary(
            id=row["id"], chat_id=row["chat_id"], from_msg_id=row["from_msg_id"],
            to_msg_id=row["to_msg_id"], covered=row["covered"], text=row["text"],
            tokens=row["tokens"],
        )

    def add_summary(
        self, *, chat_id: str, from_msg_id: int, to_msg_id: int,
        covered: int, text: str, tokens: int,
    ) -> int:
        with self._lock:
            cur = self._conn.execute(
                """INSERT INTO summaries
                   (chat_id, from_msg_id, to_msg_id, covered, text, tokens, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (chat_id, from_msg_id, to_msg_id, covered, text, tokens, time.time()),
            )
            self._conn.commit()
            return int(cur.lastrowid)

    # ── sessions ─────────────────────────────────────────────────────────────

    def open_session(self, chat_id: str) -> SessionRow | None:
        with self._lock:
            row = self._conn.execute(
                """SELECT * FROM sessions WHERE chat_id = ? AND closed_at IS NULL
                   ORDER BY id DESC LIMIT 1""",
                (chat_id,),
            ).fetchone()
        if not row:
            return None
        return SessionRow(
            id=row["id"], chat_id=row["chat_id"], agent=row["agent"],
            agent_session_id=row["agent_session_id"], started=bool(row["started"]),
            created_at=row["created_at"], last_used_at=row["last_used_at"],
            turns=row["turns"],
        )

    def create_session(self, chat_id: str, agent: str, agent_session_id: str) -> SessionRow:
        now = time.time()
        with self._lock:
            cur = self._conn.execute(
                """INSERT INTO sessions
                   (chat_id, agent, agent_session_id, started, created_at, last_used_at)
                   VALUES (?, ?, ?, 0, ?, ?)""",
                (chat_id, agent, agent_session_id, now, now),
            )
            self._conn.commit()
        return SessionRow(
            id=int(cur.lastrowid), chat_id=chat_id, agent=agent,
            agent_session_id=agent_session_id, started=False,
            created_at=now, last_used_at=now, turns=0,
        )

    def touch_session(self, session_id: int, *, started: bool = True) -> None:
        with self._lock:
            self._conn.execute(
                """UPDATE sessions
                   SET last_used_at = ?, turns = turns + 1, started = ?
                   WHERE id = ?""",
                (time.time(), 1 if started else 0, session_id),
            )
            self._conn.commit()

    def close_session(self, session_id: int, reason: str) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE sessions SET closed_at = ?, close_reason = ? WHERE id = ?",
                (time.time(), reason, session_id),
            )
            self._conn.commit()

    # ── gates ────────────────────────────────────────────────────────────────

    def open_gate(self, chat_id: str, kind: str, what: str) -> int:
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO pending_gates (chat_id, kind, what, created_at) VALUES (?, ?, ?, ?)",
                (chat_id, kind, what, time.time()),
            )
            self._conn.commit()
            return int(cur.lastrowid)

    def pending_gates(self, chat_id: str) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                """SELECT * FROM pending_gates WHERE chat_id = ? AND resolved_at IS NULL
                   ORDER BY id ASC""",
                (chat_id,),
            ).fetchall()
        return [dict(r) for r in rows]

    def resolve_gates(self, chat_id: str, by: str) -> int:
        with self._lock:
            cur = self._conn.execute(
                """UPDATE pending_gates SET resolved_at = ?, resolved_by = ?
                   WHERE chat_id = ? AND resolved_at IS NULL""",
                (time.time(), by, chat_id),
            )
            self._conn.commit()
            return cur.rowcount

    # ── kv ───────────────────────────────────────────────────────────────────

    def get_kv(self, key: str, default: str = "") -> str:
        with self._lock:
            row = self._conn.execute("SELECT value FROM kv WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else default

    def set_kv(self, key: str, value: str) -> None:
        with self._lock:
            self._conn.execute(
                """INSERT INTO kv (key, value) VALUES (?, ?)
                   ON CONFLICT(key) DO UPDATE SET value = excluded.value""",
                (key, value),
            )
            self._conn.commit()
