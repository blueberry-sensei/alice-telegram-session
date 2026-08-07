"""
Hợp đồng giữa ATLS và một agent CLI.

Điểm thiết kế quan trọng nhất: **resume là tối ưu, không phải điều kiện.**

CLI không hỗ trợ nối tiếp session vẫn dùng được đầy đủ, vì tầng `atls/memory/` đã
dựng sẵn cửa sổ hội thoại và adapter chỉ việc dán vào prompt. Điều này giữ cho danh
sách CLI hỗ trợ mở rộng được — không phải chờ một CLI mọc thêm tính năng mới cắm được.

Adapter KHÔNG được biết gì về Telegram, về store, về session lifecycle. Nó nhận một
`AgentRequest` và trả một `AgentResult`. Có thế thôi.
"""

from __future__ import annotations

import asyncio
import os
import signal
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from atls import log

_log = log.get("adapters")

# Agent in ra đúng token này (một mình) khi quyết định không xen vào.
SILENT_TOKEN = "[SILENT]"


@dataclass(frozen=True)
class AgentRequest:
    prompt: str
    system: str
    session_id: str
    resume: bool
    cwd: Path
    model: str = ""
    timeout: float | None = None  # None = không giới hạn (việc dài là hợp lệ)
    env: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class AgentResult:
    ok: bool
    text: str
    stderr: str = ""
    returncode: int = 0
    stopped: bool = False   # bị người dùng cắt ngang bằng /stop

    @property
    def silent(self) -> bool:
        return self.text.replace("`", "").strip() == SILENT_TOKEN


class AgentAdapter(Protocol):
    name: str

    def is_available(self) -> bool: ...
    def supports_resume(self) -> bool: ...
    async def run(self, req: AgentRequest) -> AgentResult: ...


def kill_tree(proc: asyncio.subprocess.Process) -> None:
    """Giết cả cây tiến trình con.

    `proc.kill()` chỉ giết tiến trình đầu. Agent CLI sinh ra shell, trình duyệt,
    server con — giết mỗi cha thì lũ con thành mồ côi, tiếp tục chạy và tiếp tục
    giữ khoá tài nguyên. Đây là nguồn gốc của "đã /stop rồi mà Chrome vẫn mở".
    """
    if proc.returncode is not None:
        return
    try:
        if sys.platform == "win32":
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                capture_output=True, timeout=15, check=False,
            )
        else:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    except Exception:  # noqa: BLE001 — tiến trình có thể vừa tự thoát
        try:
            proc.kill()
        except ProcessLookupError:
            pass


class SubprocessAdapter:
    """Phần chung của mọi adapter: chạy lệnh, canh huỷ, gom stdout/stderr.

    Lớp con chỉ cần cài `build_command()` và `is_available()`.
    """

    name = "subprocess"
    executable = ""

    def is_available(self) -> bool:
        from shutil import which
        return bool(self.executable) and which(self.executable) is not None

    def supports_resume(self) -> bool:
        return False

    def build_command(self, req: AgentRequest) -> list[str]:
        raise NotImplementedError

    def prompt_via_stdin(self) -> bool:
        """Đưa prompt qua stdin thay vì tham số dòng lệnh.

        Windows có trần ~32k ký tự cho một dòng lệnh. Cửa sổ hội thoại 20k token dễ
        vượt trần đó — và lỗi hiện ra dưới dạng "The command line is too long", một
        thông báo chẳng liên quan gì tới nguyên nhân thật.
        """
        return True

    async def run(self, req: AgentRequest) -> AgentResult:
        cmd = self.build_command(req)
        env = {**os.environ, **req.env}
        _log.debug("chạy: %s", " ".join(cmd[:6]) + (" …" if len(cmd) > 6 else ""))

        kwargs: dict = {
            "cwd": str(req.cwd),
            "env": env,
            "stdout": asyncio.subprocess.PIPE,
            "stderr": asyncio.subprocess.PIPE,
            "stdin": asyncio.subprocess.PIPE if self.prompt_via_stdin() else asyncio.subprocess.DEVNULL,
        }
        if sys.platform != "win32":
            # Nhóm tiến trình riêng để `killpg` quét được cả cây con.
            kwargs["preexec_fn"] = os.setsid
        else:
            kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP

        try:
            proc = await asyncio.create_subprocess_exec(*cmd, **kwargs)
        except FileNotFoundError:
            return AgentResult(
                ok=False, text="",
                stderr=f"Không tìm thấy lệnh `{cmd[0]}`. Kiểm tra PATH.",
                returncode=127,
            )

        payload = req.prompt.encode("utf-8") if self.prompt_via_stdin() else None
        try:
            out, err = await asyncio.wait_for(proc.communicate(payload), req.timeout)
        except asyncio.TimeoutError:
            kill_tree(proc)
            return AgentResult(ok=False, text="", stderr="quá thời gian cho phép", returncode=124)
        except asyncio.CancelledError:
            # Người dùng /stop hoặc daemon tắt. Giết cây con RỒI mới truyền lỗi lên.
            kill_tree(proc)
            raise

        text = (out or b"").decode("utf-8", errors="replace").strip()
        stderr = (err or b"").decode("utf-8", errors="replace").strip()
        return AgentResult(
            ok=proc.returncode == 0, text=text, stderr=stderr,
            returncode=proc.returncode or 0,
        )
