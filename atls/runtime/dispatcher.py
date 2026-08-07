"""
Dispatcher — chỗ mọi tầng gặp nhau.

Một lượt đi qua đúng bảy bước, theo thứ tự này và không thứ tự nào khác:

    1. ChatLock       — bảo đảm không có agent thứ hai trên cùng chat
    2. Worker slot    — giới hạn số chat chạy song song
    3. Compaction     — nén nếu cửa sổ sắp tràn
    4. Window         — dựng ≤ 20k token
    5. Session        — chọn/xoay (trần 12h)
    6. Agent          — gọi CLI, vừa chạy vừa giữ ack
    7. Gửi + lưu      — trả lời lên Telegram, ghi vào archive

Bước 1 trước bước 2 là cố ý: nếu chiếm slot trước rồi mới chờ khoá chat, hai tin của
cùng một chat sẽ ăn hai slot mà chỉ một cái chạy được — worker pool cạn vì chờ chính
mình. Đây là kiểu deadlock-do-đói rất khó truy khi đã chạy thật.
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path

from atls import log, secrets
from atls.adapters import AgentRequest, AgentResult
from atls.memory.tokens import count_tokens
from atls.memory.window import build_window
from atls.runtime.ack import AckGuard
from atls.runtime.directives import DirectiveRunner, extract
from atls.runtime.locks import ChatLockRegistry
from atls.runtime.router import Route
from atls.store import Store
from atls.telegram.model import Incoming

_log = log.get("runtime.dispatcher")

SYSTEM_FILE = Path(__file__).resolve().parent.parent.parent / "prompts" / "system.md"


class Dispatcher:
    def __init__(
        self, *, cfg, store: Store, api, adapter, sessions, compactor,
        chat_locks: ChatLockRegistry,
    ) -> None:
        self._cfg = cfg
        self._store = store
        self._api = api
        self._adapter = adapter
        self._sessions = sessions
        self._compactor = compactor
        self._locks = chat_locks
        self._slots = asyncio.Semaphore(cfg.workers)
        self._running: dict[str, asyncio.Task] = {}
        self._system = SYSTEM_FILE.read_text(encoding="utf-8") if SYSTEM_FILE.exists() else ""
        self._directives = DirectiveRunner(api=api, store=store, cfg=cfg)

    # ── điểm vào ─────────────────────────────────────────────────────────────

    async def handle(self, chat_id: str, batch: list[Incoming], route: Route) -> None:
        lock = self._locks.get(chat_id)
        if lock.locked():
            _log.info("chat %s đang bận, %d tin sẽ chờ tới lượt", chat_id, len(batch))
        async with lock:
            async with self._slots:
                task = asyncio.current_task()
                if task:
                    self._running[chat_id] = task
                try:
                    await self._turn(chat_id, batch, route)
                except asyncio.CancelledError:
                    await self._api.send_message(
                        chat_id,
                        "Dạ em dừng giữa chừng theo lệnh ạ.\n\n"
                        "⚠️ Việc đang chạy bị cắt ngang nên có thể đã làm xong một phần "
                        "rồi mới dừng — Bệ hạ kiểm lại giúp em trạng thái trước khi chạy lại.",
                    )
                    raise
                finally:
                    self._running.pop(chat_id, None)

    def cancel(self, chat_id: str) -> bool:
        """`/stop` — cắt lượt đang chạy của chat này. True nếu có gì để cắt."""
        task = self._running.get(chat_id)
        if task and not task.done():
            task.cancel()
            return True
        return False

    def busy(self, chat_id: str) -> bool:
        return self._locks.busy(chat_id)

    # ── một lượt ─────────────────────────────────────────────────────────────

    async def _turn(self, chat_id: str, batch: list[Incoming], route: Route) -> None:
        started = time.time()

        # MỘT AckGuard cho cả lượt, bao trọn cả nén lẫn gọi agent.
        #
        # Trước đây có hai guard nối nhau: một cho nén (`immediate=True`), một cho agent.
        # Guard đầu gửi ack rồi bị vứt đi cùng cờ `sent` của nó, nên guard sau không
        # biết đã ack và gửi cái thứ hai — người dùng nhận HAI câu "chờ em chút" cho
        # một câu hỏi. Tệ hơn: nếu lượt đó kết thúc bằng `[SILENT]`, nhánh dọn dẹp ở
        # dưới nhìn vào cờ của guard SAU (False) nên không dọn, và câu "chờ em chút"
        # của guard đầu nằm lại trong group một mình, không bao giờ có hồi kết.
        needs_compaction = self._compactor.needs_compaction(chat_id)
        async with AckGuard(
            self._api, chat_id, after=self._cfg.ack_after,
            addressed=route.addressed,
            immediate=route.likely_long or needs_compaction,
        ) as ack:
            # 3. Nén trước khi dựng cửa sổ. Đồng bộ trong lượt: nén nền sinh ra cửa sổ
            #    đua khi lượt kế tiếp đọc summary cũ trong lúc bản mới đang ghi dở.
            summary = await self._compactor.maybe_compact(chat_id) if needs_compaction else None

            # 4. Cửa sổ ≤ budget. Xây SAU khi các tin của chùm này đã nằm trong archive
            #    (ingest ghi trước khi debounce), nên nó đã bao gồm câu hỏi mới nhất.
            window = build_window(
                self._store, chat_id, self._cfg.window_tokens, summary=summary,
                # Câu vừa gõ là truy vấn để tra archive. Không có nó thì agent chỉ
                # còn bản tóm tắt đã bị viết đè nhiều lần — xem `atls/memory/recall.py`.
                question=" ".join(m.text for m in batch if m.text).strip(),
            )

            # 5. Session.
            choice = self._sessions.choose(chat_id, self._adapter.name)
            prompt = self._build_prompt(window, choice, batch, route)

            # 6. Gọi agent, vừa chạy vừa giữ ack.
            result = await self._invoke(chat_id, prompt, choice)

        elapsed = time.time() - started

        # 7. Gửi + lưu.
        if result.silent:
            if ack.sent:
                # Ack đã bay lên chat rồi; một mình nó trông như câu trả lời bị cụt.
                await self._api.send_message(chat_id, "Dạ em xem xong rồi, không có gì bất thường ạ.")
            _log.info("chat %s: im lặng (%.1fs)", chat_id, elapsed)
            return

        if not result.ok:
            tail = (result.stderr or "(không có stderr)")[-400:]
            await self._api.send_message(
                chat_id, f"Dạ em gọi {self._adapter.name} thất bại "
                         f"(mã {result.returncode}).\n\n```\n{tail}\n```"
            )
            _log.error("chat %s: agent lỗi %s — %s", chat_id, result.returncode, tail)
            return

        # Bóc chỉ thị TRƯỚC khi gửi: dòng `[[SEND_FILE: …]]` mà lọt lên chat vừa xấu
        # vừa lộ đường dẫn nội bộ.
        answer, directives = extract(result.text)

        ids: list[int] = []
        if answer:
            ids = await self._api.send_message(chat_id, answer)
        elif not directives:
            # Agent trả về rỗng mà không phải [SILENT] — hiếm, nhưng im lặng hoàn toàn
            # sau khi đã gửi ack thì người đọc tưởng agent chết.
            answer = "Dạ em xử lý xong rồi ạ."
            ids = await self._api.send_message(chat_id, answer)

        if directives:
            _log.info("chat %s: %d chỉ thị (%s)", chat_id, len(directives),
                      ", ".join(d.name for d in directives))
            await self._directives.run_all(chat_id, directives)

        if answer:
            self._store.add_message(
                chat_id=chat_id, role="agent", text=secrets.redact(answer),
                tokens=count_tokens(answer),
                tg_message_id=ids[0] if ids else None, sender_name="Alice",
            )
        _log.info("chat %s: trả lời %d ký tự trong %.1fs (cửa sổ %d token)",
                  chat_id, len(answer), elapsed, window.tokens)

    async def _invoke(self, chat_id: str, prompt: str, choice) -> AgentResult:
        req = AgentRequest(
            prompt=prompt, system=self._system,
            session_id=choice.row.agent_session_id, resume=choice.resume,
            cwd=self._cfg.agent_cwd, model=self._cfg.agent_model, timeout=None,
        )
        result = await self._adapter.run(req)

        # Resume hỏng: mở session sạch và chạy lại CÙNG prompt. Prompt đã chứa cửa sổ
        # hội thoại nếu session cũ là fresh; nếu nó là resume thì prompt chưa có cửa
        # sổ — nên phải dựng lại trước khi thử lần hai.
        if not result.ok and choice.resume:
            _log.info("chat %s: resume thất bại, mở session mới và chạy lại", chat_id)
            fresh = self._sessions.on_resume_failed(chat_id, choice, self._adapter.name)
            window = build_window(
                self._store, chat_id, self._cfg.window_tokens,
                question=" ".join(m.text for m in batch if m.text).strip(),
            )
            retry_prompt = f"{window.render()}\n\n{prompt}" if window.render() else prompt
            result = await self._adapter.run(
                AgentRequest(
                    prompt=retry_prompt, system=self._system,
                    session_id=fresh.row.agent_session_id, resume=False,
                    cwd=self._cfg.agent_cwd, model=self._cfg.agent_model, timeout=None,
                )
            )
            choice = fresh

        if result.ok:
            self._sessions.on_success(choice)
        return result

    # ── dựng prompt ──────────────────────────────────────────────────────────

    def _build_prompt(self, window, choice, batch: list[Incoming], route: Route) -> str:
        parts: list[str] = []

        # Session mới → dán cửa sổ. Session nối tiếp → KHÔNG dán: lịch sử thật đã nằm
        # trong session CLI, dán thêm chỉ tạo hai nguồn sự thật cho cùng một chuyện.
        if choice.fresh:
            rendered = window.render()
            if rendered:
                parts.append(rendered)
            if choice.rotated_from in ("max_age", "idle"):
                parts.append(
                    "(Ghi chú hệ thống: phiên làm việc trước đã đóng theo lịch. Bạn vẫn "
                    "nhớ nguyên nội dung hội thoại ở trên, chỉ là bối cảnh kỹ thuật của "
                    "phiên cũ — file đang mở, lệnh vừa chạy — đã được dọn.)"
                )

        head = self._describe_batch(batch)
        body = "\n".join(f"[{m.sender_name}]: {m.text}".rstrip() for m in batch if m.text)

        attachments = [m for m in batch if m.media]
        if attachments:
            body += "\n\n" + "\n".join(
                f"[gửi kèm {m.media.get('kind')}: {m.media.get('path') or m.media.get('name') or m.media.get('file_id')}]"
                for m in attachments
            )

        quoted = next((m.reply_to_text for m in batch if m.reply_to_text), "")
        if quoted:
            parts.append(f"[Tin được reply tới]: {secrets.redact(quoted[:500])}")

        parts.append(f"{head}\n\n{body}")
        return "\n\n".join(parts)

    def _describe_batch(self, batch: list[Incoming]) -> str:
        first = batch[0]
        where = {
            "private": "chat riêng",
            "group": "group",
            "supergroup": "group",
            "channel": "channel",
        }.get(first.chat_kind, first.chat_kind)
        senders = list(dict.fromkeys(m.sender_name for m in batch if m.sender_name))
        who = " + ".join(senders) or "người lạ"

        head = f"[Tin nhắn từ {who} trong {where} \"{first.chat_title}\"]"
        if len(batch) > 1:
            head += (f" — {len(batch)} tin gửi liên tiếp nhau, đọc CẢ CHÙM "
                     "rồi trả lời MỘT lần")
        head += " — tin này nhắm THẲNG vào bạn, phải trả lời"
        return head
