<#
.SYNOPSIS
    Đăng ký ATLS chạy lúc đăng nhập Windows.

.DESCRIPTION
    Tạo một Scheduled Task chạy start_atls.vbs khi user hiện tại đăng nhập.

    Vì sao Scheduled Task chứ không phải thư mục Startup: Startup không khởi động lại
    khi tiến trình chết, không đặt được độ trễ, và không chạy khi máy vừa thức dậy từ
    sleep. Task Scheduler làm được cả ba.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File deploy\windows\install_task.ps1
    powershell -ExecutionPolicy Bypass -File deploy\windows\install_task.ps1 -Remove
#>
param(
    [string]$TaskName = "AliceTelegramSession",
    [switch]$Remove
)

$ErrorActionPreference = "Stop"
$root = Resolve-Path (Join-Path $PSScriptRoot "..\..")
$vbs  = Join-Path $PSScriptRoot "start_atls.vbs"

if ($Remove) {
    try { Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false }
    catch { Write-Host "Không có task '$TaskName' để gỡ." }
    Write-Host "Đã gỡ."
    return
}

if (-not (Test-Path $vbs)) { throw "Không tìm thấy $vbs" }
if (-not (Test-Path (Join-Path $root ".env"))) {
    Write-Warning "Chưa có .env — chép .env.example thành .env rồi điền TELEGRAM_BOT_TOKEN."
}

$action = New-ScheduledTaskAction -Execute "wscript.exe" `
    -Argument "`"$vbs`"" -WorkingDirectory $root

# Trễ 30 giây: lúc vừa logon thì mạng thường chưa sẵn sàng, và lần gọi Telegram đầu
# tiên sẽ thất bại rồi phải backoff.
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$trigger.Delay = "PT30S"

$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -StartWhenAvailable -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 2) `
    -ExecutionTimeLimit ([TimeSpan]::Zero)   # 0 = không giới hạn; daemon phải sống mãi

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger `
    -Settings $settings -Description "Alice Telegram Session — agent nghe Telegram 24/7" `
    -Force | Out-Null

Write-Host "Đã đăng ký '$TaskName'. Chạy ngay:  Start-ScheduledTask -TaskName $TaskName"
Write-Host "Log:  $root\.atls\logs\"
