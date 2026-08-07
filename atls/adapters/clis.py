"""
Adapter cho từng agent CLI.

Mỗi lớp ở đây chỉ trả lời đúng một câu hỏi: *"gọi CLI này thế nào để nó chạy một lượt
headless và in kết quả ra stdout?"*. Khác biệt giữa chúng nhiều hơn ta tưởng — cách
truyền system prompt, cách đặt id session, cách bật chế độ không hỏi quyền, mỗi CLI
một kiểu.

Thêm CLI mới: kế thừa `SubprocessAdapter`, cài `build_command`, đăng ký vào `REGISTRY`.
"""

from __future__ import annotations

import shlex
from pathlib import Path

from atls.adapters.base import AgentRequest, SubprocessAdapter


class ClaudeAdapter(SubprocessAdapter):
    """Claude Code — `claude --print`.

    `--session-id <uuid>` tạo session mới có id ta chọn; `--resume <uuid>` nối tiếp.
    Dùng sai chiều (resume vào id chưa tồn tại) là lỗi chắc chắn, nên `SessionManager`
    theo dõi cờ `started` để biết CLI đã thật sự tạo session hay chưa.
    """

    name = "claude"
    executable = "claude"

    def __init__(self, skip_permissions: bool = True) -> None:
        # Có cờ tắt là điều kiện để `--dangerously-skip-permissions` được phép tồn tại.
        # Nó là thứ biến "một tin nhắn Telegram" thành "một lệnh chạy trên máy chủ", và
        # một tuỳ chọn nguy hiểm mà không có đường tắt thì không phải quyết định thiết
        # kế, chỉ là hardcode. Mặc định vẫn bật vì cả sản phẩm dựa trên việc agent làm
        # được việc thật; ai chạy ở nơi không tin được thì tắt.
        self._skip_permissions = skip_permissions

    def supports_resume(self) -> bool:
        return True

    def prompt_via_stdin(self) -> bool:
        return True

    def build_command(self, req: AgentRequest) -> list[str]:
        cmd = [
            self.executable, "--print",
            *(["--dangerously-skip-permissions"] if self._skip_permissions else []),
            *(["--resume", req.session_id] if req.resume else ["--session-id", req.session_id]),
        ]
        if req.model:
            cmd += ["--model", req.model]
        if req.system:
            cmd += ["--append-system-prompt", req.system]
        return cmd


class CodexAdapter(SubprocessAdapter):
    """OpenAI Codex CLI — `codex exec`.

    Codex đặt tên session theo id nội bộ của nó, không nhận id từ ngoài; `codex exec
    resume --last` là đường nối tiếp phiên gần nhất **trong cùng thư mục làm việc**.
    Vì mỗi chat có `cwd` riêng khi cần cách ly, mặc định ta để ATLS tự dán cửa sổ hội
    thoại thay vì phụ thuộc `--last` — an toàn hơn và không sai khi nhiều chat dùng
    chung một thư mục.
    """

    name = "codex"
    executable = "codex"

    def supports_resume(self) -> bool:
        return False

    def build_command(self, req: AgentRequest) -> list[str]:
        cmd = [
            self.executable, "exec",
            "--skip-git-repo-check",
            "--sandbox", "danger-full-access",
        ]
        if req.model:
            cmd += ["--model", req.model]
        # Codex không có cờ append-system-prompt; nhét system vào đầu prompt là đường
        # duy nhất. Đánh dấu rõ ranh giới để model không nhầm nó là lời người dùng.
        return cmd

    def compose_prompt(self, req: AgentRequest) -> str:
        return f"<system>\n{req.system}\n</system>\n\n{req.prompt}" if req.system else req.prompt

    async def run(self, req: AgentRequest):
        merged = AgentRequest(
            prompt=self.compose_prompt(req), system="", session_id=req.session_id,
            resume=False, cwd=req.cwd, model=req.model, timeout=req.timeout, env=req.env,
        )
        return await super().run(merged)


class OpenCodeAdapter(SubprocessAdapter):
    """OpenCode (Go) — `opencode run`.

    `--session <id>` nối tiếp một session đã có. OpenCode nhận id từ ngoài nên ta
    dùng luôn uuid của ATLS, giữ được ánh xạ một-một giữa chat và session.
    """

    name = "opencode"
    executable = "opencode"

    def supports_resume(self) -> bool:
        return True

    def build_command(self, req: AgentRequest) -> list[str]:
        cmd = [self.executable, "run"]
        if req.model:
            cmd += ["--model", req.model]
        if req.resume:
            cmd += ["--session", req.session_id]
        return cmd

    def compose_prompt(self, req: AgentRequest) -> str:
        return f"<system>\n{req.system}\n</system>\n\n{req.prompt}" if req.system else req.prompt

    async def run(self, req: AgentRequest):
        merged = AgentRequest(
            prompt=self.compose_prompt(req), system="", session_id=req.session_id,
            resume=req.resume, cwd=req.cwd, model=req.model, timeout=req.timeout, env=req.env,
        )
        return await super().run(merged)


class TemplateAdapter(SubprocessAdapter):
    """Adapter đa dụng lái bằng `ATLS_AGENT_CMD`.

    Dành cho antigravity và mọi CLI chưa có lớp riêng. Placeholder được thay lúc chạy:

        {prompt} {system} {session_id} {model} {cwd}

    Ví dụ:
        ATLS_AGENT_CMD=my-cli run --system {system} --sid {session_id} -- {prompt}

    Không có `{prompt}` trong template thì prompt đi qua stdin. Đây là đường thoát để
    không phải sửa code ATLS mỗi khi có một CLI mới ra đời.
    """

    name = "custom"

    def __init__(self, template: str, name: str = "custom") -> None:
        self._template = template
        self.name = name
        parts = shlex.split(template, posix=False) if template else []
        self.executable = parts[0] if parts else ""

    def is_available(self) -> bool:
        from shutil import which
        return bool(self.executable) and which(self.executable) is not None

    def prompt_via_stdin(self) -> bool:
        return "{prompt}" not in self._template

    def build_command(self, req: AgentRequest) -> list[str]:
        values = {
            "prompt": req.prompt,
            "system": req.system,
            "session_id": req.session_id,
            "model": req.model,
            "cwd": str(req.cwd),
        }
        # Tách token TRƯỚC khi thay giá trị: thay trước rồi tách thì một prompt có dấu
        # nháy hay khoảng trắng sẽ bị vỡ thành nhiều tham số.
        out: list[str] = []
        for token in shlex.split(self._template, posix=False):
            for key, value in values.items():
                token = token.replace("{" + key + "}", value)
            out.append(token)
        return out


class AntigravityAdapter(TemplateAdapter):
    """Antigravity CLI.

    Chưa chốt được cú pháp ổn định cho lượt headless nên mặc định dùng dạng phổ biến
    nhất (`antigravity run`) và cho phép ghi đè toàn bộ bằng `ATLS_AGENT_CMD`. Nói
    thẳng chỗ chưa chắc còn hơn hardcode một cú pháp sai rồi để người dùng tự đoán.
    """

    def __init__(self, template: str = "") -> None:
        super().__init__(template or "antigravity run", name="antigravity")


REGISTRY = {
    "claude": ClaudeAdapter,
    "codex": CodexAdapter,
    "opencode": OpenCodeAdapter,
}


def build_adapter(kind: str, template: str = "", *, skip_permissions: bool = True):
    kind = (kind or "claude").lower()
    if kind == "antigravity":
        return AntigravityAdapter(template)
    if kind == "custom":
        if not template:
            raise ValueError("ATLS_AGENT=custom nhưng thiếu ATLS_AGENT_CMD")
        return TemplateAdapter(template)
    cls = REGISTRY.get(kind)
    if cls is None:
        raise ValueError(
            f"Không biết agent '{kind}'. Chọn: {', '.join(sorted(REGISTRY))}, "
            "antigravity, custom."
        )
    if cls is ClaudeAdapter:
        return cls(skip_permissions=skip_permissions)
    return cls()


def available_agents() -> dict[str, bool]:
    """Bảng {tên: có cài chưa} — dùng cho `atls doctor`."""
    out = {name: cls().is_available() for name, cls in REGISTRY.items()}
    out["antigravity"] = AntigravityAdapter().is_available()
    return out
