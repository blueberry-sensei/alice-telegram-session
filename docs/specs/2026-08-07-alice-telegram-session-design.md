# Alice Telegram Session (ATLS) — Thiết kế

> Trạng thái: APPROVED · Ngày: 2026-08-07 · Tác giả: Alice + Blueberry Sensei

## 0. Một câu

ATLS là **lớp phiên và trí nhớ hội thoại** đặt giữa Telegram và một agent CLI bất kỳ,
để agent sống 24/7 với người dùng mà không quên chuyện cũ và không phình context.

Alice Coding trả lời câu hỏi *"project này đã quyết gì, đã vấp gì"* (trí nhớ **công việc**).
ATLS trả lời câu hỏi *"tuần trước ông này nói gì với mình"* (trí nhớ **hội thoại**).
Hai lớp độc lập, cắm vào nhau thì được một cộng sự thật sự.

## 1. Vấn đề

Một agent nối vào Telegram theo cách ngây thơ sẽ chết theo đúng bốn cách sau:

| Triệu chứng | Nguyên nhân gốc |
|---|---|
| "Tuần trước ông này chửi mình cái gì nhỉ?" | Lịch sử chat không được lưu, hoặc lưu bằng buffer cuộn xoá dòng cũ |
| Trả lời chậm dần rồi tràn context | Nhồi cả lịch sử vào mỗi lượt, không có cơ chế nén |
| Hai agent cùng chạy, ghi đè nhau | Không có khoá; mỗi tin nhắn sinh một tiến trình |
| Bot lắm lời, bị tắt sau ba ngày | Không có cổng "việc này có phải của mình không" |

## 2. Bốn ràng buộc cứng

1. **Session sống tối đa 12 giờ.** Quá hạn → mở session mới sạch, nhưng **không mất trí nhớ**:
   cửa sổ hội thoại đã nén được dán vào session mới.
2. **Không bao giờ có hai agent cùng chạy trên một chat.** Các chat khác nhau chạy song song,
   giới hạn bởi worker pool.
3. **Cửa sổ hội thoại ≤ 20k token.** Vượt ngưỡng → auto-compact phần cũ thành tóm tắt.
4. **Nghe 24/7.** Mất kết nối, restart máy, webhook gửi trùng — không được mất tin, không được xử lý hai lần.

## 3. Kiến trúc

```
Telegram
   │  webhook (prod) │ long-poll (dev)
   ▼
┌────────────┐   update_id UNIQUE → idempotent
│  Ingest    │──────────────────────────────► SQLite: messages (archive vĩnh viễn)
└─────┬──────┘
      ▼
┌────────────┐  gom tin gõ liên tiếp thành một chùm
│  Debounce  │  (10s im lặng, trần 120s), theo TỪNG chat
└─────┬──────┘
      ▼
┌────────────┐  mention / reply / command / private / trigger?
│  Router    │  không → chỉ lưu archive, KHÔNG gọi agent
└─────┬──────┘
      ▼
┌────────────┐  semaphore(N) + ChatLock(chat_id) + ResourceLock(chrome, brain)
│ Dispatcher │
└─────┬──────┘
      ▼
┌────────────┐  [summary] + [N tin gần nhất] ≤ 20k token
│ Window     │  quá ngưỡng → Compactor nén phần cũ
└─────┬──────┘
      ▼
┌────────────┐  chọn/xoay session (12h TTL), gọi CLI
│  Adapter   │  claude · codex · opencode · antigravity · custom
└─────┬──────┘
      ▼
   Telegram (typing… → ack nếu lâu → câu trả lời)
```

## 4. Các thành phần

### 4.1 Store (`atls/store/`)

SQLite một file, `journal_mode=WAL` (nhiều worker ghi song song không chặn nhau).

| Bảng | Vai trò | Ghi chú |
|---|---|---|
| `chats` | metadata mỗi chat + cấu hình override | title, kind, persona, agent, enabled |
| `messages` | **archive vĩnh viễn**, không bao giờ xoá | `UNIQUE(update_id)` = chống xử lý trùng |
| `summaries` | các đoạn nén, chuỗi liên tiếp | mỗi row phủ khoảng `[from_msg_id, to_msg_id]` |
| `sessions` | vòng đời session theo chat | `agent_session_id`, `created_at`, TTL |
| `pending_gates` | chờ người thật (login, xác nhận) | agent hỏi → user `/done` |

`UNIQUE(update_id)` là điểm mấu chốt: webhook Telegram **có** gửi lại khi ta trả HTTP chậm.
`INSERT OR IGNORE` biến toàn bộ pipeline thành idempotent mà không cần logic gì thêm.

### 4.2 Memory (`atls/memory/`)

- `tokens.py` — đếm token. `tiktoken` nếu có, không thì heuristic (ký tự/3.6 cho tiếng Việt).
- `window.py` — dựng cửa sổ: `[summary mới nhất] + [tin thô mới nhất...]` cho tới khi chạm budget.
- `compactor.py` — khi phần thô vượt `COMPACT_TRIGGER` (mặc định 16k):
  1. Chọn các tin cũ, giữ lại `KEEP_RAW` tin gần nhất (mặc định 12).
  2. Gọi agent CLI (lượt headless rẻ, không session) với `prompts/compact.md`.
  3. Ghi `summaries` row mới **nối tiếp** summary cũ (running summary, không chồng chất).

Compaction chạy **trước** khi dựng prompt, đồng bộ trong lượt — người dùng chờ thêm vài giây
một lần mỗi ~40 tin, đổi lại không bao giờ tràn.

### 4.3 Session (`atls/session/manager.py`)

Một session **cho mỗi chat**. Xoay khi thoả **bất kỳ** điều nào:

| Điều kiện | Mặc định | Lý do |
|---|---|---|
| Tuổi session | > 12h | Ràng buộc cứng #1 — rác context từ phiên làm việc trước |
| Im lặng | > 3h | Chuyện mới thì nên bắt đầu sạch |
| Resume lỗi | ngay | CLI dọn session cũ là chuyện thường, không phải lỗi đáng báo |
| `/reset` | ngay | Người dùng chủ động |

Xoay session **không** làm mất trí nhớ: session mới nhận cửa sổ đã nén qua prompt.
Đây là lý do memory layer phải độc lập với session layer.

### 4.4 Runtime (`atls/runtime/`)

**Debounce theo chat.** Người ta tách một ý thành ba tin. Chờ 10s im lặng rồi mới xử,
mỗi tin mới reset cửa sổ, trần 120s để một người gõ liên tục không giữ agent câm mãi.

**Router — cổng "việc này của mình không".** Deterministic, rẻ, không gọi model:

| Đường vào | Có gọi agent? |
|---|---|
| Chat riêng (private) | Luôn luôn |
| @mention bot | Luôn luôn |
| Reply vào tin của bot | Luôn luôn |
| Lệnh `/...` | Luôn luôn |
| Trigger word cấu hình được (vd "alice ơi") | Có |
| Còn lại | **Không** — chỉ vào archive làm ngữ cảnh nền |

Tầng hai là **luật im lặng** phía model: agent vẫn có quyền trả `[SILENT]` khi thấy không nên chen vào.
Hai tầng vì tầng một không hiểu ngữ cảnh, tầng hai thì tốn một lượt CLI.

**Khoá.** Ba loại, đừng nhầm:

| Khoá | Phạm vi | Chống |
|---|---|---|
| `SingletonLock` | tiến trình | Hai daemon cùng chạy sau khi restart lỗi |
| `ChatLock` | mỗi chat | Hai agent cùng một cuộc hội thoại |
| `ResourceLock` | toàn máy, có tên | Tranh Chrome profile / brain sync giữa các chat và với script ngoài |

`ResourceLock` là file có heartbeat, để **script ngoài repo cũng đọc được** (dùng làm PreToolUse hook).

**Ack — trả lời nhanh khi việc lâu.** Hai đường:
- *Chủ động*: router đoán việc dài (có động từ hành động, có file đính kèm, là lệnh) → gửi ack ngay.
- *Bị động*: quá `ACK_AFTER_SECONDS` (12s) mà chưa xong → gửi ack.

12 giây là ranh giới đo được giữa "hỏi-đáp thuần" (5–10s) và "có làm việc thật" (tính bằng phút).
Ack **không hứa thời gian cụ thể** — hứa 2 phút rồi chạy 10 phút còn tệ hơn im.

### 4.5 Adapter (`atls/adapters/`)

```python
class AgentAdapter(Protocol):
    name: str
    def is_available(self) -> bool: ...
    def supports_resume(self) -> bool: ...
    async def run(self, req: AgentRequest) -> AgentResult: ...
```

| Adapter | Lệnh | Resume |
|---|---|---|
| `claude` | `claude --print --session-id/--resume --append-system-prompt` | có |
| `codex` | `codex exec` / `codex exec resume` | có |
| `opencode` | `opencode run --session` | có |
| `antigravity` | template lệnh cấu hình được | tuỳ |
| `custom` | `ATLS_AGENT_CMD` template | không |

**CLI không hỗ trợ resume vẫn dùng được đầy đủ**: memory layer đã dựng sẵn cửa sổ hội thoại,
adapter chỉ việc dán vào prompt. Resume là tối ưu, không phải điều kiện.

### 4.6 Capabilities (`atls/capabilities/`)

| Khả năng | Cách làm |
|---|---|
| Gửi PDF | Markdown → PDF (`reportlab`, optional dep) → `sendDocument` |
| Gửi ảnh/file | `sendPhoto` / `sendDocument` với caption |
| Nhận file từ user | `getFile` → tải về `inbox/<chat_id>/` → đường dẫn vào prompt |
| Tạo ảnh bằng ChatGPT | Skill `atls-image-gen`: agent lái Chrome MCP trên profile có sẵn |
| Nhờ người thật đăng nhập | `pending_gates` + tin nhắn hướng dẫn, chờ `/done` |

Tạo ảnh **không** nằm trong runtime — nó là một **skill** để agent tự thực hiện.
Runtime không được biết cách lái Chrome; nó chỉ biết gửi ảnh lên Telegram.

## 5. Xử lý lỗi

| Tình huống | Xử lý |
|---|---|
| Webhook gửi trùng | `UNIQUE(update_id)` nuốt |
| Agent CLI chết | Trả lỗi ngắn gọn lên chat, giữ session, không crash daemon |
| Resume hỏng | Mở session mới, chạy lại **cùng prompt** — mất ngữ cảnh CLI chứ không mất câu trả lời |
| Compaction lỗi | Rơi về cắt cứng theo token, log WARN — không được chặn câu trả lời |
| Mất mạng | Backoff luỹ thừa; long-poll tự nối lại; webhook do Telegram retry |
| `/stop` giữa chừng | `StopWatcher` kill process tree, báo rõ "có thể đã làm xong một phần" |
| Secret lọt vào chat | `redact()` trước khi ghi DB **và** trước khi vào prompt |

## 6. Kiểm thử

Ba nhóm, không cần Telegram thật:

1. **Store** — idempotency (`update_id` trùng), WAL đa tiến trình, archive không bao giờ mất tin.
2. **Memory** — cửa sổ luôn ≤ budget; compaction giữ đúng khoảng msg_id; running summary không nhân đôi.
3. **Runtime** — router bắt đúng 6 đường vào; debounce reset đúng; `ChatLock` không cho hai lượt cùng chat;
   session xoay đúng ở mốc 12h và 3h idle.

Adapter test bằng CLI giả (script echo), không gọi model thật.

## 7. Ngoài phạm vi (cố ý)

- Không tự đăng nhập Google/ChatGPT, không nhập mật khẩu, không giải CAPTCHA — luôn bàn giao cho người thật.
- Không giao diện web quản trị. Cấu hình bằng `.env` + lệnh trong chat.
- Không đa tenant. Một deployment phục vụ một "Alice".

## 8. Quan hệ với Alice Coding

| | Alice Coding | ATLS |
|---|---|---|
| Nhớ cái gì | Quyết định, lỗi, wiki, changelog của **project** | **Hội thoại** với con người theo thời gian |
| Đơn vị | Entry Markdown + brain vector | Tin nhắn + tóm tắt theo chat |
| Vòng đời | Vĩnh viễn, có prune/supersede | Vĩnh viễn (archive) + nén (cửa sổ) |
| Nạp lúc nào | Đầu mỗi task | Mỗi lượt chat |

Cắm vào nhau: mỗi lần ATLS compact, bản tóm tắt **có thể** được ghi thành entry
`knowledge/context/` để brain đánh chỉ mục — lúc đó agent tra được hội thoại cũ bằng ngữ nghĩa,
không chỉ theo thứ tự thời gian. Tính năng này bật/tắt bằng `ATLS_BRAIN_BRIDGE`.
