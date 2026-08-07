"""Router — cổng "việc này của mình không".

Sai ở đây có hai kiểu, và cả hai đều giết sản phẩm: quá rộng thì bot lắm lời và bị
tắt; quá hẹp thì người ta gọi mà nó im.
"""

from __future__ import annotations

from conftest import bot_reply, make_update

from atls.runtime.router import Decision, classify, merge
from atls.telegram.model import parse_update

BOT = "alice_bot"


def route(text: str, **kw):
    return classify(parse_update(make_update(1, text, **kw)), BOT, ("alice ơi",))


def test_chat_rieng_luon_tra_loi():
    r = route("hôm nay sao rồi", chat_type="private", chat_id="7")
    assert r.decision is Decision.ANSWER
    assert r.addressed


def test_group_khong_goi_thi_chi_ghi_nen():
    r = route("ê ông ăn cơm chưa")
    assert r.decision is Decision.BACKGROUND
    assert not r.addressed


def test_mention_thi_tra_loi():
    assert route("@alice_bot check giùm cái feed").decision is Decision.ANSWER


def test_mention_khong_phan_biet_hoa_thuong():
    assert route("@Alice_Bot ơi").decision is Decision.ANSWER


def test_reply_vao_tin_cua_bot_thi_tra_loi():
    assert route("vậy à", reply_to=bot_reply()).decision is Decision.ANSWER


def test_trigger_word():
    assert route("alice ơi cứu em với").decision is Decision.ANSWER


def test_lenh_he_thong_khong_danh_thuc_agent():
    r = route("/status")
    assert r.decision is Decision.COMMAND
    assert r.command == "status"


def test_lenh_co_kem_ten_bot():
    r = route("/reset@alice_bot")
    assert r.decision is Decision.COMMAND and r.command == "reset"


def test_lenh_co_tham_so():
    r = route("/nhớ cái feed hỏng tuần trước")
    assert r.command == "nhớ"
    assert r.args == "cái feed hỏng tuần trước"


def test_lenh_la_trong_chat_rieng_di_toi_agent():
    """Lệnh không có trong SYSTEM_COMMANDS: chat riêng thì rõ ràng là gọi ta."""
    r = route("/deploy staging", chat_type="private", chat_id="7")
    assert r.decision is Decision.ANSWER


def test_lenh_la_khong_ghi_dich_trong_group_thi_khong_danh_thuc_agent():
    """Group thật có nhiều bot. `/deploy` trần rất có thể là của bot khác — đánh thức
    nhầm thì tốn một lượt CLI thật và một câu trả lời chen ngang."""
    assert route("/deploy staging").decision is Decision.BACKGROUND


def test_lenh_la_ghi_dich_ten_minh_thi_di_toi_agent():
    assert route("/deploy@alice_bot staging").decision is Decision.ANSWER


def test_lenh_la_kem_mention_thi_di_toi_agent():
    assert route("/deploy staging @alice_bot").decision is Decision.ANSWER


def test_lenh_gui_cho_bot_khac_bi_bo_qua():
    """`/poll@bot_khac` — người ta đang nói với bot khác trong cùng phòng."""
    r = route("/poll@bot_khac Ăn gì trưa nay?")
    assert r.decision is Decision.BACKGROUND


def test_lenh_he_thong_gui_cho_bot_khac_cung_bi_bo_qua():
    assert route("/status@bot_khac").decision is Decision.BACKGROUND


def test_duong_dan_khong_phai_la_lenh():
    """`/home/user/log.txt` dán vào group không được coi là lệnh `/home/user/log.txt`."""
    r = route("/var/log/nginx/error.log")
    assert r.decision is Decision.BACKGROUND
    assert r.command == ""


def test_reply_vao_tin_cua_bot_khac_thi_khong_tra_loi():
    """Cờ `is_bot` trần là sai trong group nhiều bot: người ta reply bot kia mà ta nhảy
    vào trả lời — đúng kiểu lắm lời khiến bot bị tắt."""
    r = route("vậy à", reply_to=bot_reply(username="bot_khac"))
    assert r.decision is Decision.BACKGROUND


def test_reply_vao_bot_khong_ro_username_thi_van_tra_loi():
    """Telegram không cho biết username (hiếm) → rơi về cờ chung, thà thừa hơn câm."""
    assert route("vậy à", reply_to=bot_reply(username="")).decision is Decision.ANSWER


def test_tin_da_sua_khong_danh_thuc_agent():
    """Telegram cấp update_id mới cho mỗi lần sửa. Để nó đi tiếp thì một người sửa lỗi
    chính tả sẽ nhận câu trả lời thứ hai cho cùng một câu hỏi."""
    r = route("@alice_bot check giùm cái feed", edited=True)
    assert r.decision is Decision.BACKGROUND


def test_tin_cua_bot_bi_bo_qua():
    """Hai bot trả lời nhau là vòng lặp vô tận có thật."""
    assert route("dạ vâng", is_bot=True).decision is Decision.IGNORE


def test_tin_rong_bi_bo_qua():
    assert route("").decision is Decision.IGNORE


def test_doan_viec_dai():
    assert route("@alice_bot kiểm tra giùm cái feed").likely_long
    assert route("@alice_bot chạy deploy đi").likely_long
    assert not route("@alice_bot ok").likely_long


def test_de_bai_dai_coi_la_viec_dai():
    assert route("@alice_bot " + "x" * 500).likely_long


def test_merge_mot_tin_goi_thi_ca_chum_duoc_tra_loi():
    """Người ta gõ '@alice' rồi mới gõ nội dung ở tin sau. Trả lời riêng tin đầu là
    trả lời khi chưa đọc câu hỏi."""
    routes = [
        classify(parse_update(make_update(1, "@alice_bot")), BOT),
        classify(parse_update(make_update(2, "check giùm cái feed")), BOT),
        classify(parse_update(make_update(3, "gấp nha")), BOT),
    ]
    merged = merge(routes)
    assert merged.decision is Decision.ANSWER
    assert merged.addressed


def test_merge_chum_toan_tin_nen_thi_van_la_nen():
    routes = [classify(parse_update(make_update(i, "chuyện riêng")), BOT) for i in (1, 2)]
    assert merge(routes).decision is Decision.BACKGROUND


def test_merge_lay_likely_long_cua_bat_ky_tin_nao():
    routes = [
        classify(parse_update(make_update(1, "@alice_bot ơi")), BOT),
        classify(parse_update(make_update(2, "@alice_bot chạy build giùm")), BOT),
    ]
    assert merge(routes).likely_long
