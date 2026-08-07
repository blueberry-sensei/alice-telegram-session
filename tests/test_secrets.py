"""Che secret.

Vì sao đáng có test riêng: lịch sử chat lưu VĨNH VIỄN. Một token lọt vào `atls.db`
nằm đó mãi mãi, và nếu bản tóm tắt được đẩy sang `knowledge/` thì nó vào cả lịch sử
git — thứ không rút lại được như một tin nhắn đã xoá.

⚠️ Mọi chuỗi fixture dưới đây được GHÉP LÚC CHẠY chứ không viết thẳng vào file.
Chúng đều là chuỗi giả, nhưng secret scanner (GitHub push protection) không phân biệt
được giả với thật — viết thẳng là chặn cả lần push. Ghép ở runtime thì file nguồn
không chứa chuỗi khớp mẫu, mà test vẫn kiểm trên chuỗi đầy đủ y hệt.
"""

from __future__ import annotations

import pytest

from atls.secrets import MASK, looks_sensitive, redact

FAKE_KEYS = {
    "telegram-token": "8123456789" ":" "AA" + "Hkhong_phai_token_that_1234567890abc",
    "anthropic": "sk-" "ant-" + "api03-abcdefghijklmnopqrstuvwxyz012345",
    "openai": "sk-" "proj-" + "abcdefghijklmnopqrstuvwxyz0123",
    "github": "gh" "p_" + "abcdefghijklmnopqrstuvwxyz0123456789",
    "slack": "xo" "xb-" + "1234567890-abcdefghijklmnop",
    "google": "AI" "za" + "SyAbCdEfGhIjKlMnOpQrStUvWxYz0123456",
    "aws": "AK" "IA" + "IOSFODNN7EXAMPLE",
}


@pytest.mark.parametrize("label", sorted(FAKE_KEYS))
def test_che_cac_dinh_dang_key_pho_bien(label: str):
    raw = FAKE_KEYS[label]
    out = redact(f"key của tôi là {raw} nhé")
    assert raw not in out, f"{label} không bị che"
    assert MASK in out


def test_giu_ten_bien_bo_gia_tri():
    """"Có người đổi DB_PASSWORD" là thông tin hữu ích; giá trị thì không.

    Tiền tố quan trọng: `\\bpassword` KHÔNG khớp `DB_PASSWORD` vì giữa "B" và "P"
    không có ranh giới từ — mà tên biến thật ngoài đời gần như luôn có tiền tố.
    """
    for name in ("PASSWORD", "DB_PASSWORD", "api_key", "CLIENT_SECRET"):
        out = redact(f"{name}=gia_tri_rat_bi_mat_123")
        assert "gia_tri_rat_bi_mat_123" not in out, f"{name} không bị che"
        assert name.split("_")[-1].lower() in out.lower(), "phải giữ lại tên biến"


def test_che_jwt():
    jwt = "ey" "JhbGciOiJIUzI1NiJ9" + ".eyJzdWIiOiIxMjM0NTY3ODkwIn0.abcdefghijklmnop"
    assert jwt not in redact(jwt)


def test_che_private_key_nhieu_dong():
    pem = (
        "-----BEGIN RSA PRIVATE KEY-----\n"
        "MIIkhongphaikhoathat\nabcdef\n"
        "-----END RSA PRIVATE KEY-----"
    )
    assert "MIIkhongphaikhoathat" not in redact(pem)


def test_che_ca_khi_nam_giua_cau_dai():
    """Secret hiếm khi đứng một mình — nó nằm giữa một câu người ta gõ vội."""
    raw = FAKE_KEYS["github"]
    text = f"ê ông ơi cái token {raw} nó hết hạn rồi hay sao ấy, check giùm"
    out = redact(text)
    assert raw not in out
    assert "hết hạn rồi hay sao" in out, "phần văn bản thường phải còn nguyên"


def test_van_ban_thuong_khong_bi_dung_toi():
    text = "Dạ Bệ hạ, feed hôm nay 1234 dòng, chạy lúc 10:00 ạ."
    assert redact(text) == text
    assert not looks_sensitive(text)


def test_redact_chuoi_rong():
    assert redact("") == ""


def test_looks_sensitive_bat_duoc_key():
    assert looks_sensitive(f"token: {FAKE_KEYS['slack']}")
