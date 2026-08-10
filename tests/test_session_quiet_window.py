"""Xoay session né khung giờ thị trường chạy mạnh."""

from __future__ import annotations

import time

import pytest

from atls.session.manager import _hour_in_window, parse_quiet_window


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("9-13", (9, 13)),
        ("22-4", (22, 4)),
        ("", None),
        ("9", None),
        ("9-13-15", None),
        ("chin-muoi", None),
        ("9-99", None),
        ("-1-5", None),
    ],
)
def test_parse_quiet_window(raw, expected):
    assert parse_quiet_window(raw) == expected


@pytest.mark.parametrize("hour,inside", [(8, False), (9, True), (12, True), (13, False)])
def test_window_is_half_open(hour, inside):
    assert _hour_in_window(hour, (9, 13)) is inside


@pytest.mark.parametrize("hour,inside", [(23, True), (2, True), (4, False), (12, False)])
def test_window_may_cross_midnight(hour, inside):
    assert _hour_in_window(hour, (22, 4)) is inside


class Row:
    def __init__(self, *, created_at, last_used_at, agent="claude", turns=1, id="s1", started=True):
        self.created_at = created_at
        self.last_used_at = last_used_at
        self.agent = agent
        self.turns = turns
        self.id = id
        self.started = started


def manager(quiet, *, max_age=100, defer_ceiling=None):
    from atls.session.manager import SessionManager

    return SessionManager(
        store=None, max_age=max_age, idle=50,
        quiet_window=quiet, defer_ceiling=defer_ceiling,
    )


def test_rotation_is_deferred_outside_the_quiet_window(monkeypatch):
    now = time.time()
    monkeypatch.setattr(time, "localtime", lambda t=None: time.struct_time(
        (2026, 8, 10, 20, 0, 0, 0, 222, 0)  # 20:00 - phiên New York
    ))
    row = Row(created_at=now - 150, last_used_at=now)
    assert manager((9, 13))._may_rotate_now(row, "max_age", now) is False


def test_rotation_proceeds_inside_the_quiet_window(monkeypatch):
    now = time.time()
    monkeypatch.setattr(time, "localtime", lambda t=None: time.struct_time(
        (2026, 8, 10, 10, 0, 0, 0, 222, 0)  # 10:00 - chợ vắng
    ))
    row = Row(created_at=now - 150, last_used_at=now)
    assert manager((9, 13))._may_rotate_now(row, "max_age", now) is True


def test_a_session_cannot_defer_forever(monkeypatch):
    """Quá trần hoãn thì xoay bất kể giờ nào - rác context mới là cái hại thật."""
    now = time.time()
    monkeypatch.setattr(time, "localtime", lambda t=None: time.struct_time(
        (2026, 8, 10, 20, 0, 0, 0, 222, 0)
    ))
    row = Row(created_at=now - 500, last_used_at=now)
    assert manager((9, 13), defer_ceiling=200)._may_rotate_now(row, "max_age", now) is True


@pytest.mark.parametrize("reason", ["agent_changed", "resume_failed", "/reset"])
def test_only_expiry_reasons_are_deferred(monkeypatch, reason):
    """Đổi agent hay resume hỏng thì session cũ vô dụng - hoãn là giữ lại đồ hỏng."""
    now = time.time()
    monkeypatch.setattr(time, "localtime", lambda t=None: time.struct_time(
        (2026, 8, 10, 20, 0, 0, 0, 222, 0)
    ))
    row = Row(created_at=now - 10, last_used_at=now)
    assert manager((9, 13))._may_rotate_now(row, reason, now) is True


def test_no_window_configured_means_no_restriction(monkeypatch):
    now = time.time()
    row = Row(created_at=now - 150, last_used_at=now)
    assert manager(None)._may_rotate_now(row, "max_age", now) is True
