"""
Cấu hình — đọc `.env` + biến môi trường, một lần, thành object bất biến.

Nguyên tắc: KHÔNG module nào khác được gọi `os.environ` trực tiếp. Mọi giá trị điều
chỉnh được đều phải xuất hiện ở đây và ở `.env.example` — nếu không, sáu tháng nữa
sẽ có một hằng số ma nằm giữa file nào đó mà không ai biết đổi ở đâu.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def _load_dotenv(path: Path) -> None:
    """Nạp `.env` vào os.environ, KHÔNG ghi đè biến môi trường đã có.

    Thứ tự ưu tiên đó là cố ý: trên server người ta set env thật (systemd, Docker),
    và một file `.env` sót lại trong image không được phép giành quyền.
    """
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def _int(key: str, default: int) -> int:
    raw = os.environ.get(key, "").strip()
    try:
        return int(raw) if raw else default
    except ValueError:
        return default


def _bool(key: str, default: bool = False) -> bool:
    raw = os.environ.get(key, "").strip().lower()
    if not raw:
        return default
    return raw in ("1", "true", "yes", "on")


def _csv(key: str) -> list[str]:
    raw = os.environ.get(key, "")
    return [p.strip() for p in raw.split(",") if p.strip()]


@dataclass(frozen=True)
class Config:
    # Telegram
    bot_token: str
    allowed_chats: frozenset[str]

    # Ingest
    ingest: str
    webhook_url: str
    webhook_host: str
    webhook_port: int
    webhook_secret: str

    # Agent
    agent: str
    agent_model: str
    agent_cwd: Path
    agent_cmd: str

    # Session
    session_max_age: int
    session_idle: int

    # Memory
    window_tokens: int
    compact_trigger: int
    keep_raw: int

    # Nhịp
    debounce: int
    debounce_max: int
    ack_after: int

    # Đồng thời
    workers: int

    # Persona
    persona: str
    triggers: tuple[str, ...]

    # Cầu nối Alice Coding
    brain_bridge: bool
    knowledge_dir: Path | None

    # Vận hành
    data_dir: Path
    log_level: str

    # Suy ra
    db_path: Path = field(init=False)
    inbox_dir: Path = field(init=False)
    outbox_dir: Path = field(init=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "db_path", self.data_dir / "atls.db")
        object.__setattr__(self, "inbox_dir", self.data_dir / "inbox")
        object.__setattr__(self, "outbox_dir", self.data_dir / "outbox")

    def chat_allowed(self, chat_id: str | int) -> bool:
        """Danh sách rỗng = nhận mọi chat. Tiện lúc thử, phải điền lúc chạy thật."""
        return not self.allowed_chats or str(chat_id) in self.allowed_chats

    def ensure_dirs(self) -> None:
        for d in (self.data_dir, self.inbox_dir, self.outbox_dir):
            d.mkdir(parents=True, exist_ok=True)


def load(env_file: Path | None = None) -> Config:
    _load_dotenv(env_file or (REPO_ROOT / ".env"))

    data_dir = Path(os.environ.get("ATLS_DATA_DIR") or ".atls")
    if not data_dir.is_absolute():
        data_dir = REPO_ROOT / data_dir

    agent_cwd = Path(os.environ.get("ATLS_AGENT_CWD") or REPO_ROOT)
    knowledge_raw = os.environ.get("ATLS_KNOWLEDGE_DIR", "").strip()

    window = _int("ATLS_WINDOW_TOKENS", 20_000)
    trigger = _int("ATLS_COMPACT_TRIGGER", 16_000)
    if trigger >= window:
        # Ngưỡng nén phải nằm DƯỚI trần cửa sổ, nếu không cửa sổ chạm trần trước khi
        # nén kịp chạy và ta rơi vào cắt cứng mỗi lượt. Sửa lặng lẽ còn hơn chết ngầm.
        trigger = int(window * 0.8)

    return Config(
        bot_token=os.environ.get("TELEGRAM_BOT_TOKEN", "").strip(),
        allowed_chats=frozenset(_csv("ATLS_ALLOWED_CHATS")),
        ingest=(os.environ.get("ATLS_INGEST") or "polling").strip().lower(),
        webhook_url=os.environ.get("ATLS_WEBHOOK_URL", "").strip(),
        webhook_host=os.environ.get("ATLS_WEBHOOK_HOST") or "0.0.0.0",
        webhook_port=_int("ATLS_WEBHOOK_PORT", 8443),
        webhook_secret=os.environ.get("ATLS_WEBHOOK_SECRET", "").strip(),
        agent=(os.environ.get("ATLS_AGENT") or "claude").strip().lower(),
        agent_model=os.environ.get("ATLS_AGENT_MODEL", "").strip(),
        agent_cwd=agent_cwd,
        agent_cmd=os.environ.get("ATLS_AGENT_CMD", "").strip(),
        session_max_age=_int("ATLS_SESSION_MAX_AGE", 12 * 3600),
        session_idle=_int("ATLS_SESSION_IDLE", 3 * 3600),
        window_tokens=window,
        compact_trigger=trigger,
        keep_raw=_int("ATLS_KEEP_RAW", 12),
        debounce=_int("ATLS_DEBOUNCE", 10),
        debounce_max=_int("ATLS_DEBOUNCE_MAX", 120),
        ack_after=_int("ATLS_ACK_AFTER", 12),
        workers=max(1, _int("ATLS_WORKERS", 3)),
        persona=(os.environ.get("ATLS_PERSONA") or "alice").strip(),
        triggers=tuple(t.lower() for t in _csv("ATLS_TRIGGERS")),
        brain_bridge=_bool("ATLS_BRAIN_BRIDGE"),
        knowledge_dir=Path(knowledge_raw) if knowledge_raw else None,
        data_dir=data_dir,
        log_level=(os.environ.get("ATLS_LOG_LEVEL") or "INFO").upper(),
    )
