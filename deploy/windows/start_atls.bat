@echo off
REM Alice Telegram Session — launcher Windows.
REM Chạy lúc đăng nhập qua start_atls.vbs (cửa sổ ẩn) hoặc install_task.ps1.

cd /d "%~dp0..\.."

REM Log ra file, KHÔNG để stdout rơi vào hư không. VBS chạy cửa sổ ẩn nên mọi dòng
REM print — ai nhắn, gộp mấy tin, im lặng hay trả lời — trước đây mất sạch và không
REM có cách nào truy lại một lượt chạy hỏng.
if not exist ".atls\logs" mkdir ".atls\logs"

REM Lấy ngày qua PowerShell, KHÔNG parse %DATE%: %DATE% đổi định dạng theo locale
REM (máy này ra dd/mm/yyyy, máy khác ra mm/dd/yyyy) nên parse tay là bug chờ sẵn.
set "_TODAY="
for /f %%d in ('powershell -NoProfile -Command "Get-Date -Format yyyy-MM-dd"') do set "_TODAY=%%d"
REM PowerShell có thể không trả gì (khởi động chậm, PATH tối thiểu lúc logon, policy
REM chặn). Không có nhánh dự phòng thì log rơi vào "atls-.log" và biến mất đúng lúc
REM cần đọc nhất.
if not defined _TODAY set "_TODAY=unknown-date"

set "_PY=.venv\Scripts\python.exe"
if not exist "%_PY%" set "_PY=python"

REM `-u` bắt buộc: stdout không phải tty thì Python buffer 8KB, log sẽ trễ hàng giờ
REM và mất phần cuối nếu tiến trình bị kill.
"%_PY%" -u -m atls.cli run >> ".atls\logs\stdout-%_TODAY%.log" 2>&1
