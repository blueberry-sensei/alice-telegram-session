"""
Đếm token.

`tiktoken` nếu cài được, không thì heuristic theo ký tự. Heuristic **cố ý ước lượng
cao hơn thực tế**: đếm thiếu thì cửa sổ tràn và lượt chat chết; đếm thừa thì chỉ nén
sớm hơn vài tin. Sai về phía an toàn.

Hệ số 3.6 ký tự/token đo trên tiếng Việt có dấu với bộ tokenize BPE — tiếng Việt tốn
token hơn tiếng Anh đáng kể (dấu thanh tách thành byte riêng), nên dùng hệ số 4 của
tiếng Anh là đếm thiếu ~10%.
"""

from __future__ import annotations

import functools

_CHARS_PER_TOKEN = 3.6


@functools.lru_cache(maxsize=1)
def _encoder():
    try:
        import tiktoken  # type: ignore
        return tiktoken.get_encoding("cl100k_base")
    except Exception:  # noqa: BLE001 — thiếu package, thiếu mạng lúc tải bảng mã, v.v.
        return None


def count_tokens(text: str) -> int:
    if not text:
        return 0
    enc = _encoder()
    if enc is not None:
        try:
            return len(enc.encode(text))
        except Exception:  # noqa: BLE001
            pass
    return int(len(text) / _CHARS_PER_TOKEN) + 1


def truncate_to_tokens(text: str, budget: int) -> str:
    """Cắt cứng về đúng budget. Đây là lưới an toàn cuối cùng khi compaction lỗi —
    không bao giờ nên là đường đi bình thường."""
    if budget <= 0:
        return ""
    if count_tokens(text) <= budget:
        return text
    enc = _encoder()
    if enc is not None:
        try:
            return enc.decode(enc.encode(text)[-budget:])
        except Exception:  # noqa: BLE001
            pass
    # `count_tokens` làm tròn LÊN (`int(len/3.6) + 1`). Cắt đúng `budget * 3.6` ký tự
    # thì đếm lại ra `budget + 1` — vượt đúng một token, và ở biên cửa sổ thì một
    # token cũng là tràn. Trừ đi một ký tự để hai hàm khớp nhau.
    return text[-(int(budget * _CHARS_PER_TOKEN) - 1):]
