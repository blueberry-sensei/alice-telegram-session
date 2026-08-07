"""
Trả lời nhanh khi việc lâu — "Bệ hạ chờ em chút nhé".

Hai đường kích hoạt:
  • **Chủ động** — router đoán việc dài (`likely_long`) → gửi ngay, không đợi.
  • **Bị động** — quá `after` giây mà agent chưa xong → gửi.

Vì sao 12 giây: đó là ranh giới đo được giữa hai loại lượt. Hỏi-đáp thuần (đọc ký ức
rồi trả lời) mất 5–10 giây; lượt có làm việc thật (gọi API, mở trình duyệt, chạy
build) tính bằng phút. Hạ xuống 5 giây thì mọi câu hỏi ngắn đều lãnh hai tin nhắn;
nâng lên 30 giây thì người gửi đã kịp lo rồi.

Mọi câu ack cố ý KHÔNG hứa thời gian cụ thể. Hứa "2 phút nữa" rồi chạy 10 phút còn
tệ hơn không nói gì.

Ack chỉ gửi khi tin được gọi THẲNG. Agent tự dưng nói "chờ em chút" trong một cuộc
trò chuyện không ai hỏi nó là đúng kiểu bot bị tắt sau ba ngày.
"""

from __future__ import annotations

import asyncio
import random

from atls import log

_log = log.get("runtime.ack")

MESSAGES = (
    "Dạ em nhận được rồi ạ, Bệ hạ chờ em chút nhé, em kiểm tra thử.",
    "Dạ vâng, em đang xem, Bệ hạ đợi em một lát nha.",
    "Dạ em ghi nhận, việc này em phải mở ra coi kỹ, Bệ hạ chờ em xíu ạ.",
    "Dạ em đi tra rồi báo lại Bệ hạ ngay ạ.",
)

TYPING_REFRESH = 4.0  # hiệu ứng "đang gõ" của Telegram tự tắt sau ~5 giây


class AckGuard:
    """Context manager: bật hiệu ứng gõ, gửi ack nếu lâu, tự dọn khi thoát."""

    def __init__(self, api, chat_id: str, *, after: float, addressed: bool,
                 immediate: bool = False) -> None:
        self._api = api
        self._chat_id = chat_id
        self._after = after
        self._addressed = addressed
        self._immediate = immediate
        self._tasks: list[asyncio.Task] = []
        self.sent = False

    async def __aenter__(self) -> "AckGuard":
        self._tasks.append(asyncio.create_task(self._typing_loop()))
        if self._addressed:
            delay = 0.0 if self._immediate else self._after
            self._tasks.append(asyncio.create_task(self._ack_after(delay)))
        return self

    async def __aexit__(self, *exc) -> None:
        for task in self._tasks:
            task.cancel()
        # Chờ các task thật sự dừng trước khi trả quyền — nếu không, một ack đang
        # bay có thể hạ cánh SAU câu trả lời và người đọc thấy "chờ em chút" đứng
        # dưới kết quả.
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()

    async def _typing_loop(self) -> None:
        try:
            while True:
                await self._api.send_chat_action(self._chat_id, "typing")
                await asyncio.sleep(TYPING_REFRESH)
        except asyncio.CancelledError:
            pass

    async def _ack_after(self, delay: float) -> None:
        try:
            if delay > 0:
                await asyncio.sleep(delay)
            await self._api.send_message(self._chat_id, random.choice(MESSAGES))
            self.sent = True
            _log.info("đã gửi ack cho chat %s (sau %.0fs)", self._chat_id, delay)
        except asyncio.CancelledError:
            pass
        except Exception as exc:  # noqa: BLE001 — ack hỏng không được giết lượt
            _log.warning("gửi ack thất bại: %s", exc)
