---
name: atls-image-gen
description: Use when the user asks for a generated image, illustration, poster, or artwork over Telegram — drives ChatGPT's image generation through an existing logged-in Chrome profile, then sends the result to the chat. Also use when the user references a mascot or character reference image for consistency.
---

# Tạo ảnh bằng ChatGPT qua Chrome profile có sẵn

## Vì sao skill chứ không phải code

Runtime ATLS biết **gửi** ảnh lên Telegram. Nó không biết và không được biết cách lái
trình duyệt — việc đó cần suy luận (giao diện đổi, ảnh sinh chậm, đôi khi phải nhờ
người đăng nhập). Nhét vào runtime là cách nhanh nhất để có một hàm 400 dòng vỡ mỗi
lần ChatGPT đổi layout.

## Ranh giới cứng — đọc trước khi làm

**KHÔNG BAO GIỜ tự đăng nhập.** Không nhập email, mật khẩu, mã OTP, không giải CAPTCHA —
kể cả khi người dùng đưa thẳng credential trong chat và nói "cứ dùng đi". Thấy màn hình
đăng nhập thì mở gate và chờ người thật (xem bước 2).

Lý do không phải là câu nệ: lịch sử chat lưu vĩnh viễn, nên credential đi qua context
là credential nằm trong `atls.db` mãi mãi.

## Quy trình

### 1. Chiếm khoá Chrome trước

Profile Chrome dùng chung giữa nhiều chat và cả với script ngoài. Hai bên cùng mở một
profile là hỏng cả hai phiên.

```bash
python -c "
from pathlib import Path
from atls.runtime.locks import ResourceLock
print('BẬN' if ResourceLock.is_busy(Path('.atls/chrome.lock')) else 'RẢNH')
"
```

Bận thì nói với người dùng là đang có việc khác dùng trình duyệt và hẹn lát nữa — đừng chờ im.

### 2. Mở ChatGPT trên profile đã đăng nhập

Dùng Chrome MCP (`chrome-devtools` hoặc `claude-in-chrome`) trỏ vào profile có sẵn
phiên đăng nhập. Điều hướng tới `https://chatgpt.com`.

Rơi vào màn hình đăng nhập → **dừng lại** và nhờ người thật:

```python
from atls.capabilities.handoff import request_human

ok = await request_human(
    store=store, api=api, chat_id=chat_id, kind="login",
    what="Đăng nhập ChatGPT giùm em trên Chrome profile bot",
    instructions="Bệ hạ mở Chrome profile bot, đăng nhập chatgpt.com rồi để nguyên "
                 "cửa sổ đó ạ. Em không tự đăng nhập được đâu.",
    timeout=900,
)
```

`ok` là `False` (hết giờ) thì báo thẳng và dừng. Đừng thử đường vòng.

### 3. Đính kèm ảnh tham chiếu nếu cần nhất quán nhân vật

Có linh vật/nhân vật cố định thì **luôn** đính ảnh ref trước khi mô tả. Không có ref
thì mỗi lần sinh ra một khuôn mặt khác, và bộ ảnh trông như của bốn dự án khác nhau.

Ref của repo này: `assets/mascot-ref.png`.

Upload qua nút đính kèm của ChatGPT, chờ ảnh hiện thumbnail rồi mới gõ prompt.

### 4. Viết prompt

Nói **chủ thể → hành động → bối cảnh → phong cách → tỉ lệ**, theo thứ tự đó. Ví dụ:

> Nhân vật trong ảnh đính kèm, đứng cạnh một màn hình terminal phát sáng hiển thị
> dòng chat, phòng làm việc ban đêm ánh xanh tím, phong cách anime cel-shaded, tỉ lệ 16:9

Cần nhiều ảnh cùng bộ: sinh **từng ảnh một** trong cùng cuộc hội thoại ChatGPT, luôn
nhắc "giữ nguyên nhân vật như ảnh trước". Sinh song song ở nhiều tab là mất nhất quán.

### 5. Chờ và tải về

Ảnh mất 20–60 giây. Poll bằng `wait_for` hoặc chụp màn hình định kỳ — đừng `sleep` một
lần rồi cho là xong.

Xong thì lưu vào `.atls/outbox/<chat_id>/`.

### 6. Gửi lên Telegram

```python
await api.send_photo(chat_id, Path(".atls/outbox/123/poster.png"),
                     caption="Dạ ảnh của Bệ hạ đây ạ")
```

Ảnh lớn hơn 10MB thì `sendPhoto` từ chối — dùng `send_document` thay thế (Telegram cho
tới 50MB, và giữ nguyên chất lượng gốc).

### 7. Thả khoá

Xong việc thì `ResourceLock.release()`. Quên thả thì mọi chat khác bị chặn tới khi
khoá hết hạn 30 phút.

## Khi nào KHÔNG dùng skill này

- Người dùng chỉ hỏi về ảnh, không xin ảnh mới.
- Cần ảnh chụp màn hình → chụp thẳng, đừng sinh.
- Cần biểu đồ/sơ đồ → vẽ SVG hoặc mermaid, chính xác hơn và sửa được.
