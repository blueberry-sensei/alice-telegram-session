"""
Dựng cửa sổ hội thoại đưa vào mỗi lượt.

Hình dạng cửa sổ, từ cũ tới mới:

    [ TÓM TẮT các phiên trước ]   ← một row summary duy nhất (running summary)
    [ tin thô ... tin thô ]       ← các tin CHƯA được nén, lấy ngược từ mới nhất
    [ tin mới nhất người vừa gõ ]

Đúng ví dụ trong đề bài: hội thoại 50 tin, đếm ngược tới tin 17 thì chạm trần →
cửa sổ = [tóm tắt(1..16), tin 17..50, tin mới]. Người dùng thấy agent nhớ nguyên
mạch, còn agent chỉ đọc đúng phần cần thiết.

Hai điều bảo đảm:
  1. Cửa sổ **luôn** ≤ budget, kể cả khi compaction chưa kịp chạy hoặc chạy hỏng.
  2. Tin mới nhất **không bao giờ** bị cắt — thà mất ngữ cảnh cũ còn hơn mất câu hỏi.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from atls.memory.recall import RECALL_BUDGET, recall
from atls.memory.recall import render as recall_render
from atls.memory.tokens import count_tokens, truncate_to_tokens
from atls.store import StoredMessage, Store, Summary

# Chừa cho phần đầu prompt (nhãn, hướng dẫn) và chênh lệch giữa heuristic với
# tokenizer thật. 5% là biên đủ mà không phí.
_SAFETY = 0.95

# Trần số tin ĐỌC LÊN để dựng cửa sổ. Không phải trần cửa sổ — trần cửa sổ tính bằng
# token và nằm ở `budget`. Cái này chỉ để một chat 200k tin không kéo cả bảng vào RAM.
# Lấy từ ĐUÔI (tin mới nhất), nên vượt trần chỉ mất phần cũ — thứ dù sao cũng không
# lọt nổi vào 20k token.
_MAX_ROWS = 2_000


@dataclass(frozen=True)
class ConversationWindow:
    summary: str
    messages: list[StoredMessage]
    tokens: int
    truncated: bool
    #: Tin cũ lôi từ archive lên vì có vẻ liên quan tới câu vừa hỏi. Xem
    #: `atls/memory/recall.py` — nó vá chỗ hở mà bản tóm tắt bị viết đè để lại.
    recalled: list[StoredMessage] = field(default_factory=list)

    def render(self) -> str:
        parts: list[str] = []
        # Đặt TRƯỚC bản tóm tắt và hội thoại gần đây, vì nó là thứ cũ nhất. Đọc theo
        # thứ tự thời gian thì agent không nhầm tin ba hôm trước là tin vừa nhắn.
        if self.recalled:
            parts.append(recall_render(self.recalled))
        if self.summary:
            parts.append(
                "=== CHUYỆN ĐÃ XẢY RA TRƯỚC ĐÓ (bản tóm tắt tự động) ===\n"
                + self.summary
            )
        if self.messages:
            parts.append(
                "=== HỘI THOẠI GẦN ĐÂY (nguyên văn) ===\n"
                + "\n".join(m.as_line() for m in self.messages)
            )
        return "\n\n".join(parts)


def build_window(
    store: Store,
    chat_id: str,
    budget: int,
    *,
    summary: Summary | None = None,
    question: str = "",
) -> ConversationWindow:
    """Dựng cửa sổ cho `chat_id` trong hạn mức `budget` token.

    `summary` truyền vào để Compactor dùng lại bản vừa tạo mà không phải đọc lại DB.

    `question` là câu người dùng vừa gõ. Có nó thì archive được tra để kéo lên những
    tin cũ liên quan — thứ đã rơi khỏi bản tóm tắt bị viết đè nhiều lần. Để trống thì
    bỏ qua hẳn bước đó (Compactor dựng cửa sổ để NÉN, không để trả lời, nên nó không
    cần và không nên kéo thêm gì vào).
    """
    if summary is None:
        summary = store.latest_summary(chat_id)

    limit = int(budget * _SAFETY)
    summary_text = summary.text if summary else ""
    summary_tokens = count_tokens(summary_text) if summary_text else 0

    # Tóm tắt phình to hơn nửa cửa sổ là hỏng — nó phải là bản NÉN, không phải bản
    # sao. Cắt nó xuống chứ đừng hy sinh tin thô: tin thô là thứ đang được hỏi.
    if summary_tokens > limit // 2:
        summary_text = truncate_to_tokens(summary_text, limit // 2)
        summary_tokens = count_tokens(summary_text)

    after = summary.to_msg_id if summary else 0
    # ĐUÔI, không phải đầu: xem `Store.messages_after_tail`.
    raw = store.messages_after_tail(chat_id, after, limit=_MAX_ROWS)

    remaining = limit - summary_tokens
    picked: list[StoredMessage] = []
    truncated = False
    # Lấy ngược từ mới nhất: tin gần nhất quý nhất, và nếu phải bỏ thì bỏ tin cũ.
    for msg in reversed(raw):
        cost = msg.tokens or count_tokens(msg.as_line())
        if cost > remaining:
            # Tin đầu tiên đã vượt trần một mình → cắt chính nó, đừng trả cửa sổ rỗng.
            if not picked:
                clipped = truncate_to_tokens(msg.as_line(), remaining)
                picked.append(
                    StoredMessage(
                        id=msg.id, chat_id=msg.chat_id, ts=msg.ts, role=msg.role,
                        sender_name=msg.sender_name, text=clipped,
                        addressed=msg.addressed, tokens=remaining, media=None,
                    )
                )
                remaining = 0
            truncated = True
            break
        picked.append(msg)
        remaining -= cost

    picked.reverse()
    total = summary_tokens + sum(m.tokens or count_tokens(m.as_line()) for m in picked)

    # Tra archive bằng phần hạn mức CÒN THỪA, không phải bằng hạn mức cộng thêm. Cửa
    # sổ vượt trần là đúng thứ cả tầng này tồn tại để chặn, và một tính năng trí nhớ
    # phá vỡ trần trí nhớ thì tự nó vô hiệu hoá chính nó.
    recalled: list[StoredMessage] = []
    if question:
        spare = min(RECALL_BUDGET, max(0, limit - total))
        if spare > 0:
            recalled = recall(
                store, chat_id, question,
                exclude_ids={m.id for m in picked},
                budget=spare,
            )
            total += sum(count_tokens(m.as_line()) for m in recalled)

    return ConversationWindow(
        summary=summary_text, messages=picked, tokens=total,
        truncated=truncated, recalled=recalled,
    )


def raw_tokens_since_summary(store: Store, chat_id: str) -> tuple[int, int]:
    """`(tổng token, số tin)` của vùng chưa nén. Đây là tín hiệu kích hoạt nén.

    Đếm bằng SQL trên TOÀN BỘ vùng chưa nén, không qua một truy vấn có `LIMIT`: chỉ số
    kích hoạt mà bão hoà ở trần thì đúng lúc hệ thống tụt lại xa nhất lại là lúc nó
    ngừng báo động.
    """
    summary = store.latest_summary(chat_id)
    after = summary.to_msg_id if summary else 0
    count, tokens = store.unsummarized_stats(chat_id, after)
    return tokens, count
