' Chạy start_atls.bat với cửa sổ ẩn (tham số 0) và không chờ (False).
' Đây là cách duy nhất khởi động lúc đăng nhập mà không nhấp nháy một cửa sổ đen.
CreateObject("WScript.Shell").Run """" & _
  CreateObject("Scripting.FileSystemObject").GetParentFolderName(WScript.ScriptFullName) & _
  "\start_atls.bat""", 0, False
