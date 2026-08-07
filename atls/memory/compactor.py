"""
Auto-compact — nén phần hội thoại cũ thành một bản tóm tắt chạy tiếp (running summary).

Khi nào chạy: trước khi dựng prompt, nếu phần thô ngoài vùng đã nén vượt
`ATLS_COMPACT_TRIGGER`. Chạy đồng bộ trong lượt — người dùng chờ thêm vài giây một
lần mỗi ~40 tin, đổi lại không bao giờ tràn context. Nén nền (background) nghe hay
hơn nhưng sinh ra một cửa sổ đua: lượt kế tiếp có thể đọc summary cũ trong khi bản
mới đang ghi dở.

Nén cái gì: mọi tin cũ hơn `KEEP_RAW` tin gần nhất. `KEEP_RAW` tin cuối LUÔN giữ
nguyên văn — người ta hay nhắc lại "cái ông vừa nói ấy", và một bản tóm tắt không
giữ được sắc thái đó.

Running summary: bản mới nhận [bản cũ + các tin vừa nén] rồi viết lại thành MỘT bản.
Không nối chuỗi summary — nối thì cửa sổ lại phình theo thời gian, đúng thứ ta đang
tránh.
"""

from __future__ import annotations

import time
from pathlib import Path

from atls import log
from atls.memory.tokens import count_tokens, truncate_to_tokens
from atls.memory.window import raw_tokens_since_summary
from atls.store import Store, StoredMessage, Summary

_log = log.get("memory.compactor")

PROMPT_FILE = Path(__file__).resolve().parent.parent.parent / "prompts" / "compact.md"

# Bản tóm tắt được phép chiếm tối đa ngần này token. Vượt là nó đang kể lại chứ
# không tóm tắt.
SUMMARY_BUDGET = 2_000

# Mỗi lần nén gom tối đa ngần này tin. Vùng chưa nén có thể lớn hơn nhiều (nén hỏng
# vài lượt liền), và nhồi cả 5.000 tin vào một prompt thì chính lượt nén tràn context.
# Nén phần cũ nhất trước, phần còn lại để lượt sau — miễn là mỗi lượt đều tiến lên.
COMPACT_BATCH = 400


class Compactor:
    def __init__(
        self,
        store: Store,
        summarize,  # async (prompt: str) -> str
        *,
        trigger_tokens: int,
        keep_raw: int,
        brain_bridge: bool = False,
        knowledge_dir: Path | None = None,
    ) -> None:
        self._store = store
        self._summarize = summarize
        self._trigger = trigger_tokens
        self._keep_raw = keep_raw
        self._bridge = brain_bridge
        self._knowledge_dir = knowledge_dir

    def needs_compaction(self, chat_id: str) -> bool:
        total, count = raw_tokens_since_summary(self._store, chat_id)
        return total > self._trigger and count > self._keep_raw

    async def maybe_compact(self, chat_id: str) -> Summary | None:
        """Nén nếu cần. Trả về summary mới, hoặc `None` khi không cần / không nén được.

        KHÔNG BAO GIỜ ném ra ngoài: compaction hỏng thì `build_window` vẫn cắt cứng
        theo token và câu trả lời vẫn tới tay người dùng. Mất nét ngữ cảnh còn hơn
        mất cả lượt chat.
        """
        try:
            return await self._compact(chat_id)
        except Exception as exc:  # noqa: BLE001
            _log.warning("nén hỏng cho chat %s (%s) — rơi về cắt cứng", chat_id, exc)
            return None

    async def _compact(self, chat_id: str) -> Summary | None:
        total, count = raw_tokens_since_summary(self._store, chat_id)
        if total <= self._trigger or count <= self._keep_raw:
            return None

        previous = self._store.latest_summary(chat_id)
        after = previous.to_msg_id if previous else 0

        # Số tin ĐƯỢC PHÉP nén = tất cả trừ `keep_raw` tin cuối, chặn trên bởi
        # `COMPACT_BATCH`. Tính trên `count` thật (SQL, không LIMIT) chứ không trên độ
        # dài của batch vừa đọc — lấy `batch[:-keep_raw]` là cắt nhầm 12 tin ở giữa
        # vùng chưa nén, và 12 tin đó không bao giờ được nén cũng không bao giờ bị bỏ.
        allowed = min(count - self._keep_raw, COMPACT_BATCH)
        if allowed <= 0:
            return None
        to_compact = self._store.messages_after(chat_id, after, limit=allowed)
        if not to_compact:
            return None

        prompt = self._build_prompt(previous, to_compact)

        started = time.time()
        text = (await self._summarize(prompt) or "").strip()
        if not text:
            _log.warning("agent trả tóm tắt rỗng cho chat %s", chat_id)
            return None

        text = truncate_to_tokens(text, SUMMARY_BUDGET)
        tokens = count_tokens(text)
        covered = (previous.covered if previous else 0) + len(to_compact)

        summary_id = self._store.add_summary(
            chat_id=chat_id,
            from_msg_id=previous.from_msg_id if previous else to_compact[0].id,
            to_msg_id=to_compact[-1].id,
            covered=covered,
            text=text,
            tokens=tokens,
        )
        _log.info(
            "đã nén chat %s: %d tin (%d token) -> %d token trong %.1fs",
            chat_id, len(to_compact), total, tokens, time.time() - started,
        )

        summary = Summary(
            id=summary_id, chat_id=chat_id,
            from_msg_id=previous.from_msg_id if previous else to_compact[0].id,
            to_msg_id=to_compact[-1].id, covered=covered, text=text, tokens=tokens,
        )
        if self._bridge:
            self._write_brain_entry(chat_id, summary, to_compact)
        return summary

    def _build_prompt(
        self, previous: Summary | None, to_compact: list[StoredMessage]
    ) -> str:
        instructions = (
            PROMPT_FILE.read_text(encoding="utf-8")
            if PROMPT_FILE.exists()
            else _FALLBACK_PROMPT
        )
        parts = [instructions]
        if previous:
            parts.append(
                "=== BẢN TÓM TẮT HIỆN CÓ (đã phủ "
                f"{previous.covered} tin trước đó) ===\n{previous.text}"
            )
        parts.append(
            "=== ĐOẠN HỘI THOẠI MỚI CẦN GỘP VÀO ===\n"
            + "\n".join(m.as_line() for m in to_compact)
        )
        parts.append(
            "Viết BẢN TÓM TẮT MỚI DUY NHẤT gộp cả hai phần trên. "
            f"Tối đa {SUMMARY_BUDGET} token. Chỉ xuất bản tóm tắt, không lời dẫn."
        )
        return "\n\n".join(parts)

    def _write_brain_entry(
        self, chat_id: str, summary: Summary, compacted: list[StoredMessage]
    ) -> None:
        """Cầu nối Alice Coding: ghi bản tóm tắt thành entry `knowledge/context/`.

        Chỉ GHI FILE, không chạy sync — sync là việc của routine, và nó tốn hàng chục
        phút. Bắt một lượt chat chờ sync là hỏng cả trải nghiệm.
        """
        if not self._knowledge_dir:
            return
        try:
            ctx_dir = self._knowledge_dir / "context"
            ctx_dir.mkdir(parents=True, exist_ok=True)
            day = time.strftime("%Y-%m-%d")
            path = ctx_dir / f"{day}-telegram-{chat_id.lstrip('-')}-{summary.id}.md"
            path.write_text(
                f"# Hội thoại Telegram — chat {chat_id} — {day}\n\n"
                f"> Sinh tự động bởi ATLS compactor. Phủ {summary.covered} tin, "
                f"tới message id {summary.to_msg_id}.\n\n"
                f"{summary.text}\n\n"
                f"## Người tham gia\n\n"
                + "\n".join(f"- {n}" for n in sorted({m.sender_name for m in compacted if m.sender_name}))
                + "\n",
                encoding="utf-8",
            )
            _log.info("đã ghi entry brain: %s", path.name)
        except Exception as exc:  # noqa: BLE001 — cầu nối hỏng không được giết lượt chat
            _log.warning("ghi entry brain thất bại: %s", exc)


_FALLBACK_PROMPT = """\
Bạn đang nén lịch sử một cuộc hội thoại Telegram để agent không tràn context.

Giữ lại, theo đúng thứ tự ưu tiên này:
1. Việc đã được giao và trạng thái của nó (xong / đang làm / bị chặn).
2. Quyết định đã chốt, sở thích đã nêu, luật người dùng đặt ra.
3. Sự việc và con số cụ thể (tên, ngày, đường dẫn, id) — đừng làm mờ thành "một số".
4. Sắc thái quan hệ: ai khen, ai phàn nàn, ai đang bực về chuyện gì.
5. Câu hỏi còn treo chưa ai trả lời.

Bỏ đi: chào hỏi xã giao, emoji đơn lẻ, câu đã bị chính người nói rút lại, và mọi
chi tiết không ảnh hưởng tới lượt sau.

Viết văn xuôi gạch đầu dòng, tiếng Việt, ngôi thứ ba, gọi đúng tên người.
Không bịa. Không suy diễn. Không có gì đáng giữ thì nói thẳng là không có.
"""
