"""Store — tính idempotent và archive vĩnh viễn.

Hai tính chất này là nền của cả hệ thống: mất một trong hai thì hoặc agent trả lời
hai lần cùng một câu, hoặc nó quên chuyện tuần trước.
"""

from __future__ import annotations

from conftest import add

from atls.store import Store


def test_update_id_trung_bi_nuot(store: Store, chat: str):
    """Telegram gửi lại update khi ta trả HTTP chậm. Lần hai phải trả None."""
    first = add(store, chat, "xin chào", update_id=1000)
    second = add(store, chat, "xin chào", update_id=1000)

    assert first is not None
    assert second is None, "update_id trùng phải bị nuốt, nếu không agent trả lời hai lần"
    assert store.count_messages(chat) == 1


def test_tin_khong_co_update_id_khong_dung_nhau(store: Store, chat: str):
    """Tin do chính agent gửi ra không có update_id — nhiều tin NULL phải cùng tồn tại."""
    for i in range(5):
        assert add(store, chat, f"trả lời {i}", role="agent", sender="Alice") is not None
    assert store.count_messages(chat) == 5


def test_archive_khong_bao_gio_mat_tin(store: Store, chat: str):
    for i in range(120):
        add(store, chat, f"tin số {i}", update_id=i)
    # Nén không đụng tới messages — chỉ ghi thêm một row summaries.
    store.add_summary(chat_id=chat, from_msg_id=1, to_msg_id=100,
                      covered=100, text="tóm tắt", tokens=50)
    assert store.count_messages(chat) == 120


def test_tim_toan_van_tieng_viet_co_dau(store: Store, chat: str):
    add(store, chat, "cái feed của Godine hỏng rồi", update_id=1)
    add(store, chat, "hôm nay trời đẹp", update_id=2)

    assert len(store.search("feed", chat_id=chat)) == 1
    # remove_diacritics=2 → gõ không dấu vẫn tìm ra.
    assert len(store.search("godine", chat_id=chat)) == 1
    assert len(store.search("khong ton tai", chat_id=chat)) == 0


def test_tim_kiem_khong_vo_vi_ky_tu_dac_biet(store: Store, chat: str):
    """Chuỗi người dùng gõ có thể chứa cú pháp FTS5. Không được ném exception."""
    add(store, chat, "lỗi ở dòng 42", update_id=1)
    for query in ['"', "AND OR", "a*b", "NEAR(", ")"]:
        store.search(query, chat_id=chat)  # chỉ cần không nổ


def test_messages_after_tra_ve_theo_thu_tu_cu_toi_moi(store: Store, chat: str):
    ids = [add(store, chat, f"tin {i}", update_id=i) for i in range(10)]
    rest = store.messages_after(chat, ids[4])
    assert [m.text for m in rest] == [f"tin {i}" for i in range(5, 10)]


def test_recent_messages_lay_moi_nhat_nhung_tra_ve_dung_thu_tu(store: Store, chat: str):
    for i in range(20):
        add(store, chat, f"tin {i}", update_id=i)
    recent = store.recent_messages(chat, 3)
    assert [m.text for m in recent] == ["tin 17", "tin 18", "tin 19"]


def test_gate_mo_va_dong(store: Store, chat: str):
    store.open_gate(chat, "login", "đăng nhập ChatGPT")
    store.open_gate(chat, "confirm", "xác nhận xoá")
    assert len(store.pending_gates(chat)) == 2

    assert store.resolve_gates(chat, "Bệ hạ") == 2
    assert store.pending_gates(chat) == []
