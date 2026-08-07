# Giữ đúng MỘT daemon ATLS sống, qua cả việc đóng terminal và khởi động lại máy.
#
# Vì sao vòng lặp nằm ở đây chứ không giao cho Task Scheduler: scheduler chỉ dựng
# lại một tác vụ THẤT BẠI. Một daemon thoát sạch (mã 0) được Windows coi là thành
# công và không bao giờ được dựng lại — mà "thoát sạch rồi biến mất" chính là kiểu
# chết tệ nhất ở đây: người dùng không còn cửa nào khác để gõ.
#
# Dùng:
#   powershell -NoProfile -ExecutionPolicy Bypass -File forever.ps1 `
#       -Root "C:\Work\alice-telegram-session" `
#       -CredentialEnv "C:\Work\project-cua-ban\.env"

param(
    # Thư mục repo ATLS (chứa .venv và gói atls).
    [string]$Root = "C:\Work\alice-telegram-session",

    # .env chứa credential của agent CLI. Để trống nếu CLI tự lo đăng nhập.
    [string]$CredentialEnv = "",

    # Tên biến trong .env, và tên biến môi trường mà CLI thật sự đọc.
    [string]$CredentialKey = "CLAUDE_TOKEN",
    [string]$CredentialEnvVar = "CLAUDE_CODE_OAUTH_TOKEN"
)

$ErrorActionPreference = "Continue"
$python = Join-Path $Root ".venv\Scripts\python.exe"
$logDir = Join-Path $Root ".atls\logs"
$log = Join-Path $logDir "supervisor.log"

if (-not (Test-Path $logDir)) { New-Item -ItemType Directory $logDir -Force | Out-Null }

function Write-Log($message) {
    $line = "{0} [alice] {1}" -f (Get-Date -Format "yyyy-MM-ddTHH:mm:ssK"), $message
    Add-Content -Path $log -Value $line -Encoding utf8
}

if (-not (Test-Path $python)) {
    Write-Log "khong tim thay python o $python - dung han"
    exit 1
}

# Tác vụ nền KHÔNG thừa kế PATH của terminal đã đăng ký nó. CLI cài qua npm nằm ở
# %APPDATA%\npm, có trên PATH của shell tương tác và thường không có ở đây — nên
# daemon khởi động ngon lành rồi chết với 127 đúng lúc có tin nhắn đầu tiên.
$npmBin = Join-Path $env:APPDATA "npm"
if (Test-Path $npmBin) { $env:PATH = "$npmBin;$env:PATH" }

# Và service cũng không thừa kế phiên đăng nhập tương tác. Thiếu bước này thì lệnh
# spawn được nhưng lượt chat chết một bước sau với "Not logged in" — đọc như một tài
# khoản hỏng chứ không như một biến môi trường thiếu.
#
# Không bao giờ in ra, không bao giờ truyền qua dòng lệnh: argv nhìn thấy được từ mọi
# tiến trình khác trên máy, environment của tiến trình con thì không.
if ($CredentialEnv -and (Test-Path $CredentialEnv)) {
    foreach ($line in Get-Content $CredentialEnv) {
        if ($line -match "^\s*$CredentialKey\s*=\s*(.+?)\s*$") {
            Set-Item -Path "Env:$CredentialEnvVar" -Value $Matches[1].Split('#')[0].Trim()
        }
    }
}
if ($CredentialEnv -and -not (Get-Item -Path "Env:$CredentialEnvVar" -ErrorAction SilentlyContinue)) {
    Write-Log "CANH BAO: khong doc duoc $CredentialKey - agent se tra loi 'Not logged in'"
}

$delay = 5
while ($true) {
    Write-Log "khoi dong daemon"
    Push-Location $Root
    & $python -m atls.cli run
    $code = $LASTEXITCODE
    Pop-Location
    Write-Log "daemon thoat voi ma $code, thu lai sau ${delay}s"

    Start-Sleep -Seconds $delay
    # Backoff tới một phút. Một daemon chết ngay lập tức (token sai, bot bị thu hồi)
    # sẽ quay vòng hàng nghìn lần và chôn vùi lỗi thật dưới chính đống restart của nó.
    $delay = [Math]::Min($delay * 2, 60)
    if ($code -eq 0) { $delay = 5 }
}
