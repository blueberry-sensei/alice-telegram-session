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


def truncate_to_tokens(text: str, budget: int, *, keep: str = "head") -> str:
    """Cắt cứng về đúng budget. Đây là lưới an toàn cuối cùng khi compaction lỗi —
    không bao giờ nên là đường đi bình thường.

    `keep="head"` giữ phần ĐẦU, `keep="tail"` giữ phần CUỐI. Mặc định là đầu, và đó là
    lựa chọn có hệ quả thật:

      • Một tin nhắn bắt đầu bằng `[Tên]: `. Giữ đuôi là vứt mất tên người nói và vế
        đầu câu hỏi — agent đọc được nửa sau của một câu không biết ai hỏi.
      • Bản tóm tắt được `prompts/compact.md` yêu cầu viết theo thứ tự ưu tiên giảm
        dần (việc đang dở trước, chuyện vặt sau). Giữ đuôi là giữ đúng phần ít giá trị nhất.
    """
    if budget <= 0:
        return ""
    if count_tokens(text) <= budget:
        return text
    enc = _encoder()
    if enc is not None:
        try:
            ids = enc.encode(text)
            return enc.decode(ids[:budget] if keep == "head" else ids[-budget:])
        except Exception:  # noqa: BLE001
            pass
    # `count_tokens` làm tròn LÊN (`int(len/3.6) + 1`). Cắt đúng `budget * 3.6` ký tự
    # thì đếm lại ra `budget + 1` — vượt đúng một token, và ở biên cửa sổ thì một
    # token cũng là tràn. Trừ đi một ký tự để hai hàm khớp nhau.
    chars = int(budget * _CHARS_PER_TOKEN) - 1
    return text[:chars] if keep == "head" else text[-chars:]
