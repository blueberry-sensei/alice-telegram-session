"""Trí nhớ — cửa sổ không bao giờ tràn, nén giữ đúng khoảng.

Đây là phần dễ sai nhất trong cả repo: sai một chút thì hoặc agent tràn context và
chết giữa lượt, hoặc nó quên mất câu vừa được hỏi.
"""

from __future__ import annotations

import pytest
from conftest import add

from atls.memory.compactor import Compactor
from atls.memory.tokens import count_tokens, truncate_to_tokens
from atls.memory.window import build_window, raw_tokens_since_summary
from atls.store import Store


def test_dem_token_uoc_luong_cao_hon_thuc_te():
    """Đếm thiếu = cửa sổ tràn = lượt chat chết. Sai phải nghiêng về phía an toàn."""
    text = "Chào Bệ hạ, hôm nay hệ thống chạy ổn định ạ." * 20
    assert count_tokens(text) >= len(text) / 4.2


def test_truncate_mac_dinh_giu_phan_dau():
    """Mặc định giữ ĐẦU. Hai chỗ gọi đều cần vậy, và cả hai đều có lý do cụ thể:
    tin nhắn mở đầu bằng `[Tên]: `, còn tóm tắt được viết theo thứ tự ưu tiên giảm dần."""
    text = "\n".join(f"dòng {i}" for i in range(500))
    out = truncate_to_tokens(text, 50)
    assert count_tokens(out) <= 50
    assert "dòng 0" in out
    assert "dòng 499" not in out


def test_truncate_giu_phan_cuoi_khi_duoc_yeu_cau():
    text = "\n".join(f"dòng {i}" for i in range(500))
    out = truncate_to_tokens(text, 50, keep="tail")
    assert count_tokens(out) <= 50
    assert "dòng 499" in out


def test_tin_bi_cat_van_giu_ten_nguoi_noi(store: Store, chat: str):
    """Một tin dài hơn cả cửa sổ bị cắt: phần giữ lại PHẢI còn nhãn người nói.

    Giữ đuôi thì agent nhận một mảnh văn bản không đầu không cuối, không biết ai nói và
    không biết đó là câu hỏi hay là log người ta dán vào.
    """
    add(store, chat, "Bệ hạ hỏi: " + "x " * 20_000, update_id=1)
    window = build_window(store, chat, budget=300)
    assert window.messages[0].text.startswith("[Bệ hạ]:")


def test_cua_so_luon_nam_trong_budget(store: Store, chat: str):
    for i in range(400):
        add(store, chat, f"Đây là tin nhắn số {i} với nội dung dài vừa phải để tốn token.",
            update_id=i)
    window = build_window(store, chat, budget=2_000)
    assert window.tokens <= 2_000
    assert window.truncated is True


def test_cua_so_uu_tien_tin_moi_nhat(store: Store, chat: str):
    for i in range(200):
        add(store, chat, f"tin số {i}", update_id=i)
    window = build_window(store, chat, budget=500)
    texts = [m.text for m in window.messages]
    assert "tin số 199" in texts, "tin mới nhất không bao giờ được bỏ"
    assert "tin số 0" not in texts, "tin cũ nhất phải bị bỏ trước"


def test_mot_tin_khong_lo_van_tra_ve_cua_so_khong_rong(store: Store, chat: str):
    """Một tin dài hơn cả budget: phải cắt chính nó, không được trả cửa sổ rỗng."""
    add(store, chat, "x " * 20_000, update_id=1)
    window = build_window(store, chat, budget=300)
    assert window.messages, "cửa sổ rỗng nghĩa là agent không thấy câu hỏi"
    assert window.tokens <= 300


def test_cua_so_bat_dau_tu_sau_summary(store: Store, chat: str):
    ids = [add(store, chat, f"tin {i}", update_id=i) for i in range(50)]
    store.add_summary(chat_id=chat, from_msg_id=ids[0], to_msg_id=ids[29],
                      covered=30, text="30 tin đầu: bàn về feed.", tokens=20)

    window = build_window(store, chat, budget=20_000)
    assert window.summary.startswith("30 tin đầu")
    assert [m.text for m in window.messages] == [f"tin {i}" for i in range(30, 50)]


def test_summary_phinh_to_bi_cat_chu_khong_nuot_tin_tho(store: Store, chat: str):
    """Tóm tắt phải là bản NÉN. Nó phình quá nửa cửa sổ là hỏng — cắt nó, đừng hy sinh
    tin thô, vì tin thô là thứ đang được hỏi."""
    ids = [add(store, chat, f"tin {i}", update_id=i) for i in range(10)]
    store.add_summary(chat_id=chat, from_msg_id=ids[0], to_msg_id=ids[4],
                      covered=5, text="dài dòng " * 5_000, tokens=10_000)

    window = build_window(store, chat, budget=1_000)
    assert window.tokens <= 1_000
    assert window.messages, "cắt tóm tắt chứ không được bỏ hết tin thô"


def test_raw_tokens_dem_dung_phan_ngoai_vung_nen(store: Store, chat: str):
    ids = [add(store, chat, "một câu vừa phải để đếm", update_id=i) for i in range(40)]
    before, _ = raw_tokens_since_summary(store, chat)

    store.add_summary(chat_id=chat, from_msg_id=ids[0], to_msg_id=ids[19],
                      covered=20, text="…", tokens=10)
    after, count = raw_tokens_since_summary(store, chat)

    assert count == 20
    assert after < before


def test_tin_hieu_kich_hoat_nen_khong_bao_hoa_o_tran_doc(store: Store, chat: str):
    """Đếm vùng chưa nén bằng SQL, KHÔNG qua truy vấn có LIMIT.

    Nếu nó đếm qua một truy vấn `LIMIT 2000` thì đúng lúc hệ thống tụt lại xa nhất
    (nén hỏng nhiều lượt liền) lại là lúc chỉ số ngừng tăng.
    """
    for i in range(2_100):
        add(store, chat, "x", update_id=i)
    _, count = raw_tokens_since_summary(store, chat)
    assert count == 2_100


def test_cua_so_giu_tin_moi_nhat_ke_ca_khi_vung_chua_nen_vuot_tran_doc(
    store: Store, chat: str, monkeypatch
):
    """Vùng chưa nén lớn hơn trần đọc → vẫn phải thấy câu hỏi mới nhất.

    Đây là hồi quy cho một lỗi câm: trần đọc từng đặt trên `ORDER BY id ASC`, nên khi
    nén hỏng nhiều lượt liền (agent CLI chết) thì cửa sổ được dựng từ khúc CŨ NHẤT và
    câu người ta vừa gõ không có trong prompt. Agent trả lời câu hỏi của hôm kia, rất
    tự tin, và không có gì trong log nói rằng chuyện đó vừa xảy ra.
    """
    from atls.memory import window as mod
    monkeypatch.setattr(mod, "_MAX_ROWS", 10)

    for i in range(40):
        add(store, chat, f"tin số {i}", update_id=i)

    texts = [m.text for m in build_window(store, chat, budget=20_000).messages]
    assert "tin số 39" in texts, "câu hỏi mới nhất KHÔNG BAO GIỜ được rơi khỏi cửa sổ"
    assert "tin số 0" not in texts


# ── compactor ────────────────────────────────────────────────────────────────

def _compactor(store: Store, reply: str = "BẢN TÓM TẮT", **kw) -> Compactor:
    async def summarize(prompt: str) -> str:
        return reply
    return Compactor(store, summarize, trigger_tokens=kw.get("trigger", 100),
                     keep_raw=kw.get("keep_raw", 5))


async def test_nen_giu_lai_dung_so_tin_gan_nhat(store: Store, chat: str):
    for i in range(60):
        add(store, chat, f"Tin nhắn số {i} với nội dung đủ dài để vượt ngưỡng nén.",
            update_id=i)

    comp = _compactor(store, keep_raw=5)
    summary = await comp.maybe_compact(chat)

    assert summary is not None
    window = build_window(store, chat, budget=20_000)
    assert len(window.messages) == 5, "đúng KEEP_RAW tin cuối phải còn nguyên văn"
    assert window.messages[-1].text.startswith("Tin nhắn số 59")


async def test_nen_lien_tiep_khong_chong_lan(store: Store, chat: str):
    """Running summary: bản mới phủ TIẾP khoảng của bản cũ, không nhân đôi."""
    comp = _compactor(store, keep_raw=5)
    for batch in range(3):
        for i in range(40):
            add(store, chat, f"batch {batch} tin {i} nội dung dài vừa đủ để tốn token.",
                update_id=batch * 100 + i)
        await comp.maybe_compact(chat)

    latest = store.latest_summary(chat)
    assert latest is not None
    # Đợt 1: 40 tin, chừa 5 → nén 35.
    # Đợt 2: 5 tin còn lại + 40 tin mới = 45, chừa 5 → nén 40. (75)
    # Đợt 3: y hệt → nén 40. (115)
    assert latest.covered == 115
    # Khoảng phủ phải LIỀN MẠCH tới tin thứ 116, không chồng lấn, không hở.
    assert latest.to_msg_id == 115


async def test_nen_hong_khong_lam_chet_luot(store: Store, chat: str):
    """Agent trả rỗng / nổ giữa chừng: phải trả None, không được ném ra ngoài."""
    async def broken(prompt: str) -> str:
        raise RuntimeError("CLI chết")

    for i in range(60):
        add(store, chat, f"tin dài số {i} để vượt ngưỡng nén nhé", update_id=i)

    comp = Compactor(store, broken, trigger_tokens=100, keep_raw=5)
    assert await comp.maybe_compact(chat) is None
    # Cửa sổ vẫn dựng được, chỉ là cắt cứng.
    assert build_window(store, chat, budget=500).tokens <= 500


async def test_nen_tien_len_duoc_ke_ca_khi_vung_chua_nen_rat_lon(store: Store, chat: str):
    """Nén hỏng nhiều lượt → vùng chưa nén phình qua một lô. Mỗi lượt vẫn phải TIẾN LÊN.

    Và không được để hở: `keep_raw` tin cuối phải tính trên tổng số tin thật, không
    phải trên độ dài của lô vừa đọc — tính nhầm thì có một dải tin ở giữa vĩnh viễn
    không được nén mà cũng không còn nằm trong cửa sổ.
    """
    from atls.memory import compactor as mod
    original = mod.COMPACT_BATCH
    mod.COMPACT_BATCH = 20
    try:
        for i in range(100):
            add(store, chat, f"tin số {i} đủ dài để vượt ngưỡng nén", update_id=i)
        comp = _compactor(store, keep_raw=5)

        first = await comp.maybe_compact(chat)
        assert first is not None and first.covered == 20

        second = await comp.maybe_compact(chat)
        assert second is not None
        # Liền mạch: bản sau bắt đầu đúng chỗ bản trước dừng, không hở không chồng.
        assert second.to_msg_id == first.to_msg_id + 20
        assert second.covered == 40
    finally:
        mod.COMPACT_BATCH = original


async def test_khong_nen_khi_chua_vuot_nguong(store: Store, chat: str):
    for i in range(3):
        add(store, chat, "ngắn", update_id=i)
    comp = _compactor(store, trigger=100_000)
    assert await comp.maybe_compact(chat) is None
    assert store.latest_summary(chat) is None
