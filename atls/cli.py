"""
Điểm vào dòng lệnh.

    atls run           chạy daemon (mặc định)
    atls doctor        kiểm tra cấu hình + CLI có sẵn, KHÔNG in giá trị secret
    atls chats         liệt kê chat đã biết
    atls recall <q>    tra archive từ terminal
    atls compact <id>  ép nén một chat ngay
    atls webhook set|delete
"""

from __future__ import annotations

import argparse
import asyncio
import signal
import sys
import time
from pathlib import Path

from atls import config, log
from atls.adapters import available_agents, build_adapter
from atls.app import App
from atls.runtime.locks import SingletonLock
from atls.store import Store


def _bootstrap(args) -> config.Config:
    cfg = config.load(Path(args.env) if args.env else None)
    cfg.ensure_dirs()
    log.setup(cfg.log_level, cfg.data_dir / "logs")
    return cfg


# ── run ──────────────────────────────────────────────────────────────────────

def cmd_run(args) -> int:
    cfg = _bootstrap(args)
    if not cfg.bot_token:
        print("Thiếu TELEGRAM_BOT_TOKEN. Chép .env.example thành .env rồi điền.", file=sys.stderr)
        return 2

    lock = SingletonLock(cfg.data_dir / "daemon.lock")
    if not lock.acquire():
        print(
            f"Đã có một ATLS daemon đang chạy (lock: {cfg.data_dir / 'daemon.lock'}).\n"
            "Dừng tiến trình cũ, hoặc xoá file lock nếu chắc chắn nó đã chết.",
            file=sys.stderr,
        )
        return 1

    app = App(cfg)
    try:
        asyncio.run(_run_with_signals(app))
        return 0
    except KeyboardInterrupt:
        return 0
    finally:
        lock.release()


async def _run_with_signals(app: App) -> None:
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, lambda: asyncio.create_task(app.stop()))
        except NotImplementedError:
            # Windows không cài được signal handler cho SIGTERM trên proactor loop.
            # KeyboardInterrupt ở `cmd_run` vẫn bắt được Ctrl+C, và service manager
            # dùng taskkill — nên bỏ qua ở đây là an toàn.
            pass
    await app.run()


# ── doctor ───────────────────────────────────────────────────────────────────

def cmd_doctor(args) -> int:
    cfg = _bootstrap(args)
    ok = True

    def line(good: bool, label: str, detail: str = "") -> None:
        nonlocal ok
        ok = ok and good
        print(f"  {'✅' if good else '❌'} {label}{(' — ' + detail) if detail else ''}")

    print("\nAlice Telegram Session — kiểm tra\n")

    print("Telegram")
    # Chỉ in ĐỘ DÀI token, không bao giờ in giá trị: `doctor` hay được chụp màn hình
    # gửi đi lúc hỏi lỗi.
    line(bool(cfg.bot_token), "TELEGRAM_BOT_TOKEN", f"{len(cfg.bot_token)} ký tự" if cfg.bot_token else "chưa điền")
    line(True, "Nhận tin", cfg.ingest)
    if cfg.ingest == "webhook":
        line(bool(cfg.webhook_url), "ATLS_WEBHOOK_URL", cfg.webhook_url or "chưa điền")
        line(bool(cfg.webhook_secret), "ATLS_WEBHOOK_SECRET",
             "đã đặt" if cfg.webhook_secret else "TRỐNG — ai đoán được URL cũng bơm được tin giả")
    line(bool(cfg.allowed_chats), "Danh sách chat",
         ", ".join(sorted(cfg.allowed_chats)) if cfg.allowed_chats else "TRỐNG — nhận mọi chat")

    print("\nAgent CLI")
    for name, present in available_agents().items():
        marker = " ← đang dùng" if name == cfg.agent else ""
        print(f"  {'✅' if present else '·  '} {name}{marker}")
    try:
        adapter = build_adapter(cfg.agent, cfg.agent_cmd)
        line(adapter.is_available(), f"`{cfg.agent}` chạy được",
             "" if adapter.is_available() else "không thấy trên PATH")
        line(True, "Nối tiếp session", "có" if adapter.supports_resume() else
             "không — ATLS tự dán cửa sổ hội thoại, vẫn nhớ đủ")
    except ValueError as exc:
        line(False, "Cấu hình agent", str(exc))

    print("\nTrí nhớ")
    line(cfg.compact_trigger < cfg.window_tokens, "Ngưỡng nén",
         f"{cfg.compact_trigger:,} < {cfg.window_tokens:,} token")
    try:
        import tiktoken  # noqa: F401
        line(True, "Đếm token", "tiktoken (chính xác)")
    except ImportError:
        line(True, "Đếm token", "heuristic — cài `pip install tiktoken` cho chính xác")
    store = Store(cfg.db_path)
    chats = store.list_chats()
    line(True, "Cơ sở dữ liệu", f"{cfg.db_path} · {len(chats)} chat")

    print("\nPhiên")
    line(cfg.session_max_age <= 12 * 3600, "Trần tuổi phiên",
         f"{cfg.session_max_age / 3600:.1f}h")
    line(True, "Ngưỡng im lặng", f"{cfg.session_idle / 3600:.1f}h")
    line(True, "Worker song song", str(cfg.workers))

    print("\nKhả năng mở rộng")
    try:
        import reportlab  # noqa: F401
        line(True, "Gửi PDF", "sẵn sàng")
    except ImportError:
        line(True, "Gửi PDF", "chưa cài reportlab (tuỳ chọn)")
    line(True, "Cầu nối Alice Coding",
         str(cfg.knowledge_dir) if cfg.brain_bridge else "tắt")

    store.close()
    print("\n" + ("Mọi thứ sẵn sàng.\n" if ok else "Còn mục ❌ ở trên cần sửa.\n"))
    return 0 if ok else 1


# ── tra cứu ──────────────────────────────────────────────────────────────────

def cmd_chats(args) -> int:
    cfg = _bootstrap(args)
    store = Store(cfg.db_path)
    rows = store.list_chats()
    if not rows:
        print("Chưa có chat nào.")
        return 0
    print(f"{'chat_id':>16}  {'loại':<11} {'tin':>6}  {'lần cuối':<17} tên")
    for r in rows:
        n = store.count_messages(r["chat_id"])
        last = time.strftime("%Y-%m-%d %H:%M", time.localtime(r["last_seen"]))
        print(f"{r['chat_id']:>16}  {r['kind']:<11} {n:>6}  {last:<17} {r['title']}")
    store.close()
    return 0


def cmd_recall(args) -> int:
    cfg = _bootstrap(args)
    store = Store(cfg.db_path)
    hits = store.search(" ".join(args.query), chat_id=args.chat, limit=args.limit)
    if not hits:
        print("Không tìm thấy gì.")
        return 1
    for m in hits:
        when = time.strftime("%Y-%m-%d %H:%M", time.localtime(m.ts))
        print(f"\n[{when}] {m.sender_name} (chat {m.chat_id})\n{m.text}")
    store.close()
    return 0


def cmd_compact(args) -> int:
    """Ép nén ngay — dùng để kiểm chất lượng bản tóm tắt mà không phải chat 40 lượt."""
    cfg = _bootstrap(args)
    from atls.memory.compactor import Compactor

    store = Store(cfg.db_path)
    adapter = build_adapter(cfg.agent, cfg.agent_cmd)

    async def summarize(prompt: str) -> str:
        import uuid
        from atls.adapters import AgentRequest
        res = await adapter.run(AgentRequest(
            prompt=prompt, system="Bạn là bộ nén ký ức. Chỉ xuất bản tóm tắt.",
            session_id=str(uuid.uuid4()), resume=False,
            cwd=cfg.agent_cwd, model=cfg.agent_model, timeout=300,
        ))
        return res.text if res.ok else ""

    comp = Compactor(store, summarize, trigger_tokens=0, keep_raw=cfg.keep_raw,
                     brain_bridge=cfg.brain_bridge, knowledge_dir=cfg.knowledge_dir)
    summary = asyncio.run(comp.maybe_compact(args.chat))
    if summary is None:
        print("Không có gì để nén (hoặc nén thất bại — xem log).")
        return 1
    print(f"Đã nén tới message #{summary.to_msg_id} ({summary.covered} tin, "
          f"{summary.tokens} token):\n\n{summary.text}")
    store.close()
    return 0


def cmd_webhook(args) -> int:
    cfg = _bootstrap(args)
    from atls.telegram.api import TelegramAPI

    async def go() -> None:
        async with TelegramAPI(cfg.bot_token) as api:
            if args.action == "delete":
                await api.delete_webhook()
                print("Đã gỡ webhook. Bot quay về chế độ long-poll.")
                return
            if not cfg.webhook_url:
                raise SystemExit("Thiếu ATLS_WEBHOOK_URL trong .env")
            url = f"{cfg.webhook_url.rstrip('/')}/atls/webhook"
            await api.set_webhook(url, cfg.webhook_secret)
            print(f"Đã đặt webhook: {url}")

    asyncio.run(go())
    return 0


# ── parser ───────────────────────────────────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser(
        prog="atls", description="Alice Telegram Session — agent sống 24/7 trên Telegram"
    )
    parser.add_argument("--env", help="đường dẫn file .env (mặc định: ./.env)")
    sub = parser.add_subparsers(dest="cmd")

    sub.add_parser("run", help="chạy daemon").set_defaults(fn=cmd_run)
    sub.add_parser("doctor", help="kiểm tra cấu hình").set_defaults(fn=cmd_doctor)
    sub.add_parser("chats", help="liệt kê chat đã biết").set_defaults(fn=cmd_chats)

    p_recall = sub.add_parser("recall", help="tra archive")
    p_recall.add_argument("query", nargs="+")
    p_recall.add_argument("--chat", help="giới hạn trong một chat")
    p_recall.add_argument("--limit", type=int, default=20)
    p_recall.set_defaults(fn=cmd_recall)

    p_compact = sub.add_parser("compact", help="ép nén một chat ngay")
    p_compact.add_argument("chat")
    p_compact.set_defaults(fn=cmd_compact)

    p_hook = sub.add_parser("webhook", help="đặt/gỡ webhook")
    p_hook.add_argument("action", choices=["set", "delete"])
    p_hook.set_defaults(fn=cmd_webhook)

    args = parser.parse_args()
    fn = getattr(args, "fn", cmd_run)
    return fn(args)


if __name__ == "__main__":
    raise SystemExit(main())
