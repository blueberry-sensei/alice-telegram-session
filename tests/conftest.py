from __future__ import annotations

import time
from pathlib import Path

import pytest

from atls.memory.tokens import count_tokens
from atls.store import Store
from atls.telegram.model import parse_update


@pytest.fixture()
def store(tmp_path: Path) -> Store:
    s = Store(tmp_path / "test.db")
    yield s
    s.close()


@pytest.fixture()
def chat(store: Store) -> str:
    store.upsert_chat("-100200", "supergroup", "Phòng thử")
    return "-100200"


def make_update(
    update_id: int, text: str, *, chat_id: str = "-100200",
    chat_type: str = "supergroup", sender: str = "Bệ hạ", sender_id: int = 7,
    is_bot: bool = False, reply_to: dict | None = None, entities: list | None = None,
    edited: bool = False,
) -> dict:
    msg = {
        "message_id": update_id * 10,
        "date": int(time.time()),
        "chat": {"id": int(chat_id), "type": chat_type, "title": "Phòng thử"},
        "from": {"id": sender_id, "first_name": sender, "is_bot": is_bot},
        "text": text,
    }
    if reply_to:
        msg["reply_to_message"] = reply_to
    if entities:
        msg["entities"] = entities
    return {"update_id": update_id, "edited_message" if edited else "message": msg}


def add(store: Store, chat_id: str, text: str, *, role: str = "human",
        sender: str = "Bệ hạ", update_id: int | None = None) -> int | None:
    return store.add_message(
        chat_id=chat_id, role=role, text=text, tokens=count_tokens(text),
        sender_name=sender, update_id=update_id,
    )


def bot_reply(message_id: int = 999, username: str = "alice_bot") -> dict:
    """Tin của bot để reply vào. `username` là cách phân biệt bot MÌNH với bot khác."""
    who = {"id": 1, "is_bot": True}
    if username:
        who["username"] = username
    return {"message_id": message_id, "from": who, "text": "dạ"}


__all__ = ["make_update", "add", "bot_reply", "parse_update"]
