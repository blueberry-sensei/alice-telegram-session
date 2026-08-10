"""
App — nối các tầng lại và chạy vòng đời.

Luồng một tin nhắn, đầy đủ:

    ingest → queue → _consume → ghi archive (idempotent) → debounce
           → router → [COMMAND: trả lời ngay] hoặc [ANSWER: dispatcher]

Điểm dễ hiểu sai: **archive được ghi TRƯỚC debounce**, không phải sau. Lý do là nếu
daemon chết trong 10 giây debounce, tin đã nằm an toàn trong DB; lượt sau khởi động
lại vẫn thấy nó trong cửa sổ hội thoại. Ghi sau debounce thì 10 giây đó là một lỗ
hổng mất dữ liệu có thật.
"""

from __future__ import annotations

import asyncio
import contextlib

from atls import log, secrets
from atls.adapters import AgentRequest, build_adapter
from atls.config import Config
from atls.memory.compactor import Compactor
from atls.memory.tokens import count_tokens
from atls.runtime.commands import CommandHandler
from atls.runtime.debounce import Debouncer
from atls.runtime.dispatcher import Dispatcher
from atls.runtime.locks import ChatLockRegistry
from atls.runtime.router import Decision, classify, merge
from atls.session import SessionManager, parse_quiet_window
from atls.store import Store
from atls.telegram.api import TelegramAPI
from atls.telegram.ingest import build_ingest
from atls.telegram.model import Incoming, parse_update

_log = log.get("app")


class App:
    def __init__(self, cfg: Config) -> None:
        self._cfg = cfg
        self._store = Store(cfg.db_path)
        self._adapter = build_adapter(
            cfg.agent, cfg.agent_cmd, skip_permissions=cfg.agent_skip_permissions
        )
        self._queue: asyncio.Queue = asyncio.Queue(maxsize=1000)
        self._locks = ChatLockRegistry()
        self._sessions = SessionManager(
            self._store,
            max_age=cfg.session_max_age,
            idle=cfg.session_idle,
            quiet_window=parse_quiet_window(cfg.session_quiet_window),
        )
        self._api: TelegramAPI | None = None
        self._tasks: list[asyncio.Task] = []
        self._debouncer = Debouncer(cfg.debounce, cfg.debounce_max, self._on_batch)

    async def run(self) -> None:
        cfg = self._cfg
        cfg.ensure_dirs()

        if not self._adapter.is_available():
            _log.warning(
                "chưa tìm thấy lệnh `%s` trên PATH — daemon vẫn chạy nhưng mọi lượt "
                "sẽ báo lỗi. Chạy `atls doctor` để kiểm.", self._adapter.name,
            )

        async with TelegramAPI(cfg.bot_token) as api:
            self._api = api
            username = await api.resolve_me()
            _log.info("đăng nhập với tư cách @%s, agent=%s", username, self._adapter.name)

            compactor = Compactor(
                self._store, self._summarize,
                trigger_tokens=cfg.compact_trigger, keep_raw=cfg.keep_raw,
                brain_bridge=cfg.brain_bridge, knowledge_dir=cfg.knowledge_dir,
            )
            self._dispatcher = Dispatcher(
                cfg=cfg, store=self._store, api=api, adapter=self._adapter,
                sessions=self._sessions, compactor=compactor, chat_locks=self._locks,
            )
            self._commands = CommandHandler(
                cfg=cfg, store=self._store, api=api,
                sessions=self._sessions, dispatcher=self._dispatcher,
            )

            ingest = build_ingest(cfg, api, self._store, self._queue)
            await ingest.start()
            self._tasks.append(asyncio.create_task(self._consume(), name="atls-consume"))
            # Gom cả vòng nhận tin vào `gather`: nó chết mà không ai đợi thì daemon vẫn
            # "đang chạy" trong khi không còn nghe ai nữa.
            self._tasks.extend(ingest.background_tasks())

            _log.info("Alice đang nghe. %d worker, cửa sổ %s token, phiên tối đa %.0fh.",
                      cfg.workers, f"{cfg.window_tokens:,}", cfg.session_max_age / 3600)
            try:
                await asyncio.gather(*self._tasks)
            except asyncio.CancelledError:
                _log.info("đang tắt…")
            finally:
                self._debouncer.cancel_all()
                await ingest.stop()
                self._store.close()

    async def stop(self) -> None:
        for task in self._tasks:
            task.cancel()

    # ── tiêu thụ hàng đợi ────────────────────────────────────────────────────

    async def _consume(self) -> None:
        while True:
            update = await self._queue.get()
            try:
                await self._ingest_one(update)
            except Exception:  # noqa: BLE001 — một update hỏng không được giết daemon
                _log.exception("xử lý update thất bại")
            finally:
                self._queue.task_done()

    async def _ingest_one(self, update: dict) -> None:
        msg = parse_update(update)
        if msg is None:
            return
        if not self._cfg.chat_allowed(msg.chat_id):
            _log.debug("bỏ qua chat ngoài danh sách: %s", msg.chat_id)
            return

        self._store.upsert_chat(msg.chat_id, msg.chat_kind, msg.chat_title)
        chat_cfg = self._store.chat_config(msg.chat_id)
        if chat_cfg and not chat_cfg.get("enabled", 1):
            return

        route = classify(msg, self._api.username, self._cfg.triggers)
        if route.decision is Decision.IGNORE:
            return

        # Tải file đính kèm TRƯỚC khi lưu, để đường dẫn nằm luôn trong archive —
        # `file_id` của Telegram hết hạn, đường dẫn local thì không.
        media = msg.media
        if media and media.get("file_id"):
            path = await self._api.download(
                media["file_id"], self._cfg.inbox_dir / msg.chat_id.lstrip("-")
            )
            if path:
                media = {**media, "path": str(path)}
                msg = _with_media(msg, media)

        text = secrets.redact(msg.text)
        row_id = self._store.add_message(
            chat_id=msg.chat_id, role="human", text=text, tokens=count_tokens(text),
            update_id=msg.update_id, tg_message_id=msg.message_id,
            sender_id=msg.sender_id, sender_name=msg.sender_name,
            reply_to=msg.reply_to_id, addressed=route.addressed, media=media,
            ts=msg.ts or None,
        )
        if row_id is None:
            # `update_id` đã có → Telegram gửi lại. Toàn bộ cơ chế chống xử lý hai lần
            # nằm ở đúng dòng này.
            _log.debug("update %s đã xử lý rồi, bỏ qua", msg.update_id)
            return

        if route.decision is Decision.COMMAND:
            # Lệnh hệ thống không đi qua debounce: người ta gõ /stop là muốn dừng NGAY,
            # không phải sau 10 giây.
            await self._commands.handle(msg, route.command, route.args)
            return

        if route.decision is Decision.BACKGROUND:
            _log.debug("chat %s: tin nền (%s)", msg.chat_id, route.reason)
            return

        await self._debouncer.add(msg)

    async def _on_batch(self, chat_id: str, batch: list[Incoming]) -> None:
        routes = [classify(m, self._api.username, self._cfg.triggers) for m in batch]
        route = merge(routes)
        if route.decision is not Decision.ANSWER:
            return
        _log.info("chat %s: %d tin -> gọi agent (%s)", chat_id, len(batch), route.reason)
        # Chạy nền để `_consume` tiếp tục nhận tin trong lúc agent làm việc. Không có
        # dòng này thì một việc 10 phút làm cả hệ thống điếc suốt 10 phút.
        task = asyncio.create_task(
            self._dispatcher.handle(chat_id, batch, route), name=f"atls-turn-{chat_id}"
        )
        self._tasks.append(task)
        task.add_done_callback(self._reap)

    def _reap(self, task: asyncio.Task) -> None:
        with contextlib.suppress(ValueError):
            self._tasks.remove(task)
        if task.cancelled():
            return
        if exc := task.exception():
            _log.error("lượt chat kết thúc bằng lỗi: %s", exc)

    # ── nén ──────────────────────────────────────────────────────────────────

    async def _summarize(self, prompt: str) -> str:
        """Lượt agent rẻ, KHÔNG session, chỉ để nén.

        Không session là cố ý: bản tóm tắt phải chỉ dựa vào đoạn hội thoại được đưa
        vào, không được nhiễm bối cảnh công việc đang dở của session chính.
        """
        import uuid
        result = await self._adapter.run(
            AgentRequest(
                prompt=prompt,
                system="Bạn là bộ nén ký ức. Chỉ xuất bản tóm tắt, không lời dẫn, "
                       "không dùng công cụ, không hỏi lại.",
                session_id=str(uuid.uuid4()), resume=False,
                cwd=self._cfg.agent_cwd, model=self._cfg.agent_model,
                timeout=300,
            )
        )
        return result.text if result.ok else ""


def _with_media(msg: Incoming, media: dict) -> Incoming:
    from dataclasses import replace
    return replace(msg, media=media)
