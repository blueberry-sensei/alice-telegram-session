"""
Tra lại archive trước khi trả lời — chữa chỗ hở lớn nhất của trí nhớ dài ngày.

## Vấn đề nó sinh ra để chữa

Cửa sổ hội thoại = [một bản tóm tắt] + [N tin thô gần nhất]. Bản tóm tắt là **một
row duy nhất bị VIẾT ĐÈ mỗi lần nén**: lần nén sau tóm tắt lại chính bản tóm tắt
trước. Sau một hai ngày, chuyện hôm kia là bản tóm tắt của bản tóm tắt của bản tóm
tắt — tam sao thất bản, và mất dần một cách **không nhìn thấy được**. Agent vẫn trả
lời trôi chảy; nó chỉ trả lời sai.

Nghịch lý là dữ liệu **chưa hề mất**: `messages` là archive vĩnh viễn và có sẵn chỉ
mục toàn văn. Cái thiếu là một đường để agent chạm vào nó. `/recall` có tồn tại nhưng
là **lệnh người dùng gõ** — runtime tự trả lời, không đánh thức agent.

## Vì sao tra TRƯỚC khi trả lời, không dùng chỉ thị

Chỉ thị (`[[...]]`) chạy **sau** khi agent đã viết xong câu trả lời, nên không kịp
giúp gì cho chính lượt đó. Muốn dùng chỉ thị thì phải chạy agent hai lần: hỏi, tra,
rồi hỏi lại — gấp đôi độ trễ và chi phí cho mọi lượt, kể cả những lượt không cần nhớ
gì. Tra trước rồi đính vào cửa sổ tốn đúng một truy vấn SQLite.

## Ba thứ dễ làm hỏng, đã chặn sẵn

1. **Truy vấn rác kéo về nhiễu.** Người ta nhắn "um", "ok", "vâng" suốt. Tìm toàn văn
   với những từ đó khớp gần như mọi thứ, và cửa sổ sẽ đầy tin ngẫu nhiên — tệ hơn là
   không có gì, vì agent sẽ tin vào chúng. Có sàn độ dài + danh sách từ vô nghĩa.
2. **Lặp lại thứ đã có.** Tin nằm trong vùng thô của cửa sổ mà bị đính thêm lần nữa là
   đốt token để nói cùng một câu hai lần.
3. **Phình cửa sổ.** Khối tra cứu có trần riêng và **nằm trong** hạn mức chung, không
   cộng thêm vào. Cửa sổ vượt trần là thứ mà cả tầng memory này tồn tại để chặn.
"""

from __future__ import annotations

import re

from atls import log
from atls.memory.tokens import count_tokens
from atls.store import StoredMessage, Store

_log = log.get("memory.recall")

#: Từ ngắn hơn thế này không mang thông tin để tìm.
MIN_TERM_LEN = 4

#: Truy vấn phải còn lại ít nhất ngần này từ có nghĩa, nếu không thì bỏ qua hẳn.
#: Một từ đơn như "lệnh" khớp hàng trăm tin và không thu hẹp được gì.
MIN_TERMS = 2

#: Khối tra cứu tối đa chiếm ngần này token trong cửa sổ.
RECALL_BUDGET = 1_200

#: Lấy tối đa ngần này tin cũ.
MAX_HITS = 6

#: Từ hay gặp trong tiếng Việt đời thường, không thu hẹp được gì khi tìm.
#: Cố ý ngắn: danh sách dài sẽ âm thầm loại mất từ khoá thật.
STOPWORDS = frozenset(
    """
    được người những nhưng không phải cũng cho các với này thì rằng vào ra
    lại nữa nhé ạ dạ vâng ừm okay được rồi đang sẽ đã là của và hay hoặc
    thế nào sao vậy bao nhiêu bây giờ hôm nay
    """.split()
)


def _terms(text: str) -> list[str]:
    """Từ khoá đáng đem đi tìm, đã bỏ nhiễu."""
    words = re.findall(r"\w+", text.lower(), flags=re.UNICODE)
    seen: list[str] = []
    for w in words:
        if len(w) < MIN_TERM_LEN or w in STOPWORDS or w.isdigit():
            continue
        if w not in seen:
            seen.append(w)
    return seen


def recall(
    store: Store,
    chat_id: str,
    question: str,
    *,
    exclude_ids: set[int] | None = None,
    budget: int = RECALL_BUDGET,
) -> list[StoredMessage]:
    """Tin cũ có thể liên quan tới câu vừa hỏi. Rỗng là kết quả hợp lệ.

    Rỗng nhiều hơn là ít: đính nhầm ngữ cảnh còn tệ hơn không đính gì, vì agent
    không có cách nào biết là nó đang đọc thứ không liên quan.
    """
    terms = _terms(question)
    if len(terms) < MIN_TERMS:
        return []

    try:
        # Tìm rộng rồi lọc, vì phần lớn kết quả đầu sẽ nằm sẵn trong cửa sổ thô.
        hits = store.search(
            " ".join(terms), chat_id=chat_id, limit=MAX_HITS * 5, match_all=False
        )
    except Exception as exc:  # noqa: BLE001 — trí nhớ hỏng không được giết một lượt chat
        _log.warning("tra archive that bai (%s) — bo qua luot nay", exc)
        return []

    skip = exclude_ids or set()
    picked: list[StoredMessage] = []
    used = 0
    for m in hits:
        if m.id in skip or not m.text.strip():
            continue
        cost = count_tokens(m.as_line())
        if used + cost > budget:
            break
        picked.append(m)
        used += cost
        if len(picked) >= MAX_HITS:
            break

    # Cũ trước mới sau: agent đọc theo dòng thời gian, không theo điểm khớp.
    picked.sort(key=lambda m: m.id)
    if picked:
        _log.info("tra archive: %d tin cu duoc dinh kem (%d token)", len(picked), used)
    return picked


def render(messages: list[StoredMessage]) -> str:
    """Khối văn bản đính vào cửa sổ. Rỗng thì không chiếm dòng nào."""
    if not messages:
        return ""
    return (
        "=== TIN CŨ CÓ THỂ LIÊN QUAN (lấy từ archive, KHÔNG theo thứ tự hội thoại) ===\n"
        + "\n".join(m.as_line() for m in messages)
    )
