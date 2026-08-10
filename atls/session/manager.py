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


def parse_quiet_window(raw: str) -> tuple[int, int] | None:
    """`"9-13"` → `(9, 13)`. Rỗng hoặc sai định dạng → `None` (không hạn chế giờ).

    Sai định dạng không được ném lỗi: cấu hình hỏng mà làm daemon không khởi động
    được thì mất cả kênh chat, trong khi thứ hỏng chỉ là một tối ưu về thời điểm.
    """
    parts = (raw or "").split("-")
    if len(parts) != 2:
        return None
    try:
        start, end = int(parts[0]), int(parts[1])
    except ValueError:
        return None
    if not (0 <= start <= 23 and 0 <= end <= 23):
        return None
    return (start, end)


def _hour_in_window(hour: int, window: tuple[int, int]) -> bool:
    """Giờ địa phương có nằm trong khung `[start, end)` không — kể cả khung vắt qua nửa đêm."""
    start, end = window
    if start == end:
        return True
    if start < end:
        return start <= hour < end
    return hour >= start or hour < end


class SessionManager:
    """Vòng đời session, cộng một luật về *thời điểm* được phép xoay.

    Xoay session làm agent quên ngữ cảnh làm việc trong vài giây đầu lượt sau. Điều
    đó vô hại lúc 10 giờ sáng và phiền lúc thị trường đang chạy — đúng lúc người
    dùng hỏi "lệnh sao rồi" thì lại là lúc agent vừa mất hết context phiên trước.

    Nên: hạn tuổi/im lặng tới hạn ngoài khung yên tĩnh thì **hoãn**, chờ tới khung
    yên tĩnh mới xoay. Có trần cứng `defer_ceiling` để một session không hoãn mãi —
    quá trần thì xoay bất kể giờ nào, vì rác context tích tụ mới là cái hại thật.

    `/reset` và `resume_failed` không đi qua luật này: một cái là người dùng chủ
    động, một cái là session bên CLI đã chết sẵn.
    """

    def __init__(
        self,
        store: Store,
        *,
        max_age: int,
        idle: int,
        quiet_window: tuple[int, int] | None = None,
        defer_ceiling: int | None = None,
    ) -> None:
        self._store = store
        self._max_age = max_age
        self._idle = idle
        self._quiet_window = quiet_window
        #: Mặc định gấp đôi hạn tuổi: hoãn được một vòng, không hoãn được hai.
        self._defer_ceiling = defer_ceiling if defer_ceiling is not None else max_age * 2

    def _may_rotate_now(self, s: SessionRow, reason: str, now: float) -> bool:
        """Đúng lúc để xoay chưa? Chỉ hoãn vì lý do hết hạn, không hoãn vì lý do khác."""
        if self._quiet_window is None or reason not in ("max_age", "idle"):
            return True
        if now - s.created_at > self._defer_ceiling:
            return True
        return _hour_in_window(time.localtime(now).tm_hour, self._quiet_window)

    def choose(self, chat_id: str, agent: str) -> SessionChoice:
        now = time.time()
        current = self._store.open_session(chat_id)

        if current is None:
            return self._open(chat_id, agent, "")

        reason = self._rotation_reason(current, agent, now)
        if reason and not self._may_rotate_now(current, reason, now):
            _log.info(
                "hoãn xoay session chat %s (%s): ngoài khung yên tĩnh %s",
                chat_id, reason, self._quiet_window,
            )
            reason = ""
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

    def on_claimed(self, choice: SessionChoice) -> None:
        """CLI đã chạy với `--session-id` → id đó **đã tồn tại trên đĩa**, kể cả khi lượt hỏng.

        Đây là chỗ Alice câm cả buổi chiều 2026-08-10: lượt đầu tạo session rồi chết
        giữa chừng, `started` vẫn False, nên mọi lượt sau lại gọi `--session-id` vào
        đúng id đó và ăn `Session ID ... is already in use` — vĩnh viễn, không tự thoát.
        `started` phải mang nghĩa "CLI đã chiếm id", không phải "lượt đó thành công".

        Đánh dấu sớm không có rủi ro ngược: nếu CLI chết TRƯỚC khi kịp tạo session thì
        lượt sau đi `--resume` vào id chưa có, hỏng, và nhánh resume-hỏng mở session sạch.
        """
        self._store.mark_session_started(choice.row.id)

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
