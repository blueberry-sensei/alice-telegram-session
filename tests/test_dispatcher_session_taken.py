"""Lượt chết dở KHÔNG được khoá chat vĩnh viễn.

Chuyện thật, 2026-08-10: một lượt tạo session bằng `--session-id` rồi chết ngay sau đó.
Cờ `started` chỉ được ghi khi lượt thành công, nên nó vẫn False — và mọi tin nhắn sau
đó lại gọi `--session-id` vào đúng id ấy, ăn `Session ID ... is already in use`. Alice
câm từ 12:56 tới tối, không có đường tự thoát: càng nhắn càng hỏng cùng một kiểu.

Hai bài dưới khoá hai nửa của cái vá: đánh dấu id đã bị chiếm ngay khi CLI chạy, và
tự mở session mới khi vẫn gặp lỗi "đã bị chiếm".
"""

from __future__ import annotations

from pathlib import Path

from conftest import add
from test_dispatcher import _API, _Cfg, _batch, _route, _summarize

from atls.adapters.base import AgentResult
from atls.memory.compactor import Compactor
from atls.runtime.dispatcher import Dispatcher
from atls.runtime.locks import ChatLockRegistry
from atls.session import SessionManager
from atls.store import Store


def _disp(tmp_path: Path, store: Store, adapter, api) -> Dispatcher:
    return Dispatcher(
        cfg=_Cfg(tmp_path), store=store, api=api, adapter=adapter,
        sessions=SessionManager(store, max_age=12 * 3600, idle=3 * 3600),
        compactor=Compactor(store, _summarize, trigger_tokens=10_000_000, keep_raw=5),
        chat_locks=ChatLockRegistry(),
    )


class _DiesOnce:
    """Lượt đầu tạo session xong chết; các lượt sau chạy được."""

    name = "fake"

    def __init__(self) -> None:
        self.requests: list = []

    def is_available(self) -> bool:
        return True

    def supports_resume(self) -> bool:
        return True

    async def run(self, req):
        self.requests.append(req)
        if len(self.requests) == 1:
            return AgentResult(ok=False, text="", returncode=1, stderr="")
        return AgentResult(ok=True, text="Dạ xong rồi ạ.")


class _AlwaysTaken:
    """CLI luôn báo id đã bị chiếm khi tạo mới; nối tiếp thì chạy được."""

    name = "fake"

    def __init__(self) -> None:
        self.requests: list = []

    def is_available(self) -> bool:
        return True

    def supports_resume(self) -> bool:
        return True

    async def run(self, req):
        self.requests.append(req)
        if not req.resume and len(self.requests) == 1:
            return AgentResult(
                ok=False, text="", returncode=1,
                stderr="Error: Session ID fd942de8-80e5-4069-b9d6-e2c091651670 is already in use.",
            )
        return AgentResult(ok=True, text="Dạ xong rồi ạ.")


async def test_luot_hong_van_danh_dau_id_da_bi_chiem(tmp_path: Path, store: Store, chat: str):
    add(store, chat, "chào em", update_id=1)
    adapter = _DiesOnce()
    disp = _disp(tmp_path, store, adapter, _API())

    await disp.handle(chat, _batch(), _route(likely_long=False))
    first_id = adapter.requests[0].session_id
    assert not adapter.requests[0].resume

    # Lượt sau PHẢI đi `--resume`, không được gọi `--session-id` lại vào id cũ.
    add(store, chat, "còn đó không em", update_id=2)
    await disp.handle(chat, _batch(), _route(likely_long=False))
    assert adapter.requests[1].session_id == first_id
    assert adapter.requests[1].resume, "id đã bị CLI chiếm mà vẫn tạo lại = câm vĩnh viễn"


async def test_id_da_bi_chiem_thi_mo_session_moi_va_van_tra_loi(
    tmp_path: Path, store: Store, chat: str
):
    add(store, chat, "em ơi", update_id=1)
    adapter = _AlwaysTaken()
    api = _API()
    disp = _disp(tmp_path, store, adapter, api)

    await disp.handle(chat, _batch(), _route(likely_long=False))

    assert len(adapter.requests) == 2, "phải tự mở session mới rồi chạy lại"
    assert adapter.requests[1].session_id != adapter.requests[0].session_id
    assert api.messages[-1] == "Dạ xong rồi ạ.", api.messages
