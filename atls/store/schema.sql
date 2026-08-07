-- Alice Telegram Session — lược đồ SQLite
--
-- Hai luật xuyên suốt:
--   1. `messages` là ARCHIVE VĨNH VIỄN. Không có đường xoá. Nén không đụng tới nó.
--   2. `update_id` là UNIQUE. Webhook Telegram CÓ gửi lại khi ta trả HTTP chậm hoặc
--      lỗi; `INSERT OR IGNORE` biến toàn bộ pipeline thành idempotent mà không cần
--      thêm một dòng logic nào ở tầng trên.

PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS chats (
    chat_id      TEXT PRIMARY KEY,
    kind         TEXT NOT NULL,              -- private | group | supergroup | channel
    title        TEXT NOT NULL DEFAULT '',
    enabled      INTEGER NOT NULL DEFAULT 1,
    persona      TEXT,                       -- override ATLS_PERSONA cho riêng chat này
    agent        TEXT,                       -- override ATLS_AGENT cho riêng chat này
    first_seen   REAL NOT NULL,
    last_seen    REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS messages (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    update_id    INTEGER UNIQUE,             -- NULL cho tin do chính agent gửi ra
    chat_id      TEXT NOT NULL,
    tg_message_id INTEGER,
    ts           REAL NOT NULL,
    role         TEXT NOT NULL,              -- human | agent | system
    sender_id    TEXT NOT NULL DEFAULT '',
    sender_name  TEXT NOT NULL DEFAULT '',
    text         TEXT NOT NULL DEFAULT '',   -- ĐÃ redact trước khi ghi
    reply_to     INTEGER,                    -- tg_message_id được reply tới
    addressed    INTEGER NOT NULL DEFAULT 0, -- 1 = tin này gọi thẳng agent
    media        TEXT,                       -- JSON: {kind, file_id, path, name}
    tokens       INTEGER NOT NULL DEFAULT 0,
    FOREIGN KEY (chat_id) REFERENCES chats(chat_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_messages_chat_ts ON messages(chat_id, ts);
CREATE INDEX IF NOT EXISTS idx_messages_chat_id_pk ON messages(chat_id, id);
CREATE INDEX IF NOT EXISTS idx_messages_sender ON messages(chat_id, sender_id, ts);

-- Tìm kiếm toàn văn trên archive — đây là thứ trả lời được "tuần trước ông này nói gì".
-- `content=` trỏ về bảng gốc nên FTS không nhân đôi dữ liệu.
CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts USING fts5(
    text,
    sender_name,
    content = 'messages',
    content_rowid = 'id',
    tokenize = 'unicode61 remove_diacritics 2'
);

CREATE TRIGGER IF NOT EXISTS messages_ai AFTER INSERT ON messages BEGIN
    INSERT INTO messages_fts(rowid, text, sender_name)
    VALUES (new.id, new.text, new.sender_name);
END;

CREATE TRIGGER IF NOT EXISTS messages_ad AFTER DELETE ON messages BEGIN
    INSERT INTO messages_fts(messages_fts, rowid, text, sender_name)
    VALUES ('delete', old.id, old.text, old.sender_name);
END;

CREATE TRIGGER IF NOT EXISTS messages_au AFTER UPDATE ON messages BEGIN
    INSERT INTO messages_fts(messages_fts, rowid, text, sender_name)
    VALUES ('delete', old.id, old.text, old.sender_name);
    INSERT INTO messages_fts(rowid, text, sender_name)
    VALUES (new.id, new.text, new.sender_name);
END;

-- Mỗi row phủ một khoảng [from_msg_id, to_msg_id] LIÊN TỤC và KHÔNG chồng lấn.
-- Row mới nhất là "running summary": nó đã bao gồm nội dung của mọi row trước nó,
-- nên khi dựng cửa sổ chỉ cần lấy MỘT row cuối, không phải nối cả chuỗi.
CREATE TABLE IF NOT EXISTS summaries (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id      TEXT NOT NULL,
    from_msg_id  INTEGER NOT NULL,
    to_msg_id    INTEGER NOT NULL,
    covered      INTEGER NOT NULL DEFAULT 0, -- số tin đã nén (tích luỹ)
    text         TEXT NOT NULL,
    tokens       INTEGER NOT NULL DEFAULT 0,
    created_at   REAL NOT NULL,
    FOREIGN KEY (chat_id) REFERENCES chats(chat_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_summaries_chat ON summaries(chat_id, to_msg_id);

CREATE TABLE IF NOT EXISTS sessions (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id          TEXT NOT NULL,
    agent            TEXT NOT NULL,
    agent_session_id TEXT NOT NULL,          -- id phía CLI (uuid cho claude, v.v.)
    started          INTEGER NOT NULL DEFAULT 0, -- 1 = CLI đã thật sự tạo session
    created_at       REAL NOT NULL,
    last_used_at     REAL NOT NULL,
    turns            INTEGER NOT NULL DEFAULT 0,
    closed_at        REAL,
    close_reason     TEXT,                   -- max_age | idle | reset | resume_failed | agent_changed
    FOREIGN KEY (chat_id) REFERENCES chats(chat_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_sessions_open ON sessions(chat_id, closed_at);

-- Việc agent phải nhờ người thật làm (đăng nhập, nhập OTP, bấm xác nhận).
-- Agent KHÔNG BAO GIỜ tự nhập mật khẩu hay giải CAPTCHA — nó mở một gate rồi chờ.
CREATE TABLE IF NOT EXISTS pending_gates (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id      TEXT NOT NULL,
    kind         TEXT NOT NULL,              -- login | confirm | otp
    what         TEXT NOT NULL,
    created_at   REAL NOT NULL,
    resolved_at  REAL,
    resolved_by  TEXT,
    FOREIGN KEY (chat_id) REFERENCES chats(chat_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_gates_open ON pending_gates(chat_id, resolved_at);

-- Con trỏ long-poll và các giá trị lặt vặt khác.
CREATE TABLE IF NOT EXISTS kv (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
