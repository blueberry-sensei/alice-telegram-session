---
name: atls-forever
description: Use when deploying an ATLS agent so it lives permanently on Telegram for one user — a single always-on assistant that survives terminal closes, reboots and 12-hour session limits. Also use when Telegram chat "works when I test it but dies when it runs for real", when two bot instances fight over getUpdates (409 Conflict), or when the agent answers "Not logged in" / "command not found" only from a scheduled task.
---

# Dựng một Alice sống vĩnh viễn trên Telegram

Đích đến: người dùng nhắn Telegram bất cứ lúc nào, **đúng một** agent nhận, nó nhớ
mạch chuyện cũ, và nó không chết khi terminal đóng hay máy khởi động lại.

Toàn bộ tài liệu này rút ra từ một lần triển khai thật trên máy production, nơi **mọi
bước dưới đây đều đã hỏng ít nhất một lần**. Thứ tự các mục là thứ tự chúng sẽ cắn.

---

## [1] Luật cứng: đúng MỘT tiến trình, và phải ĐẾM chứ đừng tin

Telegram không chia hàng đợi giữa hai long-poller. Chúng **giành** nhau, và trong lúc
giành thì **cả hai đều không nhận được gì**:

```
getUpdates thất bại (409): Conflict: terminated by other getUpdates request
```

Nghĩa là hai bản chạy không làm mọi thứ chậm đi — nó làm kênh **chết câm**.

**Ba cái bẫy đã dính, cả ba đều làm người triển khai tin nhầm là đã dừng:**

1. **Lệnh dừng trả về thành công KHÔNG phải bằng chứng tiến trình đã chết.**
   `Stop-ScheduledTask` chỉ *báo hiệu*; supervisor dựng lại ngay. `pkill -f` trên
   Windows/git-bash có thể khớp 0 tiến trình và **không báo lỗi gì cả**.
2. **File khoá của daemon có thể không chặn bản thứ hai.** Đừng tin nó cho tới khi
   thấy nó từ chối một lần khởi động thật.
3. **Câu lệnh đếm tiến trình TỰ KHỚP VỚI CHÍNH NÓ.** Dòng lệnh của tiến trình đang
   chạy phép lọc có chứa chuỗi cần tìm, nên nó tự đếm mình vào — hoặc tệ hơn, tự giết
   mình (thoát 255). Phải loại `$PID` ra, và nên chạy từ **file script** thay vì
   `-Command`, vì chuỗi lệnh inline nằm nguyên trong command line của chính nó.

```powershell
# Đếm đúng: loại chính mình, và nhìn cây tiến trình
$me = $PID
@(Get-CimInstance Win32_Process | Where-Object {
    $_.ProcessId -ne $me -and $_.Name -like 'python*' -and $_.CommandLine -like '*atls*'
}).Count
```

Quy trình khởi động lại: **dừng → giết theo PID → đếm cho tới khi bằng 0 → mới bật lại
→ đếm lại.** Không có bước "chắc là chết rồi".

Kèm theo: đặt `ATLS_WORKERS=1`. Hai worker trên một chat là hai agent cùng ghi một
thư mục làm việc.

---

## [2] Môi trường của terminal KHÁC môi trường của service

Đây là lỗi tốn nhiều thời gian nhất, và nó có hai tầng — vá tầng một xong sẽ lộ tầng hai.

### 2a. Không tìm thấy lệnh (thoát 127)

Tác vụ nền **không thừa kế PATH** của terminal đã đăng ký nó. CLI cài qua npm nằm ở
`%APPDATA%\npm`, có trên PATH của shell tương tác và thường không có trên PATH của
service.

**Nhưng nguyên nhân sâu hơn nằm chỗ khác, và nó là bài học chính của tài liệu này:**

> **Lệnh chẩn đoán và lệnh chạy thật dùng hai luật tìm file khác nhau.**
> `shutil.which` có thử `PATHEXT` nên tìm ra bản đuôi `.cmd`. Còn `CreateProcess` của
> Windows **chỉ** thêm `.exe` vào một tên trần. Cùng một chữ `claude`: `doctor` in
> **✅ chạy được**, lần chạy thật trả **127**.

Một cổng kiểm tra **xanh** đứng trước một lời gọi **hỏng** còn tệ hơn không có cổng
nào — nó dời chỗ hỏng sang đúng lúc không ai nhìn, ở đây là tin nhắn đầu tiên của
người dùng.

Bản vá nằm trong `atls/adapters/base.py`: phân giải tên lệnh bằng cùng một luật
**trước khi** spawn, để phép kiểm và phép chạy không thể lệch nhau nữa.

### 2b. Chạy được rồi thì "Not logged in"

Service **không thừa kế phiên đăng nhập tương tác**, và cũng không đọc `.env` của
project khác. Credential phải được nạp vào môi trường của tiến trình con.

```powershell
# Đọc từ .env của project, KHÔNG in ra, KHÔNG truyền qua dòng lệnh.
# argv nhìn thấy được từ mọi tiến trình khác trên máy; environment của con thì không.
foreach ($line in Get-Content $envFile) {
    if ($line -match '^\s*CLAUDE_TOKEN\s*=\s*(.+?)\s*$') {
        $env:CLAUDE_CODE_OAUTH_TOKEN = $Matches[1].Split('#')[0].Trim()
    }
}
if (-not $env:CLAUDE_CODE_OAUTH_TOKEN) { Write-Log "CANH BAO: thieu token" }
```

Thiếu token thì **ghi cảnh báo vào log**, đừng để lượt chat chết lặng lẽ.

---

## [3] Phép thử phải đi ĐÚNG con đường mà việc thật đi

Hệ quả trực tiếp của [2]: **đừng hỏi `doctor`.** Gọi thẳng adapter với một prompt thật,
từ đúng môi trường mà service sẽ chạy:

```python
import asyncio, pathlib
from atls.adapters.clis import ClaudeAdapter
from atls.adapters.base import AgentRequest

res = asyncio.run(ClaudeAdapter().run(AgentRequest(
    prompt="Tra loi dung mot tu: OK", system="", session_id="", resume=False,
    cwd=pathlib.Path("/duong/dan/project"), model="...", timeout=240,
)))
print(res.ok, res.returncode, res.text[:80], res.stderr[:200])
```

`ok=True` mới là qua. Bất cứ thứ gì khác — kể cả `doctor` toàn dấu ✅ — là chưa.

---

## [4] Sống sót qua terminal đóng: supervisor + tác vụ hệ thống

Tiến trình khởi động từ một phiên chat **chết cùng phiên đó**. Nếu đã hứa với người
dùng là "luôn ở đó" thì "luôn" phải sống sót được việc đóng terminal.

**Task Scheduler chỉ dựng lại tác vụ THẤT BẠI.** Một daemon thoát sạch (mã 0) được
Windows coi là thành công và **không bao giờ** được dựng lại. Nên vòng giám sát phải
nằm trong script, không nằm ở scheduler:

```powershell
$delay = 5
while ($true) {
    Write-Log "khoi dong daemon"
    Push-Location $Root
    & $python -m atls.cli run
    $code = $LASTEXITCODE
    Pop-Location
    Write-Log "daemon thoat voi ma $code, thu lai sau ${delay}s"
    Start-Sleep -Seconds $delay
    $delay = [Math]::Min($delay * 2, 60)   # backoff: token sai se quay vong nghin lan
    if ($code -eq 0) { $delay = 5 }
}
```

Đăng ký chạy khi đăng nhập, không giới hạn thời gian chạy:

```powershell
$action  = New-ScheduledTaskAction -Execute "powershell.exe" `
           -Argument '-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File <duong-dan>\alice_forever.ps1'
$trigger = New-ScheduledTaskTrigger -AtLogOn -User "$env:USERNAME"
$set     = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
           -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1)
Register-ScheduledTask -TaskName "<ten>" -Action $action -Trigger $trigger -Settings $set -Force
```

> Tắt vĩnh viễn một tác vụ **của người khác** cần quyền admin và sẽ thất bại. Nếu có
> một vai cũ cũng chạm Telegram mà không tắt được, xem mục [5].

---

## [5] Chỉ một chủ sở hữu kênh, khai báo bằng env

Khi có sẵn một tiến trình cũ cũng nói chuyện Telegram (và không tắt vĩnh viễn được),
đừng dựa vào việc nhớ đừng bật nó. Khoá ở tầng code:

```
# .env cua project cu
GT_TELEGRAM_OWNER=atls
```

```python
owner = os.environ.get("GT_TELEGRAM_OWNER", "").strip().lower()
if owner and owner != "golden-trade":
    print("Kenh Telegram do '%s' so huu." % owner, file=sys.stderr)
    return 4     # tu choi khoi dong
```

Khai báo chứ không đoán: một cái khoá dựa trên "tôi nhớ là đã tắt rồi" không phải khoá.

---

## [6] "Sống vĩnh viễn" thật ra là gì

Phiên CLI **không** sống mãi, và không nên. Cấu hình đang dùng:

```
ATLS_SESSION_MAX_AGE=43200   # 12h roi mo phien moi
ATLS_SESSION_IDLE=10800      # 3h im lang thi dong
ATLS_WINDOW_TOKENS=20000     # tran cua so hoi thoai
ATLS_COMPACT_TRIGGER=16000   # vuot nguong nay thi nen phan cu
ATLS_KEEP_RAW=12             # giu nguyen van 12 tin gan nhat
```

Cái sống vĩnh viễn là **trí nhớ**, không phải tiến trình: hết 12 giờ thì phiên đóng,
cửa sổ hội thoại đã nén được dán sang phiên mới. Người dùng không thấy đứt mạch.

Đừng nới `ATLS_SESSION_MAX_AGE` lên vô hạn để "cho nó nhớ lâu hơn" — nó sẽ phình
context tới lúc mỗi lượt vừa chậm vừa đắt, rồi hỏng vào giờ tệ nhất.

---

## [7] Danh sách kiểm trước khi nói "xong"

Chỉ tick khi có **bằng chứng đã chạy**, không tick theo suy luận:

- [ ] Đếm tiến trình = đúng một cặp (đã loại `$PID` khỏi phép đếm)
- [ ] Log **không còn** dòng 409 nào trong vài phút gần nhất
- [ ] Gọi thẳng adapter → `ok=True` (không dùng `doctor` làm bằng chứng)
- [ ] Người dùng nhắn một tin THẬT và nhận được trả lời — **đường vào chỉ chứng minh
      được bằng cách này**, không có cách nào khác
- [ ] Giết tiến trình bằng tay → supervisor dựng lại trong vòng một phút
- [ ] `ATLS_ALLOWED_CHATS` có giá trị (cổng fail-CLOSED), `ATLS_ALLOW_ALL_CHATS=0`
- [ ] Không có credential nào bị in ra log hay truyền qua dòng lệnh

---

## [8] Ba cái bẫy đọc log

- **Im lặng là bình thường.** Long-poller khoẻ chỉ ghi log khi có lỗi hoặc có tin.
  Mười phút không có dòng nào là dấu hiệu tốt.
- **`pending_update_count` từ `getWebhookInfo` là phép kiểm KHÔNG phá gì.** Nó không
  tiêu thụ update nên không cướp tin của daemon đang chạy. Số đó lớn dần = daemon
  không nhận; bằng 0 kèm daemon đang chạy = lành.
- **Lỗi 409 tự hết sau backoff.** Thấy 409 rồi im nghĩa là bản thừa đã chết. Đừng
  khởi động lại lần nữa chỉ vì nhìn thấy dòng 409 cũ trong log.

---

## Vì sao skill chứ không phải một script cài đặt

Mọi bước ở đây đều cần **nhìn và phán đoán**: đếm đúng tiến trình nào, đọc log để phân
biệt "im lặng lành" với "chết câm", quyết định có nên giết một tiến trình lạ hay không.
Một script `setup.ps1` sẽ chạy trót lọt trên máy đã đúng sẵn và hỏng câm trên máy chưa
đúng — đúng cái kiểu hỏng mà tài liệu này tồn tại để chặn.
