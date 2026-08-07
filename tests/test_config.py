"""Cấu hình — và một luật an toàn không được phép mềm.

Cổng chat là thứ duy nhất đứng giữa "một tin nhắn Telegram" và "một lệnh chạy trên máy
chủ". Adapter claude chạy kèm `--dangerously-skip-permissions`, chat riêng thì tin nào
cũng trả lời — nên nếu cổng này fail-open thì bất kỳ ai đoán ra username của bot đều có
một shell trên máy. Nhóm test đầu tiên ở đây canh đúng chuyện đó.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from atls import config

_KEYS = (
    "TELEGRAM_BOT_TOKEN", "ATLS_ALLOWED_CHATS", "ATLS_ALLOW_ALL_CHATS",
    "ATLS_AGENT", "ATLS_AGENT_SKIP_PERMISSIONS", "ATLS_WINDOW_TOKENS",
    "ATLS_COMPACT_TRIGGER", "ATLS_DATA_DIR",
)


@pytest.fixture()
def env(monkeypatch, tmp_path: Path):
    """Môi trường sạch: `.env` thật của máy không được lọt vào test."""
    for key in _KEYS:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("ATLS_DATA_DIR", str(tmp_path / "data"))

    def load():
        return config.load(tmp_path / "khong-co.env")
    return load


# ── cổng chat ────────────────────────────────────────────────────────────────

def test_danh_sach_rong_thi_khong_chat_nao_duoc_phep(env):
    """FAIL-CLOSED. Trước đây trống nghĩa là 'nhận mọi chat', và trống là mặc định."""
    cfg = env()
    assert not cfg.chat_allowed("-100200")
    assert not cfg.chat_allowed("7")
    assert not cfg.chat_gate_is_open


def test_mo_cong_phai_la_hanh_dong_co_y(env, monkeypatch):
    monkeypatch.setenv("ATLS_ALLOW_ALL_CHATS", "1")
    cfg = env()
    assert cfg.chat_allowed("-100200")
    assert cfg.chat_gate_is_open, "doctor và log khởi động phải nói to chuyện này"


def test_danh_sach_co_ten_thi_chi_nhung_ten_do(env, monkeypatch):
    monkeypatch.setenv("ATLS_ALLOWED_CHATS", "-100200, 7")
    cfg = env()
    assert cfg.chat_allowed("-100200")
    assert cfg.chat_allowed("7")
    assert not cfg.chat_allowed("-999")
    assert not cfg.chat_gate_is_open


def test_danh_sach_co_ten_thi_allow_all_khong_noi_rong_them(env, monkeypatch):
    """Điền danh sách rồi mà quên tắt cờ mở toang: danh sách phải THẮNG."""
    monkeypatch.setenv("ATLS_ALLOWED_CHATS", "-100200")
    monkeypatch.setenv("ATLS_ALLOW_ALL_CHATS", "1")
    cfg = env()
    assert not cfg.chat_allowed("-999")


# ── quyền của agent ──────────────────────────────────────────────────────────

def test_bo_qua_hoi_quyen_tat_duoc(env, monkeypatch):
    """Một tuỳ chọn nguy hiểm mà không có đường tắt thì không phải quyết định thiết kế."""
    assert env().agent_skip_permissions is True
    monkeypatch.setenv("ATLS_AGENT_SKIP_PERMISSIONS", "0")
    assert env().agent_skip_permissions is False


def test_adapter_bo_co_khi_tat_hoi_quyen():
    from atls.adapters import build_adapter
    from atls.adapters.base import AgentRequest

    req = AgentRequest(prompt="hi", system="", session_id="abc", resume=False, cwd=Path("."))
    assert "--dangerously-skip-permissions" in build_adapter("claude").build_command(req)
    assert "--dangerously-skip-permissions" not in build_adapter(
        "claude", skip_permissions=False
    ).build_command(req)


# ── trí nhớ ──────────────────────────────────────────────────────────────────

def test_nguong_nen_luon_nam_duoi_tran_cua_so(env, monkeypatch):
    """Ngưỡng ≥ trần thì cửa sổ chạm trần trước khi nén kịp chạy → cắt cứng mỗi lượt."""
    monkeypatch.setenv("ATLS_WINDOW_TOKENS", "10000")
    monkeypatch.setenv("ATLS_COMPACT_TRIGGER", "20000")
    cfg = env()
    assert cfg.compact_trigger < cfg.window_tokens
