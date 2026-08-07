"""Lớp Telegram — chuẩn hoá update, cắt tin, chuyển Markdown."""

from __future__ import annotations

from conftest import bot_reply, make_update

from atls.telegram.api import MAX_CHARS, md_to_html, split_message
from atls.telegram.model import parse_update


def test_parse_tin_thuong():
    inc = parse_update(make_update(5, "chào em"))
    assert inc is not None
    assert inc.chat_kind == "supergroup"
    assert inc.text == "chào em"
    assert inc.sender_name == "Bệ hạ"


def test_parse_channel_post_lay_ten_channel_lam_nguoi_gui():
    """Channel post không có `from`. Không xử lý thì mọi dòng archive mang tên rỗng."""
    upd = {
        "update_id": 9,
        "channel_post": {
            "message_id": 3, "date": 0,
            "chat": {"id": -100999, "type": "channel", "title": "Thông báo"},
            "text": "bảo trì lúc 2h sáng",
        },
    }
    inc = parse_update(upd)
    assert inc is not None
    assert inc.chat_kind == "channel"
    assert inc.sender_name == "Thông báo"


def test_parse_caption_duoc_coi_la_text():
    upd = make_update(1, "")
    upd["message"].pop("text")
    upd["message"]["caption"] = "cái ảnh này nè"
    upd["message"]["photo"] = [
        {"file_id": "nhỏ", "file_size": 100},
        {"file_id": "to", "file_size": 9000},
    ]
    inc = parse_update(upd)
    assert inc.text == "cái ảnh này nè"
    # `photo` là mảng tăng dần — lấy phần tử đầu là tải về thumbnail 90px.
    assert inc.media["file_id"] == "to"


def test_parse_reply_giu_noi_dung_duoc_reply():
    inc = parse_update(make_update(1, "vậy à", reply_to=bot_reply()))
    assert inc.reply_to_is_bot
    assert inc.reply_to_text == "dạ"


def test_parse_update_khong_phai_tin_nhan_tra_none():
    assert parse_update({"update_id": 1, "callback_query": {}}) is None


def test_tach_lenh():
    inc = parse_update(make_update(1, "/nhớ feed hỏng"))
    assert inc.command() == ("nhớ", "feed hỏng")

    inc = parse_update(make_update(2, "/status@alice_bot"))
    assert inc.command() == ("status", "")


# ── cắt tin ──────────────────────────────────────────────────────────────────

def test_tin_ngan_khong_bi_cat():
    assert split_message("ngắn thôi") == ["ngắn thôi"]


def test_moi_manh_deu_duoi_tran_4096():
    """Vượt trần là API trả 400 và câu trả lời biến mất hoàn toàn."""
    text = "\n\n".join(f"Đoạn số {i}. " + "chữ " * 200 for i in range(40))
    chunks = split_message(text)
    assert len(chunks) > 1
    assert all(len(c) <= MAX_CHARS for c in chunks)


def test_cat_uu_tien_ranh_gioi_doan():
    """Cắt ở ranh giới đoạn khi ranh giới đó nằm trong nửa sau cửa sổ.

    Nửa đầu bị loại có chủ đích: một ranh giới ở ký tự thứ 8 mà cắt theo thì ra một
    mảnh 8 ký tự và một mảnh 5000 ký tự vẫn phải cắt tiếp — tệ hơn là cắt thẳng.
    """
    first = "x" * 3000
    text = first + "\n\n" + "y" * 3000
    assert split_message(text)[0] == first


def test_ranh_gioi_qua_som_thi_cat_thang():
    text = "ngắn\n\n" + "x" * 5000
    chunks = split_message(text)
    assert len(chunks[0]) > 4000, "không được sinh mảnh tí hon rồi vẫn phải cắt tiếp"


def test_khong_mat_chu_khi_cat():
    text = "".join(f"{i} " for i in range(4000))
    assert "".join(split_message(text)).replace(" ", "") == text.replace(" ", "")


# ── Markdown → HTML ──────────────────────────────────────────────────────────

def test_escape_truoc_khi_dung_the():
    """Một dấu `<` giữa câu không được làm hỏng cả tin."""
    assert md_to_html("if a < b") == "if a &lt; b"


def test_dam_nghieng_code():
    assert md_to_html("**đậm**") == "<b>đậm</b>"
    assert md_to_html("*nghiêng*") == "<i>nghiêng</i>"
    assert md_to_html("`code`") == "<code>code</code>"


def test_khoi_code():
    assert "<pre>" in md_to_html("```python\nprint(1)\n```")


def test_link():
    assert md_to_html("[Alice](https://x.dev)") == '<a href="https://x.dev">Alice</a>'


def test_heading_thanh_in_dam():
    """Telegram không có thẻ heading — in đậm là cách gần nhất."""
    assert md_to_html("## Tiêu đề") == "<b>Tiêu đề</b>"
