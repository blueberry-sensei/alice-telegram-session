"""Dispatcher — chỗ mọi tầng gặp nhau, và chỗ người dùng nhìn thấy kết quả.

Nhóm test này canh thứ khó thấy nhất từ trong code: **một câu hỏi thì nhận đúng một
câu trấn an**. Đọc code từng tầng thì mọi thứ đều hợp lý; chỉ khi ghép lại mới lộ ra
là có hai `AckGuard` nối nhau và cái sau không biết cái trước đã nói gì.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from conftest import add, make_update

from atls.adapters.base import AgentResult
from atls.memory.compactor import Compactor
from atls.runtime.dispatcher import Dispatcher
from atls.runtime.locks import ChatLockRegistry
from atls.runtime.router import Decision, Route
from atls.store import Store
from atls.telegram.model import parse_update

ACK_FRAGMENTS = ("chờ em", "đợi em", "em đi tra")


class _API:
    def __init__(self):
        self.messages: list[str] = []
        self.documents: list = []
        self.photos: list = []

    async def send_message(self, chat_id, text, **kw):
        self.messages.append(text)
        return [len(self.messages)]

    async def send_chat_action(self, chat_id, action="typing"):
        return None

    async def send_document(self, chat_id, path, caption=""):
        self.documents.append((path, caption))
        return 1

    async def send_photo(self, chat_id, path, caption=""):
        self.photos.append((path, caption))
        return 1

    @property
    def acks(self) -> list[str]:
        return [m for m in self.messages if any(f in m.lower() for f in ACK_FRAGMENTS)]


class _Adapter:
    name = "fake"

    def __init__(self, reply: str = "Dạ xong rồi ạ.", delay: float = 0.0):
        self._reply = reply
        self._delay = delay
        self.calls = 0

    def is_available(self) -> bool:
        return True

    def supports_resume(self) -> bool:
        return True

    async def run(self, req):
        self.calls += 1
        if self._delay:
            await asyncio.sleep(self._delay)
        return AgentResult(ok=True, text=self._reply)


class _Cfg:
    workers = 3
    window_tokens = 20_000
    agent_model = ""
    ack_after = 30.0          # cao, để nhánh "quá lâu" không tự bắn trong test

    def __init__(self, root: Path):
        self.agent_cwd = root
        self.data_dir = root / ".atls"
        self.inbox_dir = self.data_dir / "inbox"
        self.outbox_dir = self.data_dir / "outbox"
        for d in (self.data_dir, self.inbox_dir, self.outbox_dir):
            d.mkdir(parents=True, exist_ok=True)


def _build(tmp_path: Path, store: Store, adapter: _Adapter, *, trigger: int = 100):
    from atls.session import SessionManager

    api = _API()
    compactor = Compactor(
        store, _summarize, trigger_tokens=trigger, keep_raw=5,
    )
    disp = Dispatcher(
        cfg=_Cfg(tmp_path), store=store, api=api, adapter=adapter,
        sessions=SessionManager(store, max_age=12 * 3600, idle=3 * 3600),
        compactor=compactor, chat_locks=ChatLockRegistry(),
    )
    return disp, api


async def _summarize(prompt: str) -> str:
    return "BẢN TÓM TẮT"


def _batch(text: str = "@alice_bot check giùm cái feed"):
    return [parse_update(make_update(1, text))]


def _route(**kw) -> Route:
    base = dict(decision=Decision.ANSWER, reason="test", addressed=True, likely_long=True)
    return Route(**{**base, **kw})


# ── một câu hỏi, một câu trấn an ─────────────────────────────────────────────

async def test_mot_luot_chi_gui_dung_mot_ack(tmp_path: Path, store: Store, chat: str):
    """Lượt vừa cần nén VỪA là việc dài — trước đây nhận HAI câu "chờ em chút".

    Hai `AckGuard` nối nhau: cái đầu (lúc nén) gửi ack rồi bị vứt đi cùng cờ `sent` của
    nó, nên cái sau không biết là đã ack.
    """
    for i in range(60):
        add(store, chat, f"tin số {i} đủ dài để vượt ngưỡng nén", update_id=i)

    disp, api = _build(tmp_path, store, _Adapter())
    await disp.handle(chat, _batch(), _route())

    assert len(api.acks) == 1, f"một câu hỏi phải nhận đúng một câu trấn an: {api.messages}"
    assert api.messages[-1] == "Dạ xong rồi ạ."


async def test_khong_ack_khi_khong_can_nen_va_viec_ngan(tmp_path: Path, store: Store, chat: str):
    """Hỏi nhanh đáp nhanh thì im — ack ngay lập tức chỉ tạo hai tin cho một câu trả lời."""
    add(store, chat, "ok chưa em", update_id=1)
    disp, api = _build(tmp_path, store, _Adapter(), trigger=10_000_000)

    await disp.handle(chat, _batch(), _route(likely_long=False))

    assert api.acks == []
    assert api.messages == ["Dạ xong rồi ạ."]


async def test_luot_im_lang_sau_khi_da_ack_thi_duoc_don_lai(
    tmp_path: Path, store: Store, chat: str
):
    """Đã trót nói "chờ em chút" thì không được biến mất.

    Trước đây cờ `sent` được đọc từ guard THỨ HAI, nên khi ack đến từ guard đầu (lúc
    nén) và lượt kết thúc bằng `[SILENT]`, câu "chờ em chút" nằm lại một mình trong
    group, không bao giờ có hồi kết.
    """
    for i in range(60):
        add(store, chat, f"tin số {i} đủ dài để vượt ngưỡng nén", update_id=i)

    disp, api = _build(tmp_path, store, _Adapter(reply="[SILENT]"))
    await disp.handle(chat, _batch(), _route())

    assert len(api.acks) == 1
    assert api.messages[-1] != api.acks[0], "ack không được là tin cuối cùng của lượt"


async def test_khong_ack_khi_tin_khong_goi_thang(tmp_path: Path, store: Store, chat: str):
    """Agent tự dưng nói "chờ em chút" trong cuộc trò chuyện không ai hỏi nó = bị tắt."""
    add(store, chat, "chuyện riêng", update_id=1)
    disp, api = _build(tmp_path, store, _Adapter(), trigger=10_000_000)

    await disp.handle(chat, _batch(), _route(addressed=False))

    assert api.acks == []


# ── khoá chat ────────────────────────────────────────────────────────────────

async def test_hai_luot_cung_chat_khong_chay_chong_nhau(
    tmp_path: Path, store: Store, chat: str
):
    add(store, chat, "hỏi", update_id=1)
    adapter = _Adapter(delay=0.05)
    disp, api = _build(tmp_path, store, adapter, trigger=10_000_000)

    await asyncio.gather(
        disp.handle(chat, _batch(), _route(likely_long=False)),
        disp.handle(chat, _batch(), _route(likely_long=False)),
    )

    assert adapter.calls == 2, "cả hai lượt đều phải chạy, chỉ là lần lượt"
    assert not disp.busy(chat)


async def test_stop_cat_luot_dang_chay(tmp_path: Path, store: Store, chat: str):
    add(store, chat, "hỏi", update_id=1)
    disp, api = _build(tmp_path, store, _Adapter(delay=5.0), trigger=10_000_000)

    task = asyncio.create_task(disp.handle(chat, _batch(), _route(likely_long=False)))
    await asyncio.sleep(0.05)
    assert disp.cancel(chat) is True

    with pytest.raises(asyncio.CancelledError):
        await task
    assert any("dừng" in m.lower() for m in api.messages)


async def test_stop_khi_khong_co_gi_chay_tra_ve_false(tmp_path: Path, store: Store, chat: str):
    disp, _ = _build(tmp_path, store, _Adapter(), trigger=10_000_000)
    assert disp.cancel(chat) is False
