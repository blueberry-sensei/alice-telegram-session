"""Runtime — debounce, khoá, đồng thời.

Yêu cầu gốc: "không bị spam session gây ra tình trạng nhiều hơn 1 agent cùng sống và
làm việc và không biết gì về nhau". Các test dưới đây là bằng chứng cho điều đó.
"""

from __future__ import annotations

import asyncio
import os
import time

import pytest
from conftest import make_update

from atls.runtime.debounce import Debouncer
from atls.runtime.locks import (
    STALE_SECONDS,
    ChatLockRegistry,
    ResourceLock,
    SingletonLock,
)
from atls.telegram.model import parse_update


def msg(n: int, text: str = "hi", chat_id: str = "-100200"):
    return parse_update(make_update(n, text, chat_id=chat_id))


# ── debounce ─────────────────────────────────────────────────────────────────

async def test_gom_tin_go_lien_tiep_thanh_mot_chum():
    """Ba tin gõ liên tiếp phải sinh MỘT lượt agent, không phải ba."""
    batches: list[list] = []

    async def flush(chat_id, batch):
        batches.append(batch)

    d = Debouncer(0.15, 5.0, flush)
    for i in range(3):
        await d.add(msg(i, f"tin {i}"))
        await asyncio.sleep(0.03)

    await asyncio.sleep(0.4)
    assert len(batches) == 1, "gõ liên tiếp mà sinh nhiều lượt = trả lời khi chưa đọc hết"
    assert len(batches[0]) == 3


async def test_moi_tin_moi_reset_dong_ho():
    """Debounce THẬT, không phải cửa sổ cố định."""
    fired = asyncio.Event()

    async def flush(chat_id, batch):
        fired.set()

    d = Debouncer(0.2, 5.0, flush)
    start = time.monotonic()
    for i in range(4):
        await d.add(msg(i))
        await asyncio.sleep(0.1)   # luôn ngắn hơn cửa sổ 0.2s

    await asyncio.wait_for(fired.wait(), timeout=2)
    assert time.monotonic() - start >= 0.45, "cửa sổ không được reset theo mỗi tin mới"


async def test_tran_debounce_chan_nguoi_go_lien_tuc():
    """Một người gõ mãi không được giữ agent câm vĩnh viễn."""
    fired = asyncio.Event()

    async def flush(chat_id, batch):
        fired.set()

    d = Debouncer(0.3, 0.4, flush)     # trần thấp hơn hẳn cửa sổ
    for i in range(10):
        await d.add(msg(i))
        await asyncio.sleep(0.08)
        if fired.is_set():
            break
    assert fired.is_set(), "chạm trần max_wait phải chốt chùm dù người ta còn gõ"


async def test_moi_chat_co_dong_ho_rieng():
    """Chat A gõ dở không được làm chậm chat B."""
    seen: dict[str, int] = {}

    async def flush(chat_id, batch):
        seen[chat_id] = len(batch)

    d = Debouncer(0.15, 5.0, flush)
    await d.add(msg(1, chat_id="-1"))
    await d.add(msg(2, chat_id="-2"))
    await asyncio.sleep(0.4)

    assert seen == {"-1": 1, "-2": 1}


async def test_chum_tin_khong_mat_khi_flush_co_await():
    """`_fire` được gọi TỪ task đếm ngược, nên nó không được huỷ chính task đó.

    Huỷ chính mình thì `CancelledError` nổ ở điểm `await` đầu tiên bên trong `_flush`
    và cả chùm biến mất — không log, không ai biết. Bản cũ chỉ tình cờ không lộ vì
    `_flush` không await lần nào; test này canh đúng cái tình cờ đó.
    """
    got: list = []

    async def flush(chat_id, batch):
        await asyncio.sleep(0.01)     # điểm huỷ, nếu có ai đó huỷ
        got.extend(batch)

    d = Debouncer(0.05, 5.0, flush)
    await d.add(msg(1))
    await asyncio.sleep(0.3)

    assert len(got) == 1, "chùm tin bị nuốt vì debouncer tự huỷ chính nó"


async def test_flush_all_khong_mat_tin_dang_cho():
    """Tắt máy trong lúc còn tin trong bộ đệm."""
    got: list = []

    async def flush(chat_id, batch):
        got.extend(batch)

    d = Debouncer(30.0, 60.0, flush)   # cửa sổ dài, sẽ không tự chốt
    await d.add(msg(1))
    await d.add(msg(2))
    await d.flush_all()

    assert len(got) == 2


# ── khoá ─────────────────────────────────────────────────────────────────────

async def test_khong_bao_gio_hai_agent_tren_cung_mot_chat():
    locks = ChatLockRegistry()
    concurrent = 0
    peak = 0

    async def turn():
        nonlocal concurrent, peak
        async with locks.get("-100"):
            concurrent += 1
            peak = max(peak, concurrent)
            await asyncio.sleep(0.05)
            concurrent -= 1

    await asyncio.gather(*(turn() for _ in range(5)))
    assert peak == 1, "hai agent cùng một cuộc hội thoại là lỗi nghiêm trọng nhất"


async def test_chat_khac_nhau_chay_song_song():
    locks = ChatLockRegistry()
    peak = 0
    concurrent = 0

    async def turn(chat: str):
        nonlocal concurrent, peak
        async with locks.get(chat):
            concurrent += 1
            peak = max(peak, concurrent)
            await asyncio.sleep(0.05)
            concurrent -= 1

    await asyncio.gather(*(turn(f"-{i}") for i in range(4)))
    assert peak == 4, "chat khác nhau phải chạy song song, không xếp hàng"


def test_singleton_chan_daemon_thu_hai(tmp_path):
    path = tmp_path / "daemon.lock"
    first = SingletonLock(path)
    assert first.acquire()

    assert not SingletonLock(path).acquire(), "hai daemon cùng chạy = tin bị xử lý hai lần"

    first.release()
    assert SingletonLock(path).acquire(), "thả rồi phải chiếm lại được"


def test_singleton_don_lock_mo_coi(tmp_path):
    """Máy sập giữa chừng để lại lock của một PID đã chết."""
    path = tmp_path / "daemon.lock"
    path.write_text("999999\n0\n", encoding="utf-8")  # PID gần như chắc chắn không tồn tại

    assert SingletonLock(path).acquire(), "lock mồ côi phải được dọn, không thì không khởi động lại được"


def test_singleton_khong_ket_vinh_vien_vi_pid_bi_tai_dung(tmp_path):
    """PID được hệ điều hành tái dùng — Windows rất nhanh.

    Máy sập, khởi động lại, một tiến trình bất kỳ nhận đúng con số PID cũ, và
    `os.kill(pid, 0)` bảo "còn sống". Chỉ dựa vào PID thì daemon thật không bao giờ
    khởi động lại được, và thông báo lỗi chỉ đường đi xoá lock bằng tay — đúng thứ lớp
    khoá này sinh ra để khỏi phải làm. Nhịp tim phá được thế kẹt đó: tiến trình mượn
    PID không biết gì về file lock nên không bao giờ chạm nó.
    """
    path = tmp_path / "daemon.lock"
    # PID của CHÍNH tiến trình test: chắc chắn "còn sống" dưới mắt `os.kill(pid, 0)`.
    path.write_text(f"{os.getpid()}\n0\n", encoding="utf-8")
    old = time.time() - (STALE_SECONDS + 60)
    os.utime(path, (old, old))

    lock = SingletonLock(path)
    assert lock.acquire(), "lock cũ không còn nhịp tim thì phải giành lại được"
    lock.release()


def test_singleton_khong_cuop_lock_con_nhip_tim(tmp_path):
    path = tmp_path / "daemon.lock"
    first = SingletonLock(path)
    assert first.acquire()
    try:
        # mtime mới → chủ còn sống → không ai được cướp, dù PID có trùng ai đi nữa.
        assert not SingletonLock(path).acquire()
    finally:
        first.release()


async def test_resource_lock_loai_tru_lan_nhau(tmp_path):
    path = tmp_path / "chrome.lock"
    a = ResourceLock(path, "chrome")
    b = ResourceLock(path, "chrome")

    assert await a.acquire(timeout=1)
    assert not await b.acquire(timeout=0.5), "hai bên cùng mở Chrome profile = tranh nhau"

    a.release()
    assert await b.acquire(timeout=1)
    b.release()


def test_resource_lock_doc_duoc_tu_ben_ngoai(tmp_path):
    """Hợp đồng với script ngoài: file tồn tại + mtime mới = đang bận. Chỉ có thế."""
    path = tmp_path / "sync.lock"
    assert not ResourceLock.is_busy(path)

    path.write_text("1\nsync\n0\n", encoding="utf-8")
    assert ResourceLock.is_busy(path)
