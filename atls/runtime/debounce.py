"""
Debounce theo từng chat.

Người ta tách một ý thành ba tin: "Alice ơi" / "check giùm cái feed" / "gấp nha".
Trả lời từng tin thì vừa chậm gấp ba, vừa trả lời tin đầu khi chưa đọc hai tin sau —
tức là trả lời sai.

Đây là debounce THẬT, không phải cửa sổ cố định: mỗi tin mới **reset** đồng hồ. Trần
`max_wait` để một người gõ liên tục không giữ agent câm vĩnh viễn.

Mỗi chat có một bộ đệm và một đồng hồ riêng — chat A đang gõ dở không được làm chậm
chat B.
"""

from __future__ import annotations

import asyncio
import time
from typing import Awaitable, Callable

from atls import log
from atls.telegram.model import Incoming

_log = log.get("runtime.debounce")


class Debouncer:
    def __init__(
        self,
        wait: float,
        max_wait: float,
        flush: Callable[[str, list[Incoming]], Awaitable[None]],
    ) -> None:
        self._wait = wait
        self._max_wait = max_wait
        self._flush = flush
        self._buffers: dict[str, list[Incoming]] = {}
        self._timers: dict[str, asyncio.Task] = {}
        self._first_seen: dict[str, float] = {}

    def pending(self, chat_id: str) -> int:
        return len(self._buffers.get(chat_id, ()))

    async def add(self, msg: Incoming) -> None:
        chat = msg.chat_id
        self._buffers.setdefault(chat, []).append(msg)
        self._first_seen.setdefault(chat, time.time())

        elapsed = time.time() - self._first_seen[chat]
        if elapsed >= self._max_wait:
            _log.debug("chat %s chạm trần debounce %.0fs -> chốt chùm", chat, self._max_wait)
            await self._fire(chat)
            return

        # Huỷ đồng hồ cũ rồi đặt lại = reset cửa sổ.
        if timer := self._timers.pop(chat, None):
            timer.cancel()
        remaining = min(self._wait, self._max_wait - elapsed)
        self._timers[chat] = asyncio.create_task(
            self._countdown(chat, remaining), name=f"atls-debounce-{chat}"
        )

    async def _countdown(self, chat: str, delay: float) -> None:
        try:
            await asyncio.sleep(delay)
        except asyncio.CancelledError:
            return
        await self._fire(chat)

    async def _fire(self, chat: str) -> None:
        timer = self._timers.pop(chat, None)
        # `is not current_task()` là bắt buộc: `_fire` được gọi TỪ `_countdown`, nên
        # `timer` chính là task đang chạy dòng này. Huỷ nó = tự huỷ mình, và
        # `CancelledError` sẽ nổ ở điểm `await` đầu tiên bên trong `_flush` — chùm tin
        # biến mất, không log, không ai biết. Hôm nay `_flush` tình cờ không await lần
        # nào nên chưa lộ; đó là mìn chờ chứ không phải an toàn.
        if timer is not None and timer is not asyncio.current_task():
            timer.cancel()
        batch = self._buffers.pop(chat, [])
        self._first_seen.pop(chat, None)
        if not batch:
            return
        try:
            await self._flush(chat, batch)
        except Exception:  # noqa: BLE001 — một chùm hỏng không được giết daemon
            _log.exception("xử lý chùm tin của chat %s thất bại", chat)

    async def flush_all(self) -> None:
        """Chốt hết bộ đệm — dùng lúc tắt máy để không mất tin đang chờ."""
        for chat in list(self._buffers):
            await self._fire(chat)

    def cancel_all(self) -> None:
        for timer in self._timers.values():
            timer.cancel()
        self._timers.clear()
