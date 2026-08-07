"""Vòng đời session — trần 12 giờ và các đường xoay khác."""

from __future__ import annotations

import time

from atls.session import SessionManager
from atls.store import Store

HOUR = 3600


def mgr(store: Store, *, max_age: int = 12 * HOUR, idle: int = 3 * HOUR) -> SessionManager:
    return SessionManager(store, max_age=max_age, idle=idle)


def _age_session(store: Store, session_id: int, *, created_ago: float = 0,
                 used_ago: float = 0) -> None:
    """Lùi mốc thời gian của một session để mô phỏng thời gian trôi."""
    now = time.time()
    with store._lock:  # noqa: SLF001 — test cố ý can thiệp trực tiếp
        store._conn.execute(
            "UPDATE sessions SET created_at = ?, last_used_at = ? WHERE id = ?",
            (now - created_ago, now - used_ago, session_id),
        )
        store._conn.commit()


def test_lan_dau_mo_session_moi(store: Store, chat: str):
    choice = mgr(store).choose(chat, "claude")
    assert not choice.resume
    assert choice.fresh, "session mới phải được dán cửa sổ hội thoại"


def test_dung_tiep_session_dang_song(store: Store, chat: str):
    m = mgr(store)
    first = m.choose(chat, "claude")
    m.on_success(first)

    second = m.choose(chat, "claude")
    assert second.row.agent_session_id == first.row.agent_session_id
    assert second.resume
    assert not second.fresh, "session nối tiếp KHÔNG dán lại cửa sổ"


def test_xoay_khi_qua_12_gio(store: Store, chat: str):
    m = mgr(store)
    first = m.choose(chat, "claude")
    m.on_success(first)
    _age_session(store, first.row.id, created_ago=13 * HOUR, used_ago=1)

    second = m.choose(chat, "claude")
    assert second.row.agent_session_id != first.row.agent_session_id
    assert second.rotated_from == "max_age"
    assert second.fresh, "xoay session thì phải dán lại cửa sổ, nếu không là mất trí nhớ"


def test_khong_xoay_khi_moi_11_gio(store: Store, chat: str):
    m = mgr(store)
    first = m.choose(chat, "claude")
    m.on_success(first)
    _age_session(store, first.row.id, created_ago=11 * HOUR, used_ago=60)

    assert m.choose(chat, "claude").row.agent_session_id == first.row.agent_session_id


def test_xoay_khi_im_lang_qua_nguong(store: Store, chat: str):
    m = mgr(store)
    first = m.choose(chat, "claude")
    m.on_success(first)
    _age_session(store, first.row.id, created_ago=4 * HOUR, used_ago=4 * HOUR)

    assert m.choose(chat, "claude").rotated_from == "idle"


def test_doi_agent_thi_xoay(store: Store, chat: str):
    """Session của claude vô nghĩa với codex."""
    m = mgr(store)
    first = m.choose(chat, "claude")
    m.on_success(first)

    assert m.choose(chat, "codex").rotated_from == "agent_changed"


def test_session_chua_start_thi_khong_resume(store: Store, chat: str):
    """Lượt trước chết trước khi CLI kịp tạo session. `--resume` vào id chưa tồn tại
    là lỗi chắc chắn."""
    m = mgr(store)
    first = m.choose(chat, "claude")
    # cố ý KHÔNG gọi on_success

    second = m.choose(chat, "claude")
    assert second.row.agent_session_id == first.row.agent_session_id
    assert not second.resume


def test_reset_dong_session(store: Store, chat: str):
    m = mgr(store)
    first = m.choose(chat, "claude")
    m.on_success(first)
    m.reset(chat)

    assert m.choose(chat, "claude").row.agent_session_id != first.row.agent_session_id


def test_resume_hong_mo_session_moi(store: Store, chat: str):
    m = mgr(store)
    first = m.choose(chat, "claude")
    m.on_success(first)
    current = m.choose(chat, "claude")

    fresh = m.on_resume_failed(chat, current, "claude")
    assert not fresh.resume
    assert fresh.row.agent_session_id != current.row.agent_session_id


def test_moi_chat_mot_session_doc_lap(store: Store):
    store.upsert_chat("a", "private", "A")
    store.upsert_chat("b", "private", "B")
    m = mgr(store)

    sa, sb = m.choose("a", "claude"), m.choose("b", "claude")
    assert sa.row.agent_session_id != sb.row.agent_session_id
