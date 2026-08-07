"""Tra archive khi trả lời — chỗ hở của trí nhớ dài ngày.

Cửa sổ hội thoại chỉ giữ [một bản tóm tắt] + [N tin thô gần nhất], và bản tóm tắt bị
VIẾT ĐÈ mỗi lần nén. Sau vài ngày, chi tiết của hôm kia là bản tóm tắt của bản tóm tắt
— mất dần mà không có dấu hiệu nào. Dữ liệu thì vẫn nằm nguyên trong archive.

Mỗi test dưới đây khoá một tính chất mà thiếu nó thì tính năng này **có hại hơn không
có**: kéo về nhiễu, kéo về thứ đã có sẵn, hoặc phá trần cửa sổ.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from atls.memory.recall import MIN_TERMS, recall, render
from atls.memory.tokens import count_tokens
from atls.memory.window import build_window
from atls.store import Store

CHAT = "1"


@pytest.fixture()
def store(tmp_path: Path) -> Store:
    s = Store(tmp_path / "recall.db")
    s.upsert_chat(CHAT, kind="private", title="t")
    yield s
    s.close()


def add(store: Store, text: str, *, role: str = "human", update_id: int | None = None) -> int:
    row_id = store.add_message(
        update_id=update_id,
        chat_id=CHAT,
        tg_message_id=None,
        ts=time.time(),
        role=role,
        sender_id="u",
        sender_name="Be Ha",
        text=text,
        tokens=count_tokens(text),
    )
    return row_id or 0


# --- lọc nhiễu ---------------------------------------------------------------

@pytest.mark.parametrize("junk", ["um", "ok", "vâng ạ", "dạ", "thế nào", ""])
def test_a_meaningless_message_recalls_nothing(store: Store, junk: str):
    """"um" khớp gần như mọi thứ. Kéo về nhiễu tệ hơn không kéo gì.

    Agent không có cách nào biết là nó đang đọc thứ không liên quan, nên nó sẽ tin.
    """
    for i in range(30):
        add(store, f"ghi chu so {i} ve chien luoc giao dich", update_id=i)
    assert recall(store, CHAT, junk) == []


def test_one_keyword_is_not_enough_to_narrow_anything(store: Store):
    add(store, "chien luoc dang chay la bam xu huong")
    assert MIN_TERMS >= 2
    assert recall(store, CHAT, "chien") == []


# --- tìm được thứ đã rơi khỏi cửa sổ -----------------------------------------

def test_it_finds_a_fact_that_fell_out_of_the_window(store: Store):
    """Kịch bản "chat một hai ngày": chuyện cũ vẫn phải lấy lại được."""
    buried = add(store, "don bay toi da cho ZENUSDT la muoi lan, dung vuot")
    for i in range(200):
        add(store, f"tin nhan lap so {i} khong lien quan", update_id=1000 + i)

    hits = recall(store, CHAT, "don bay ZENUSDT toi da bao nhieu")
    assert buried in [m.id for m in hits]


def test_messages_already_in_the_window_are_not_repeated(store: Store):
    """Đính lại thứ agent đang đọc là trả token để nói cùng một câu hai lần."""
    first = add(store, "muc tieu la hai nghin USDT khong co deadline")
    assert recall(store, CHAT, "muc tieu hai nghin USDT") != []
    assert recall(store, CHAT, "muc tieu hai nghin USDT", exclude_ids={first}) == []


# --- không được phá trần -----------------------------------------------------

def test_recall_never_pushes_the_window_over_budget(store: Store):
    for i in range(400):
        add(store, f"ghi chu chien luoc giao dich rat dai so {i} " + "x" * 200, update_id=i)

    budget = 2_000
    window = build_window(store, CHAT, budget, question="ghi chu chien luoc giao dich")
    assert window.tokens <= budget


def test_a_tiny_spare_budget_recalls_nothing_rather_than_overflowing(store: Store):
    for i in range(50):
        add(store, "chien luoc giao dich " + "y" * 400, update_id=i)
    window = build_window(store, CHAT, 200, question="chien luoc giao dich")
    assert window.recalled == []


# --- hình dạng đầu ra --------------------------------------------------------

def test_the_block_is_absent_when_there_is_nothing_to_say(store: Store):
    assert render([]) == ""
    add(store, "khong lien quan gi")
    window = build_window(store, CHAT, 20_000, question="um")
    assert "TIN CŨ" not in window.render()


def test_recalled_messages_are_labelled_as_archive_not_as_recent(store: Store):
    """Không nhãn thì agent đọc tin ba hôm trước như tin vừa nhắn."""
    add(store, "quy tac cu ve don bay va rui ro moi lenh")
    for i in range(200):
        add(store, f"chen giua {i}", update_id=2000 + i)
    window = build_window(store, CHAT, 20_000, question="quy tac don bay rui ro")
    if window.recalled:
        assert "archive" in window.render()


def test_compaction_path_does_not_drag_the_archive_back_in(store: Store):
    """Compactor dựng cửa sổ để NÉN, không để trả lời — nó không được kéo thêm gì."""
    add(store, "chien luoc giao dich cu")
    window = build_window(store, CHAT, 20_000)  # không truyền question
    assert window.recalled == []


def test_a_broken_archive_search_does_not_kill_the_turn(store: Store, monkeypatch):
    """Trí nhớ hỏng phải làm câu trả lời nghèo đi, không làm nó biến mất."""
    add(store, "chien luoc giao dich")

    def boom(*a, **k):
        raise RuntimeError("fts hong")

    monkeypatch.setattr(store, "search", boom)
    assert recall(store, CHAT, "chien luoc giao dich") == []
