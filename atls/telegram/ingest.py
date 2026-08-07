"""
Lớp nhận tin — hai đường vào, cùng một đầu ra.

Cả `WebhookIngest` lẫn `PollingIngest` đều đẩy `dict` update thô vào cùng một
`asyncio.Queue`. Mọi tầng phía sau không biết và không cần biết tin tới bằng đường
nào — đó là lý do đổi từ polling sang webhook lúc lên server chỉ là đổi một biến env.

Webhook cần URL public HTTPS; polling chạy được sau NAT. Cùng một pipeline.
"""

from __future__ import annotations

import asyncio
from typing import Protocol

from aiohttp import web

from atls import log
from atls.store import Store
from atls.telegram.api import TelegramAPI, TelegramError

_log = log.get("telegram.ingest")

_OFFSET_KEY = "polling_offset"


class Ingest(Protocol):
    async def start(self) -> None: ...
    async def stop(self) -> None: ...


class PollingIngest:
    """Long-poll `getUpdates`. Con trỏ offset nằm trong SQLite, không nằm ở file rời —
    một nguồn sự thật, và nó sống sót qua restart cùng lúc với archive."""

    def __init__(self, api: TelegramAPI, store: Store, queue: asyncio.Queue) -> None:
        self._api = api
        self._store = store
        self._queue = queue
        self._task: asyncio.Task | None = None

    async def start(self) -> None:
        # `getUpdates` và webhook loại trừ nhau: còn webhook đang đặt thì mọi lần gọi
        # `getUpdates` trả 409 Conflict và daemon câm mà không báo gì rõ ràng. Gỡ
        # trước, giữ nguyên các tin đang chờ.
        try:
            await self._api.delete_webhook()
        except TelegramError as exc:
            _log.warning("không gỡ được webhook cũ: %s", exc)
        self._task = asyncio.create_task(self._loop(), name="atls-polling")
        _log.info("nhận tin bằng long-poll")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    def _offset(self) -> int | None:
        raw = self._store.get_kv(_OFFSET_KEY)
        return int(raw) if raw else None

    async def _loop(self) -> None:
        backoff = 1
        while True:
            try:
                updates = await self._api.get_updates(self._offset(), timeout=50)
                backoff = 1
                for upd in updates:
                    # Ghi offset cho MỌI update, kể cả loại ta không xử lý. Bỏ sót
                    # bước này thì một `callback_query` lạ kẹt vĩnh viễn ở đầu hàng
                    # đợi và daemon quay vòng nóng, ngốn CPU mà không làm gì.
                    self._store.set_kv(_OFFSET_KEY, str(int(upd["update_id"]) + 1))
                    await self._queue.put(upd)
            except asyncio.CancelledError:
                raise
            except TelegramError as exc:
                _log.warning("long-poll lỗi: %s (thử lại sau %ds)", exc, backoff)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 60)


class WebhookIngest:
    """Nhận push từ Telegram.

    Hai luật của webhook, sai là mất tin:
      1. **Trả 200 ngay.** Telegram coi phản hồi chậm là thất bại và gửi lại. Xử lý
         phải nằm sau hàng đợi, không nằm trong handler.
      2. **Kiểm `X-Telegram-Bot-Api-Secret-Token`.** URL webhook có token bot trong
         đường dẫn thì ai đoán được URL cũng bơm được update giả.
    """

    def __init__(
        self, api: TelegramAPI, queue: asyncio.Queue, *,
        url: str, host: str, port: int, secret: str, path: str = "/atls/webhook",
    ) -> None:
        self._api = api
        self._queue = queue
        self._url = url.rstrip("/")
        self._host = host
        self._port = port
        self._secret = secret
        self._path = path
        self._runner: web.AppRunner | None = None

    async def start(self) -> None:
        app = web.Application()
        app.router.add_post(self._path, self._handle)
        app.router.add_get("/healthz", lambda _r: web.json_response({"ok": True}))

        self._runner = web.AppRunner(app, access_log=None)
        await self._runner.setup()
        await web.TCPSite(self._runner, self._host, self._port).start()

        full = f"{self._url}{self._path}"
        await self._api.set_webhook(full, self._secret)
        _log.info("nhận tin bằng webhook tại %s (nghe %s:%d)", full, self._host, self._port)

    async def stop(self) -> None:
        # KHÔNG gọi deleteWebhook: nếu xoá thì trong lúc process chết, Telegram vứt
        # bỏ mọi tin thay vì xếp hàng chờ. Giữ webhook = tin được giữ lại tối đa 24h.
        if self._runner:
            await self._runner.cleanup()

    async def _handle(self, request: web.Request) -> web.Response:
        if self._secret and request.headers.get("X-Telegram-Bot-Api-Secret-Token") != self._secret:
            _log.warning("từ chối request webhook sai secret từ %s", request.remote)
            return web.Response(status=403)
        try:
            update = await request.json()
        except Exception:  # noqa: BLE001
            return web.Response(status=400)
        # Đẩy vào hàng đợi rồi trả 200 NGAY. Mọi xử lý nặng nằm ở phía tiêu thụ.
        await self._queue.put(update)
        return web.Response(status=200)


def build_ingest(cfg, api: TelegramAPI, store: Store, queue: asyncio.Queue) -> Ingest:
    if cfg.ingest == "webhook":
        if not cfg.webhook_url:
            raise TelegramError("ATLS_INGEST=webhook nhưng thiếu ATLS_WEBHOOK_URL")
        return WebhookIngest(
            api, queue, url=cfg.webhook_url, host=cfg.webhook_host,
            port=cfg.webhook_port, secret=cfg.webhook_secret,
        )
    return PollingIngest(api, store, queue)
