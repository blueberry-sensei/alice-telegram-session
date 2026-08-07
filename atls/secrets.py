"""
Che secret trước khi ghi xuống đĩa và trước khi đưa vào prompt.

Vì sao phải có: lịch sử chat được lưu VĨNH VIỄN. Một token dán nhầm vào group sẽ
nằm trong `atls.db` mãi mãi, và nếu bản tóm tắt được đẩy sang `knowledge/` thì nó
còn vào cả lịch sử git — thứ không rút lại được.

Che chứ KHÔNG xoá tin: người dùng vẫn cần agent nhớ "vừa có ai đó đổi webhook",
chỉ là không được nhớ kèm giá trị.
"""

from __future__ import annotations

import re

MASK = "«đã che»"

# Thứ tự quan trọng: mẫu cụ thể chạy trước mẫu chung, nếu không mẫu chung nuốt mất
# phần đặc trưng và ta mất khả năng nói "cái vừa che là bot token".
_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    # Telegram bot token: 8-10 chữ số : 35 ký tự base64url
    ("telegram-token", re.compile(r"\b\d{8,10}:[A-Za-z0-9_-]{30,}\b")),
    # OpenAI / Anthropic / GitHub / Slack.
    #
    # `anthropic-key` PHẢI đứng trước `api-key`: `sk-ant-…` khớp cả hai, và mẫu chạy
    # trước là mẫu thắng. Đảo thứ tự thì luật anthropic thành code chết và mọi khoá
    # Anthropic bị dán nhãn "api-key" — vẫn che được, nhưng ta mất khả năng nói đúng
    # thứ vừa che là gì, mà đó là điểm duy nhất của việc gắn nhãn.
    ("anthropic-key", re.compile(r"\bsk-ant-[A-Za-z0-9_-]{16,}\b")),
    ("api-key", re.compile(r"\b(?:sk|pk|rk)-[A-Za-z0-9_-]{16,}\b")),
    ("github-token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}\b")),
    ("slack-token", re.compile(r"\bxox[abposr]-[A-Za-z0-9-]{10,}\b")),
    ("google-key", re.compile(r"\bAIza[A-Za-z0-9_-]{30,}\b")),
    ("aws-key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    # JWT
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b")),
    # PEM block
    ("private-key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.S)),
    # `PASSWORD=...`, `DB_PASSWORD=...`, `api_key: "..."`, `token = ...`
    #
    # Tiền tố `[\w.]*` là bắt buộc và từng bị bỏ sót: `\bpassword` KHÔNG khớp
    # `DB_PASSWORD`, vì giữa "B" và "P" không có ranh giới từ. Mà tên biến thật ngoài
    # đời gần như luôn có tiền tố.
    (
        "assignment",
        re.compile(
            r"(?i)([\w.]*(?:password|passwd|secret|token|api[_-]?key|access[_-]?key|"
            r"client[_-]?secret))\s*[:=]\s*[\"']?([^\s\"',;]{6,})"
        ),
    ),
)


def redact(text: str) -> str:
    """Trả về `text` với mọi secret nhận diện được thay bằng nhãn có tên loại."""
    if not text:
        return text
    out = text
    for label, pattern in _PATTERNS:
        if label == "assignment":
            # Giữ lại TÊN biến — "có người đổi DB_PASSWORD" là thông tin hữu ích,
            # giá trị thì không.
            out = pattern.sub(lambda m: f"{m.group(1)}={MASK}", out)
        else:
            out = pattern.sub(f"{MASK}:{label}", out)
    return out


def looks_sensitive(text: str) -> bool:
    """Có secret không? Dùng để cảnh báo người gửi, không dùng để chặn."""
    return redact(text) != text
