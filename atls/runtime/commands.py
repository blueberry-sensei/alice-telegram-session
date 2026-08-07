"""
Lệnh hệ thống — runtime tự trả lời, KHÔNG đánh thức agent.

Vì sao tách khỏi agent: các lệnh này phải trả lời tức thì. Bắt người dùng chờ tám
giây một tiến trình CLI khởi động chỉ để nghe "đã reset session" là vô lý, và tệ hơn:
`/stop` mà phải chờ agent rảnh mới xử lý được thì nó chẳng dừng được gì cả.
"""

from __future__ import annotations

import time

from atls import log
from atls.store import Store
from atls.telegram.model import Incoming

_log = log.get("runtime.commands")

HELP = """\
<b>Alice Telegram Session</b>

/status — phiên hiện tại, trí nhớ, ai đang bận
/reset — đóng phiên, bắt đầu sạch (vẫn nhớ hội thoại)
/stop — cắt ngang việc đang chạy
/nhớ &lt;từ khoá&gt; — tra lại chuyện cũ trong toàn bộ lịch sử chat
/done — báo đã làm xong việc em nhờ (đăng nhập, xác nhận…)
/whoami — em thấy gì về chat này
/help — bảng này

Ngoài lệnh, cứ @mention em hoặc reply vào tin của em là em trả lời.
Trong chat riêng thì tin nào em cũng đọc.
"""


class CommandHandler:
    def __init__(self, *, cfg, store: Store, api, sessions, dispatcher) -> None:
        self._cfg = cfg
        self._store = store
        self._api = api
        self._sessions = sessions
        self._dispatcher = dispatcher

    async def handle(self, msg: Incoming, command: str, args: str) -> None:
        fn = getattr(self, f"_cmd_{command}", None)
        if fn is None:
            await self._api.send_message(msg.chat_id, HELP)
            return
        await fn(msg, args)

    # ── lệnh ─────────────────────────────────────────────────────────────────

    async def _cmd_start(self, msg: Incoming, args: str) -> None:
        await self._api.send_message(
            msg.chat_id,
            "Dạ em là Alice ạ. Em nhớ mọi thứ mình đã nói với nhau, kể cả chuyện "
            "tuần trước — cứ nhắn em bình thường.\n\n" + HELP,
        )

    async def _cmd_help(self, msg: Incoming, args: str) -> None:
        await self._api.send_message(msg.chat_id, HELP)

    async def _cmd_status(self, msg: Incoming, args: str) -> None:
        chat_id = msg.chat_id
        total = self._store.count_messages(chat_id)
        summary = self._store.latest_summary(chat_id)
        busy = self._dispatcher.busy(chat_id)
        gates = self._store.pending_gates(chat_id)

        lines = [
            "<b>Trạng thái</b>",
            f"• Phiên: {self._sessions.describe(chat_id)}",
            f"• Agent: <code>{self._cfg.agent}</code>"
            + (f" ({self._cfg.agent_model})" if self._cfg.agent_model else ""),
            f"• Lịch sử: {total} tin đã lưu",
        ]
        if summary:
            lines.append(
                f"• Đã nén: {summary.covered} tin → {summary.tokens} token tóm tắt"
            )
        else:
            lines.append("• Đã nén: chưa cần nén lần nào")
        lines.append(f"• Cửa sổ tối đa: {self._cfg.window_tokens:,} token")
        lines.append(f"• Đang chạy việc gì không: {'có' if busy else 'không'}")
        if gates:
            lines.append(f"• Đang chờ Bệ hạ: {len(gates)} việc — gõ /done khi xong")
            lines += [f"   ↳ {g['what']}" for g in gates[:3]]
        await self._api.send_message(chat_id, "\n".join(lines))

    async def _cmd_reset(self, msg: Incoming, args: str) -> None:
        self._sessions.reset(msg.chat_id)
        await self._api.send_message(
            msg.chat_id,
            "Dạ em mở phiên mới rồi ạ. Em vẫn nhớ mọi chuyện mình đã nói — chỉ là "
            "bối cảnh kỹ thuật của phiên cũ đã được dọn cho sạch.",
        )

    async def _cmd_stop(self, msg: Incoming, args: str) -> None:
        if self._dispatcher.cancel(msg.chat_id):
            _log.info("chat %s: /stop bởi %s", msg.chat_id, msg.sender_name)
            return  # dispatcher tự gửi thông báo dừng, tránh gửi hai lần
        await self._api.send_message(
            msg.chat_id, "Dạ em đang rảnh, không có việc nào đang chạy để dừng ạ."
        )

    async def _cmd_done(self, msg: Incoming, args: str) -> None:
        n = self._store.resolve_gates(msg.chat_id, msg.sender_name)
        if n:
            await self._api.send_message(
                msg.chat_id, f"Dạ em ghi nhận {n} việc đã xong, cảm ơn Bệ hạ ạ."
            )
        else:
            await self._api.send_message(
                msg.chat_id, "Dạ em không có việc nào đang nhờ Bệ hạ cả ạ."
            )

    async def _cmd_whoami(self, msg: Incoming, args: str) -> None:
        cfg = self._store.chat_config(msg.chat_id)
        await self._api.send_message(
            msg.chat_id,
            f"<b>Chat này</b>\n"
            f"• ID: <code>{msg.chat_id}</code>\n"
            f"• Loại: {msg.chat_kind}\n"
            f"• Tên: {msg.chat_title or '(không có)'}\n"
            f"• Em thấy lần đầu: {_fmt(cfg.get('first_seen'))}\n"
            f"• Bệ hạ đang gọi em: {msg.sender_name} (<code>{msg.sender_id}</code>)",
        )

    async def _cmd_recall(self, msg: Incoming, args: str) -> None:
        await self._cmd_nhớ(msg, args)

    async def _cmd_nhớ(self, msg: Incoming, args: str) -> None:
        """Tra thẳng archive — đường trả lời "tuần trước ông này nói gì".

        Cố ý KHÔNG gọi agent: tìm toàn văn trên SQLite mất mili-giây và trả về nguyên
        văn kèm ngày. Cho agent tóm tắt lại chỉ thêm một lớp có thể bịa.
        """
        if not args:
            await self._api.send_message(
                msg.chat_id, "Dạ Bệ hạ gõ <code>/nhớ &lt;từ khoá&gt;</code> giúp em ạ."
            )
            return
        hits = self._store.search(args, chat_id=msg.chat_id, limit=8)
        if not hits:
            await self._api.send_message(
                msg.chat_id, f"Dạ em tìm \"{args}\" trong lịch sử chat này mà không thấy gì ạ."
            )
            return
        lines = [f"<b>Em tìm thấy {len(hits)} chỗ nhắc tới \"{args}\":</b>", ""]
        for m in hits:
            when = time.strftime("%d/%m %H:%M", time.localtime(m.ts))
            text = m.text if len(m.text) <= 220 else m.text[:220] + "…"
            lines.append(f"<b>{when}</b> — {m.sender_name}:\n{text}\n")
        await self._api.send_message(msg.chat_id, "\n".join(lines))


def _fmt(ts) -> str:
    if not ts:
        return "(không rõ)"
    return time.strftime("%d/%m/%Y", time.localtime(float(ts)))
