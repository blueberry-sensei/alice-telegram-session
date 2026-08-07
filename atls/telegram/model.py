"""
Chuẩn hoá update của Telegram thành một dạng duy nhất.

Bot API trả về sáu hình dạng khác nhau cho cùng một khái niệm "có người vừa nói gì
đó": `message`, `edited_message`, `channel_post`, `edited_channel_post`,
`business_message`, và tin trong topic của forum. Nếu để nguyên thì mọi tầng phía
sau đều phải biết cả sáu. Ép về một `Incoming` ở đây, một lần.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# Thứ tự có ý nghĩa: `edited_*` đứng sau bản gốc để khi cả hai cùng có (không xảy ra
# trong thực tế nhưng API không cấm) ta lấy bản gốc.
_MESSAGE_KEYS = (
    "message",
    "channel_post",
    "business_message",
    "edited_message",
    "edited_channel_post",
)

# Một lệnh THẬT: `/tên` hoặc `/tên@bot`, rồi hết dòng hoặc khoảng trắng.
#
# `[^\s@/]+` (cấm cả dấu `/`) là chỗ dễ bỏ sót nhất: thiếu nó thì `/home/user/log.txt`
# — một đường dẫn ai đó dán vào group — được coi là lệnh `/home/user/log.txt` và đánh
# thức agent. Lớp ký tự này cũng cố ý KHÔNG giới hạn về ASCII, vì `/nhớ` là lệnh thật
# của ATLS còn Telegram thì chỉ gắn entity `bot_command` cho lệnh ASCII.
_COMMAND = re.compile(r"^/(?P<name>[^\s@/]+)(?:@(?P<target>[A-Za-z0-9_]+))?(?:\s|$)")

_MEDIA_KEYS = (
    ("photo", "ảnh"),
    ("document", "tài liệu"),
    ("audio", "âm thanh"),
    ("voice", "tin thoại"),
    ("video", "video"),
    ("video_note", "video tròn"),
    ("animation", "ảnh động"),
    ("sticker", "sticker"),
)


@dataclass(frozen=True)
class Incoming:
    update_id: int
    chat_id: str
    chat_kind: str          # private | group | supergroup | channel
    chat_title: str
    message_id: int
    ts: float
    sender_id: str
    sender_name: str
    sender_is_bot: bool
    text: str
    reply_to_id: int | None
    reply_to_text: str
    reply_to_is_bot: bool
    reply_to_username: str = ""
    entities: list[dict] = field(default_factory=list)
    media: dict | None = None
    edited: bool = False

    @property
    def is_command(self) -> bool:
        return bool(_COMMAND.match(self.text))

    def command(self) -> tuple[str, str]:
        """Tách `/lệnh@bot tham số` thành `("lệnh", "tham số")`. Không phải lệnh → `("", "")`."""
        m = _COMMAND.match(self.text)
        if not m:
            return "", ""
        return m.group("name").lower(), self.text[m.end():].strip()

    def command_target(self) -> str:
        """Phần `@bot` trong `/lệnh@bot`, thường rỗng.

        Trong group nhiều bot, đây là cách DUY NHẤT biết lệnh dành cho ai. Bỏ qua nó
        thì `/poll@othersbot` cũng đánh thức ta.
        """
        m = _COMMAND.match(self.text)
        return (m.group("target") or "").lower() if m else ""

    def command_is_for(self, bot_username: str) -> bool:
        """Lệnh không ghi đích thì coi như dành cho mọi bot trong phòng — kể cả ta."""
        target = self.command_target()
        return not target or target == (bot_username or "").lower()

    def mentions(self, bot_username: str) -> bool:
        if not bot_username:
            return False
        return f"@{bot_username}".lower() in self.text.lower()

    def replies_to(self, bot_username: str) -> bool:
        """Reply vào tin của CHÍNH bot này, không phải một bot bất kỳ.

        `reply_to_is_bot` đơn thuần là sai trong group có từ hai bot: người ta reply
        vào tin của bot kia và ta nhảy vào trả lời — đúng kiểu lắm lời khiến bot bị tắt.
        Chỉ khi Telegram không cho biết username (hiếm) mới rơi về cờ chung.
        """
        if not self.reply_to_is_bot:
            return False
        if not self.reply_to_username or not bot_username:
            return True
        return self.reply_to_username.lower() == bot_username.lower()


def _sender_name(user: dict) -> str:
    if not user:
        return ""
    parts = [user.get("first_name") or "", user.get("last_name") or ""]
    full = " ".join(p for p in parts if p).strip()
    return full or user.get("username") or ""


def _extract_media(msg: dict) -> dict | None:
    for key, label in _MEDIA_KEYS:
        item = msg.get(key)
        if not item:
            continue
        # `photo` là MẢNG các kích cỡ, sắp xếp tăng dần. Phần tử cuối là bản to nhất —
        # lấy phần tử đầu là tải về một cái thumbnail 90px và tưởng đó là ảnh thật.
        if key == "photo" and isinstance(item, list):
            item = item[-1]
        if not isinstance(item, dict):
            continue
        return {
            "kind": label,
            "file_id": item.get("file_id", ""),
            "name": item.get("file_name") or "",
            "mime": item.get("mime_type") or "",
            "size": item.get("file_size") or 0,
        }
    return None


def parse_update(update: dict) -> Incoming | None:
    """Trả `None` cho update không phải tin nhắn (callback_query, poll, my_chat_member…).

    Người gọi vẫn PHẢI ghi nhận offset cho những update đó — bỏ qua mà không ghi
    offset thì update lạ kẹt vĩnh viễn ở đầu hàng đợi và daemon quay vòng nóng.
    """
    msg = None
    edited = False
    for key in _MESSAGE_KEYS:
        if key in update:
            msg = update[key]
            edited = key.startswith("edited_")
            break
    if not isinstance(msg, dict):
        return None

    chat = msg.get("chat") or {}
    chat_id = chat.get("id")
    if chat_id is None:
        return None

    reply = msg.get("reply_to_message") or {}
    sender = msg.get("from") or {}

    # Channel post không có `from`. Nguồn tin là chính channel — dùng tên channel
    # làm tên người gửi, nếu không mọi dòng archive của channel đều mang tên rỗng.
    sender_name = _sender_name(sender) or (
        chat.get("title", "") if chat.get("type") == "channel" else ""
    )

    return Incoming(
        update_id=int(update.get("update_id", 0)),
        chat_id=str(chat_id),
        chat_kind=chat.get("type") or "private",
        chat_title=chat.get("title") or _sender_name(sender) or "",
        message_id=int(msg.get("message_id") or 0),
        ts=float(msg.get("date") or 0),
        sender_id=str(sender.get("id") or chat_id),
        sender_name=sender_name or "người lạ",
        sender_is_bot=bool(sender.get("is_bot")),
        text=(msg.get("text") or msg.get("caption") or "").strip(),
        reply_to_id=int(reply["message_id"]) if reply.get("message_id") else None,
        reply_to_text=(reply.get("text") or reply.get("caption") or "").strip(),
        reply_to_is_bot=bool((reply.get("from") or {}).get("is_bot")),
        reply_to_username=str((reply.get("from") or {}).get("username") or ""),
        entities=list(msg.get("entities") or msg.get("caption_entities") or []),
        media=_extract_media(msg),
        edited=edited,
    )
