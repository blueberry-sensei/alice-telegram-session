"""Nhánh *resume thất bại* của dispatcher — đường hồi phục khi CLI dọn mất session cũ.

Đây là nhánh hiếm khi chạy trong lúc dev (session còn mới) nhưng CHẮC CHẮN chạy khi
Alice sống dài ngày. Nếu nó gãy thì lượt đó mất trắng: người gửi thấy "chờ em chút"
rồi không bao giờ có câu trả lời.
"""

from __future__ import annotations

import uuid
from pathlib import Path

from conftest import add
from test_dispatcher import _API, _Cfg, _batch, _route, _summarize

from atls.adapters.base import AgentResult
from atls.memory.compactor import Compactor
from atls.runtime.dispatcher import Dispatcher
from atls.runtime.locks import ChatLockRegistry
from atls.session import SessionManager
from atls.store import Store


class _FlakyAdapter:
    """Lần gọi đầu (có `--resume`) hỏng, lần sau (session sạch) chạy được."""

    name = "fake"

    def __init__(self) -> None:
        self.requests: list = []

    def is_available(self) -> bool:
        return True

    def supports_resume(self) -> bool:
        return True

    async def run(self, req):
        self.requests.append(req)
        if req.resume:
            return AgentResult(
                ok=False, text="", returncode=1,
                stderr="No conversation found with session ID: ...",
            )
        return AgentResult(ok=True, text="Dạ xong rồi ạ.")


async def test_resume_hong_thi_chay_lai_luot_chu_khong_no(
    tmp_path: Path, store: Store, chat: str
):
    add(store, chat, "hôm qua mình chốt gì ấy nhỉ", update_id=1)

    # Session cũ đã được CLI tạo thật → lượt này sẽ đi đường `--resume`.
    row = store.create_session(chat, "fake", str(uuid.uuid4()))
    store.touch_session(row.id, started=True)

    adapter = _FlakyAdapter()
    api = _API()
    disp = Dispatcher(
        cfg=_Cfg(tmp_path), store=store, api=api, adapter=adapter,
        sessions=SessionManager(store, max_age=12 * 3600, idle=3 * 3600),
        compactor=Compactor(store, _summarize, trigger_tokens=10_000_000, keep_raw=5),
        chat_locks=ChatLockRegistry(),
    )

    await disp.handle(chat, _batch(), _route(likely_long=False))

    assert len(adapter.requests) == 2, "phải thử lại bằng session sạch"
    assert adapter.requests[0].resume and not adapter.requests[1].resume
    assert api.messages[-1] == "Dạ xong rồi ạ.", api.messages

    # Lần hai KHÔNG có session CLI cũ để dựa vào → prompt phải tự mang cửa sổ hội thoại.
    assert "hôm qua mình chốt gì ấy nhỉ" in adapter.requests[1].prompt
