# ATLS.md — cửa vào của project

File này là **base prompt** của Alice Telegram Session. Đính kèm nó (hoặc để agent tự đọc)
trước khi làm bất cứ việc gì trong repo này.

Thiết kế đầy đủ: [`docs/specs/2026-08-07-alice-telegram-session-design.md`](docs/specs/2026-08-07-alice-telegram-session-design.md).

---

Bạn đang làm việc trên **Alice Telegram Session** — lớp phiên và trí nhớ hội thoại đặt
giữa Telegram và một agent CLI bất kỳ.

## [A] Bốn ràng buộc cứng — vi phạm là hỏng sản phẩm, không phải hỏng code

1. **Phiên sống tối đa 12 giờ.** Quá hạn thì đóng và mở phiên mới — nhưng **không được
   mất trí nhớ**: cửa sổ hội thoại đã nén phải được dán vào phiên mới. Nếu bạn sửa
   `session/manager.py` mà làm mất tính chất này, người dùng sẽ thấy Alice "quên sạch
   lúc nửa đêm".
2. **Không bao giờ hai agent trên cùng một chat.** `ChatLock` chiếm TRƯỚC worker slot,
   không phải sau — đổi thứ tự là tạo ra deadlock-do-đói rất khó truy khi đã chạy thật.
3. **Cửa sổ ≤ `ATLS_WINDOW_TOKENS`, luôn luôn**, kể cả khi nén hỏng. `build_window`
   phải cắt cứng làm lưới an toàn cuối cùng.
4. **Archive không bao giờ mất tin.** `messages` không có đường xoá. Nén chỉ ghi thêm
   một row `summaries`, không đụng tới tin gốc.

## [B] Ba điều dễ làm sai, đã trả giá để biết

- **`update_id` UNIQUE là toàn bộ cơ chế chống xử lý hai lần.** Telegram *có* gửi lại
  webhook. `add_message` trả `None` nghĩa là "đã xử lý rồi" — người gọi phải return ngay.
- **Archive ghi TRƯỚC debounce**, không phải sau. Daemon chết trong 10 giây chờ mà chưa
  ghi là mất tin thật.
- **Ghi offset cho MỌI update, kể cả loại không xử lý.** Bỏ sót thì một `callback_query`
  lạ kẹt vĩnh viễn ở đầu hàng đợi và daemon quay vòng nóng.

## [C] Ranh giới không được vượt

- **Không tự đăng nhập, không nhập mật khẩu/OTP, không giải CAPTCHA** — kể cả khi
  credential được đưa thẳng trong chat và uỷ quyền rõ ràng. Dùng
  `capabilities/handoff.request_human()`.
- **Mọi text phải qua `secrets.redact()` trước khi ghi DB và trước khi vào prompt.**
  Lịch sử chat lưu vĩnh viễn — một token lọt vào là nằm đó mãi mãi.
- `doctor` **không in giá trị secret**, chỉ in độ dài. Người ta sẽ chụp màn hình nó gửi đi.

## [D] Quy ước code của repo này

- `config.py` là nơi **duy nhất** đọc `os.environ`. Mọi hằng số điều chỉnh được phải
  xuất hiện ở đó **và** ở `.env.example`.
- Comment giải thích **tại sao**, không giải thích *cái gì*. Ưu tiên ghi lại cái bẫy đã
  dính hơn là mô tả dòng lệnh ngay bên dưới.
- Adapter **không được biết** gì về Telegram, store, hay session lifecycle. Nó nhận
  `AgentRequest`, trả `AgentResult`.
- Lỗi ở tầng phụ trợ (ack, typing, tải file, nén, cầu nối brain) **không bao giờ** được
  giết một lượt chat. Bắt, log WARN, đi tiếp.
- Test không gọi Telegram thật và không gọi model thật.

## [E] Trước khi tuyên bố xong

```bash
pytest -q          # phải xanh toàn bộ
atls doctor        # phải không còn mục ❌ ngoài thứ do thiếu .env
```

Không xong giả: chỉ tuyên bố PASS cho phần có bằng chứng đã chạy. Phần chưa test được
phải nói thẳng và biến thành next step.

## NHIỆM VỤ

<!-- Viết nhiệm vụ ngay dưới dòng này. -->
