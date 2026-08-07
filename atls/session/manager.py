"""
Vòng đời session — một session cho mỗi chat.

Điều quan trọng nhất phải hiểu về file này: **xoay session không làm mất trí nhớ.**

Session là bộ nhớ *làm việc* của CLI (nó đang mở file nào, vừa chạy lệnh gì, suy
luận tới đâu). Sau 12 giờ, thứ đó phần lớn là rác — nó thuộc về công việc hôm qua
và chỉ làm nhiễu việc hôm nay. Trí nhớ *hội thoại* nằm ở tầng khác (`atls/memory/`)
và được dán vào mọi session mới. Đó là lý do hai tầng tách rời.

Điều kiện xoay — thoả BẤT KỲ điều nào:

    tuổi > ATLS_SESSION_MAX_AGE (12h)   ràng buộc cứng, rác context tích luỹ
    im lặng > ATLS_SESSION_IDLE (3h)    chuyện mới thì nên bắt đầu sạch
    đổi agent CLI                       session của claude vô nghĩa với codex
    resume thất bại                     CLI dọn session cũ là chuyện thường
    /reset                              người dùng chủ động
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass

from atls import log
from atls.store import SessionRow, Store

_log = log.get("session")


@dataclass(frozen=True)
class SessionChoice:
    row: SessionRow
    resume: bool         # True = nối tiếp session CLI cũ
    rotated_from: str    # lý do xoay, rỗng nếu dùng tiếp session cũ

    @property
    def fresh(self) -> bool:
        """Session mới thì phải dán cửa sổ hội thoại vào prompt.

        Session nối tiếp thì KHÔNG dán — lịch sử thật đã nằm sẵn trong session CLI, dán
        thêm chỉ tạo nhiễu và làm agent tưởng có hai nguồn sự thật khác nhau.
        """
        return not self.resume


class SessionManager:
    def __init__(self, store: Store, *, max_age: int, idle: int) -> None:
        self._store = store
        self._max_age = max_age
        self._idle = idle

    def choose(self, chat_id: str, agent: str) -> SessionChoice:
        now = time.time()
        current = self._store.open_session(chat_id)

        if current is None:
            return self._open(chat_id, agent, "")

        reason = self._rotation_reason(current, agent, now)
        if reason:
            self._store.close_session(current.id, reason)
            _log.info(
                "xoay session chat %s (%s): %d lượt, sống %.1f giờ",
                chat_id, reason, current.turns, (now - current.created_at) / 3600,
            )
            return self._open(chat_id, agent, reason)

        # Session tồn tại nhưng CLI chưa thật sự tạo nó (lượt trước chết trước khi
        # chạy xong). `--resume` vào một id chưa tồn tại là lỗi chắc chắn.
        return SessionChoice(row=current, resume=current.started, rotated_from="")

    def _rotation_reason(self, s: SessionRow, agent: str, now: float) -> str:
        if s.agent != agent:
            return "agent_changed"
        if now - s.created_at > self._max_age:
            return "max_age"
        if now - s.last_used_at > self._idle:
            return "idle"
        return ""

    def _open(self, chat_id: str, agent: str, reason: str) -> SessionChoice:
        row = self._store.create_session(chat_id, agent, str(uuid.uuid4()))
        return SessionChoice(row=row, resume=False, rotated_from=reason)

    def on_success(self, choice: SessionChoice) -> None:
        self._store.touch_session(choice.row.id, started=True)

    def on_resume_failed(self, chat_id: str, choice: SessionChoice, agent: str) -> SessionChoice:
        """Resume hỏng → mở session sạch và chạy lại **cùng prompt**.

        Đây KHÔNG phải lỗi đáng báo cho người dùng: CLI dọn session cũ theo lịch riêng
        của nó là chuyện bình thường. Ta mất ngữ cảnh phía CLI, không mất câu trả lời —
        vì cửa sổ hội thoại sẽ được dán vào session mới.
        """
        self._store.close_session(choice.row.id, "resume_failed")
        _log.info("resume hỏng cho chat %s, mở session mới", chat_id)
        return self._open(chat_id, agent, "resume_failed")

    def reset(self, chat_id: str) -> None:
        if current := self._store.open_session(chat_id):
            self._store.close_session(current.id, "reset")

    def describe(self, chat_id: str) -> str:
        s = self._store.open_session(chat_id)
        if not s:
            return "chưa có session nào đang mở"
        now = time.time()
        return (
            f"session `{s.agent_session_id[:8]}` ({s.agent}) · "
            f"{s.turns} lượt · sống {(now - s.created_at) / 3600:.1f}h "
            f"/ trần {self._max_age / 3600:.0f}h · "
            f"im {(now - s.last_used_at) / 60:.0f} phút"
        )
