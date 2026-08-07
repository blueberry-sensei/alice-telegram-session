"""
Bàn giao cho người thật — đăng nhập, OTP, xác nhận.

Đây là hiện thân bằng code của một ranh giới cứng: **agent không bao giờ tự đăng
nhập, không nhập mật khẩu, không nhập OTP, không giải CAPTCHA** — kể cả khi
credential được đưa thẳng trong chat và được uỷ quyền rõ ràng.

Thay vì thế, nó mở một "gate": gửi hướng dẫn lên chat rồi CHỜ. Người thật làm xong
gõ `/done`. Không có đường tắt nào khác, và đó là điểm mạnh chứ không phải hạn chế —
credential không bao giờ đi qua context của model, nên không bao giờ nằm trong
archive vĩnh viễn.
"""

from __future__ import annotations

import asyncio
import time

from atls import log
from atls.store import Store

_log = log.get("capabilities.handoff")

POLL_SECONDS = 5


async def request_human(
    *, store: Store, api, chat_id: str, kind: str, what: str,
    instructions: str = "", timeout: float = 900,
) -> bool:
    """Nhờ người thật làm `what`, chờ tối đa `timeout` giây. True = họ đã gõ /done.

    Chờ NGAY TRONG lượt này là cố ý. Agent chạy headless một lượt — không có "quay
    lại" nào cả, nên trả về rồi hẹn xử lý sau là mất trắng việc đang làm.
    """
    gate_id = store.open_gate(chat_id, kind, what)
    icon = {"login": "🔐", "otp": "🔢", "confirm": "✋"}.get(kind, "🙏")

    body = [f"{icon} <b>Dạ em cần Bệ hạ giúp một việc ạ</b>", "", what]
    if instructions:
        body += ["", instructions]
    body += ["", f"Xong rồi Bệ hạ gõ <code>/done</code> giúp em nhé. "
                 f"Em chờ tối đa {int(timeout / 60)} phút ạ."]
    await api.send_message(chat_id, "\n".join(body))
    _log.info("mở gate %s (%s) cho chat %s", gate_id, kind, chat_id)

    deadline = time.time() + timeout
    while time.time() < deadline:
        await asyncio.sleep(POLL_SECONDS)
        if not any(g["id"] == gate_id for g in store.pending_gates(chat_id)):
            _log.info("gate %s đã được giải quyết", gate_id)
            return True

    await api.send_message(
        chat_id, "Dạ em chờ hơi lâu mà chưa thấy Bệ hạ báo xong, em tạm dừng việc này ạ. "
                 "Lúc nào rảnh Bệ hạ nhắn lại em làm tiếp nha."
    )
    _log.info("gate %s hết giờ chờ", gate_id)
    return False
