"""
Client Bot API — async, một session aiohttp dùng chung.

Ba thứ dễ sai mà lớp này che đi:
  1. **Trần 4096 ký tự.** Vượt là API trả 400 và câu trả lời biến mất hoàn toàn.
     Ở đây tự cắt theo ranh giới dòng/câu, không cắt giữa từ.
  2. **Markdown của Telegram không phải Markdown.** `MarkdownV2` bắt escape 18 ký tự;
     thiếu một dấu là cả tin bị từ chối. Ta gửi HTML — luật escape chỉ có 3 ký tự và
     không thể sai.
  3. **429 Too Many Requests** kèm `retry_after`. Không tôn trọng là bị siết tiếp.
"""

from __future__ import annotations

import asyncio
import html
import json
import re
from pathlib import Path

import aiohttp

from atls import log

_log = log.get("telegram.api")

MAX_CHARS = 4096
API_HOST = "https://api.telegram.org"


class TelegramError(RuntimeError):
    pass


def md_to_html(text: str) -> str:
    """Markdown agent hay viết → HTML tối giản Telegram chấp nhận.

    Telegram chỉ hiểu <b> <i> <u> <s> <code> <pre> <a> <blockquote>. Mọi thẻ khác là
    lỗi 400. Nên: escape sạch trước, rồi mới dựng lại đúng những thẻ đó.
    """
    out = html.escape(text, quote=False)
    # Khối code trước (chúng nuốt mọi cú pháp bên trong), rồi mới tới inline.
    out = re.sub(r"```(?:\w+)?\n?(.*?)```", r"<pre>\1</pre>", out, flags=re.S)
    out = re.sub(r"`([^`\n]+)`", r"<code>\1</code>", out)
    out = re.sub(r"\*\*([^\n*]+)\*\*", r"<b>\1</b>", out)
    out = re.sub(r"(?<![\w*])\*([^\n*]+)\*(?![\w*])", r"<i>\1</i>", out)
    out = re.sub(r"(?<![\w_])__([^\n_]+)__(?![\w_])", r"<u>\1</u>", out)
    out = re.sub(r"\[([^\]]+)\]\((https?://[^)\s]+)\)", r'<a href="\2">\1</a>', out)
    # Heading Markdown không có thẻ tương ứng — in đậm là cách gần nhất.
    out = re.sub(r"^#{1,6}\s*(.+)$", r"<b>\1</b>", out, flags=re.M)
    return out


def split_message(text: str, limit: int = MAX_CHARS) -> list[str]:
    """Cắt theo ranh giới đoạn → dòng → câu → ký tự. Ưu tiên chỗ cắt ít gây khó đọc nhất."""
    if len(text) <= limit:
        return [text]
    chunks: list[str] = []
    rest = text
    while len(rest) > limit:
        window = rest[:limit]
        cut = max(window.rfind("\n\n"), window.rfind("\n"))
        if cut < limit // 2:
            cut = max(window.rfind(". "), window.rfind("! "), window.rfind("? "))
            cut = cut + 1 if cut > limit // 2 else limit
        chunks.append(rest[:cut].rstrip())
        rest = rest[cut:].lstrip()
    if rest:
        chunks.append(rest)
    return chunks


class TelegramAPI:
    def __init__(self, token: str) -> None:
        if not token:
            raise TelegramError("Thiếu TELEGRAM_BOT_TOKEN")
        self._token = token
        self._session: aiohttp.ClientSession | None = None
        self._username: str = ""

    async def __aenter__(self) -> "TelegramAPI":
        self._session = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=None, sock_connect=15)
        )
        return self

    async def __aexit__(self, *exc) -> None:
        if self._session:
            await self._session.close()

    @property
    def username(self) -> str:
        return self._username

    def _url(self, method: str) -> str:
        return f"{API_HOST}/bot{self._token}/{method}"

    async def call(self, method: str, payload: dict | None = None, *, timeout: int = 60) -> dict:
        assert self._session is not None, "TelegramAPI phải dùng trong `async with`"
        # Bỏ key None: Bot API từ chối `reply_to_message_id=null` thay vì coi như vắng mặt.
        data = {k: v for k, v in (payload or {}).items() if v is not None}

        last = ""
        for attempt in range(5):
            try:
                async with self._session.post(
                    self._url(method), json=data,
                    timeout=aiohttp.ClientTimeout(total=timeout + 15),
                ) as resp:
                    raw = await resp.text()
                    status = resp.status
            except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
                if attempt == 4:
                    raise TelegramError(f"{method}: mất kết nối — {exc}") from exc
                # Backoff luỹ thừa: mạng chập chờn thì thử ngay lại chỉ làm tệ hơn.
                await asyncio.sleep(2 ** attempt)
                continue

            # Không phải JSON = reverse proxy / CDN trả trang lỗi HTML thay cho Bot API.
            # Chuyện này CÓ xảy ra thật (502 từ Cloudflare) và trước đây nó ném
            # `json.JSONDecodeError` xuyên qua mọi lớp bắt lỗi bên dưới rồi giết luôn
            # vòng long-poll — daemon còn sống, còn ghi log khởi động, mà điếc vĩnh viễn.
            # Đây là lỗi tạm thời, phải xử như mất kết nối chứ không phải như lỗi cứng.
            try:
                body = json.loads(raw)
            except ValueError:
                last = f"HTTP {status}, phản hồi không phải JSON: {raw[:160]!r}"
                if attempt == 4:
                    raise TelegramError(f"{method}: {last}")
                _log.warning("%s trả phản hồi lạ (%s), thử lại", method, last)
                await asyncio.sleep(2 ** attempt)
                continue

            if body.get("ok"):
                return body.get("result")
            if status == 429:
                wait = int((body.get("parameters") or {}).get("retry_after", 3))
                _log.warning("429 từ %s, chờ %ds", method, wait)
                await asyncio.sleep(wait)
                continue
            raise TelegramError(f"{method} thất bại ({status}): {body.get('description')}")
        raise TelegramError(f"{method}: hết lượt thử{(' — ' + last) if last else ''}")

    # ── vòng đời ─────────────────────────────────────────────────────────────

    async def resolve_me(self) -> str:
        me = await self.call("getMe")
        self._username = me.get("username", "")
        return self._username

    async def get_updates(self, offset: int | None, timeout: int = 50) -> list[dict]:
        return await self.call(
            "getUpdates",
            {"offset": offset, "timeout": timeout,
             "allowed_updates": ["message", "edited_message", "channel_post", "edited_channel_post"]},
            timeout=timeout,
        ) or []

    async def set_webhook(self, url: str, secret: str) -> None:
        await self.call("setWebhook", {
            "url": url,
            "secret_token": secret or None,
            # `drop_pending_updates` cố ý để False: restart sau sự cố thì các tin
            # đến trong lúc chết vẫn phải được xử lý, không được im lặng nuốt.
            "drop_pending_updates": False,
            "allowed_updates": ["message", "edited_message", "channel_post", "edited_channel_post"],
            "max_connections": 40,
        })

    async def delete_webhook(self) -> None:
        await self.call("deleteWebhook", {"drop_pending_updates": False})

    # ── gửi ──────────────────────────────────────────────────────────────────

    async def send_message(self, chat_id: str, text: str, *, reply_to: int | None = None) -> list[int]:
        """Gửi (tự cắt nếu dài). Trả về danh sách message_id đã gửi."""
        ids: list[int] = []
        chunks = split_message(text)
        for i, chunk in enumerate(chunks):
            payload = {
                "chat_id": chat_id,
                "text": md_to_html(chunk),
                "parse_mode": "HTML",
                "link_preview_options": {"is_disabled": True},
                # Chỉ mảnh đầu mới reply — reply cả 5 mảnh vào cùng một tin trông như spam.
                "reply_to_message_id": reply_to if i == 0 else None,
            }
            try:
                res = await self.call("sendMessage", payload)
            except TelegramError as exc:
                # HTML hỏng (agent viết `<` giữa câu, thẻ lồng sai) là lỗi 400 và MẤT
                # câu trả lời. Gửi lại dạng thuần — xấu còn hơn không tới.
                _log.warning("gửi HTML lỗi (%s), gửi lại dạng thuần", exc)
                res = await self.call("sendMessage", {
                    "chat_id": chat_id, "text": chunk,
                    "link_preview_options": {"is_disabled": True},
                })
            ids.append(int(res.get("message_id", 0)))
        return ids

    async def send_chat_action(self, chat_id: str, action: str = "typing") -> None:
        try:
            await self.call("sendChatAction", {"chat_id": chat_id, "action": action}, timeout=10)
        except TelegramError:
            pass  # chỉ là hiệu ứng "đang gõ" — hỏng thì kệ, đừng làm hỏng lượt

    async def send_document(self, chat_id: str, path: Path, caption: str = "") -> int:
        return await self._send_file("sendDocument", "document", chat_id, path, caption)

    async def send_photo(self, chat_id: str, path: Path, caption: str = "") -> int:
        return await self._send_file("sendPhoto", "photo", chat_id, path, caption)

    async def _send_file(
        self, method: str, field: str, chat_id: str, path: Path, caption: str
    ) -> int:
        assert self._session is not None
        if not path.exists():
            raise TelegramError(f"Không có file để gửi: {path}")
        form = aiohttp.FormData()
        form.add_field("chat_id", str(chat_id))
        if caption:
            # Caption có trần riêng 1024 ký tự, khác hẳn 4096 của text.
            form.add_field("caption", md_to_html(caption[:1024]))
            form.add_field("parse_mode", "HTML")
        form.add_field(field, path.read_bytes(), filename=path.name)
        async with self._session.post(self._url(method), data=form) as resp:
            body = await resp.json(content_type=None)
        if not body.get("ok"):
            raise TelegramError(f"{method} thất bại: {body.get('description')}")
        return int(body["result"].get("message_id", 0))

    # ── nhận file ────────────────────────────────────────────────────────────

    async def download(self, file_id: str, dest_dir: Path) -> Path | None:
        """Tải file người dùng gửi về `dest_dir`. Trả `None` nếu không tải được."""
        assert self._session is not None
        try:
            info = await self.call("getFile", {"file_id": file_id})
            remote = info.get("file_path")
            if not remote:
                return None
            dest_dir.mkdir(parents=True, exist_ok=True)
            dest = dest_dir / Path(remote).name
            url = f"{API_HOST}/file/bot{self._token}/{remote}"
            async with self._session.get(url) as resp:
                resp.raise_for_status()
                dest.write_bytes(await resp.read())
            return dest
        except Exception as exc:  # noqa: BLE001 — file hỏng không được giết lượt chat
            _log.warning("tải file %s thất bại: %s", file_id, exc)
            return None
