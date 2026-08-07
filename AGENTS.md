# Alice Telegram Session — luật của project

Đọc [`ATLS.md`](ATLS.md) trước khi gõ dòng code đầu tiên của mỗi phiên. Năm luật dưới đây
có hiệu lực **cho mọi lượt**.

1. **Bốn ràng buộc cứng không được phá** (chi tiết ở `ATLS.md` mục A):
   phiên ≤ 12h nhưng không mất trí nhớ · một chat một agent · cửa sổ luôn ≤ trần token ·
   archive không bao giờ mất tin.

2. **Không tự đăng nhập, không nhập secret, không giải CAPTCHA.** Bàn giao cho người thật
   qua `capabilities/handoff.request_human()`. Mọi text qua `secrets.redact()` trước khi
   ghi đĩa hoặc vào prompt.

3. **`config.py` là nơi duy nhất đọc env.** Thêm một hằng số điều chỉnh được mà không
   khai báo ở đó **và** ở `.env.example` là tạo ra một hằng số ma.

4. **Lỗi phụ trợ không được giết lượt chat.** ack, typing, tải file, nén, cầu nối brain —
   bắt, log WARN, đi tiếp. Người dùng phải nhận được câu trả lời kể cả khi ngoại vi hỏng.

5. **Không xong giả.** `pytest -q` phải xanh trước khi tuyên bố hoàn thành. Phần chưa test
   được phải nói thẳng, không được im lặng bỏ qua.

## Lệnh hay dùng

```bash
pytest -q                    # 111 test, không cần Telegram thật
atls doctor                  # kiểm cấu hình + CLI có sẵn
atls chats                   # chat đã biết, số tin mỗi chat
atls recall <từ khoá>        # tra archive từ terminal
atls compact <chat_id>       # ép nén ngay để kiểm chất lượng tóm tắt
```

## Bản đồ nhanh

| Sửa cái gì | Vào đâu |
|---|---|
| Cách chọn/xoay phiên | `atls/session/manager.py` |
| Cách nén, giữ lại gì | `atls/memory/compactor.py` + `prompts/compact.md` |
| Cách dựng cửa sổ | `atls/memory/window.py` |
| "Việc này của mình không" | `atls/runtime/router.py` |
| Luật im lặng phía model | `prompts/system.md` |
| Thêm agent CLI mới | `atls/adapters/clis.py` |
| Lệnh `/…` trong Telegram | `atls/runtime/commands.py` |
| Gửi file/PDF/ảnh, bàn giao người thật | `atls/runtime/directives.py` + `prompts/system.md` |
| Lược đồ dữ liệu | `atls/store/schema.sql` |
