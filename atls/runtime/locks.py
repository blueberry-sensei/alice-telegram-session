"""
Ba loại khoá. Đừng nhầm chúng với nhau — mỗi cái chặn một tai nạn khác nhau.

┌────────────────┬──────────────┬──────────────────────────────────────────────┐
│ SingletonLock  │ tiến trình   │ Hai daemon cùng chạy sau restart lỗi         │
│ ChatLock       │ mỗi chat     │ Hai agent cùng một cuộc hội thoại            │
│ ResourceLock   │ toàn máy     │ Tranh Chrome profile / brain sync            │
└────────────────┴──────────────┴──────────────────────────────────────────────┘

`ChatLock` sống trong tiến trình (asyncio) vì chỉ một daemon được chạy — đã có
`SingletonLock` bảo đảm điều đó.

`ResourceLock` phải là **file**, không phải asyncio, vì bên tranh chấp có thể là
script ngoài repo (routine theo lịch, phiên agent tương tác người dùng tự mở). File
có heartbeat là giao thức duy nhất mà cả hai phía đều đọc được.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import threading
import time
from collections import defaultdict
from pathlib import Path

from atls import log

_log = log.get("runtime.locks")

# Khoá cũ hơn ngưỡng này coi như chủ của nó đã chết (máy sập, process bị kill -9).
STALE_SECONDS = 30 * 60
HEARTBEAT_SECONDS = 30


class SingletonLock:
    """Một daemon một máy. Dùng PID **và** nhịp tim để phân biệt khoá chết với khoá sống.

    Chỉ PID là chưa đủ, vì hệ điều hành tái dùng PID — Windows rất nhanh. Máy sập, khởi
    động lại, một tiến trình bất kỳ nhận đúng con số PID cũ, và `os.kill(pid, 0)` bảo
    "còn sống". Daemon thật thì không bao giờ khởi động lại được nữa, thông báo lỗi thì
    chỉ đường đi xoá file lock bằng tay — đúng thứ lớp khoá này sinh ra để khỏi phải làm.

    Nên chủ khoá **chạm mtime** đều đặn suốt đời nó. Chạm bằng thread chứ không bằng
    asyncio: `acquire()` được gọi trước khi có event loop nào (`cmd_run` gọi nó rồi mới
    tới `asyncio.run`), và một daemon thread thì không giữ tiến trình sống thêm lúc thoát.
    """

    def __init__(self, path: Path) -> None:
        self._path = path
        self._held = False
        self._stop = threading.Event()
        self._beat: threading.Thread | None = None

    def acquire(self) -> bool:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        if self._path.exists():
            if self._alive() and not self._ancient():
                return False
            _log.warning("dọn lock daemon mồ côi: %s", self._path)
            self._path.unlink(missing_ok=True)
        try:
            fd = os.open(str(self._path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            return False
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(f"{os.getpid()}\n{time.time()}\n")
        self._held = True
        self._stop.clear()
        self._beat = threading.Thread(target=self._heartbeat, daemon=True, name="atls-lock-beat")
        self._beat.start()
        return True

    def _heartbeat(self) -> None:
        while not self._stop.wait(HEARTBEAT_SECONDS):
            try:
                self._path.touch()
            except OSError:
                return

    def _ancient(self) -> bool:
        """Khoá quá cũ so với nhịp tim → chủ của nó không còn chạm nó nữa.

        Đây là thứ phá được thế kẹt PID-tái-dùng: tiến trình mượn PID không biết gì về
        file này nên không bao giờ chạm nó.
        """
        try:
            return (time.time() - self._path.stat().st_mtime) > STALE_SECONDS
        except OSError:
            return True

    def _alive(self) -> bool:
        """PID trong file còn sống không? Không đọc được thì coi là CHẾT — thà nhận
        nhầm quyền còn hơn không bao giờ khởi động lại được sau một lần máy sập."""
        try:
            pid = int(self._path.read_text(encoding="utf-8").splitlines()[0])
        except (OSError, ValueError, IndexError):
            return False
        # KHÔNG có ngoại lệ "pid == os.getpid() thì coi là chết". Nghe hợp lý (dọn lock
        # của chính mình) nhưng nó phá đúng thứ lớp này sinh ra để làm: hai đối tượng
        # SingletonLock trong CÙNG tiến trình sẽ đều chiếm được lock.
        try:
            os.kill(pid, 0)  # signal 0 = chỉ hỏi "có tồn tại không"
            return True
        except ProcessLookupError:
            return False
        except PermissionError:
            return True  # tồn tại nhưng khác user
        except OSError:
            return False

    def release(self) -> None:
        if self._held:
            self._stop.set()
            self._path.unlink(missing_ok=True)
            self._held = False

    def __enter__(self) -> "SingletonLock":
        if not self.acquire():
            raise RuntimeError(
                f"Đã có một ATLS daemon đang chạy (lock: {self._path}). "
                "Dừng tiến trình cũ, hoặc xoá file lock nếu chắc chắn nó đã chết."
            )
        return self

    def __exit__(self, *exc) -> None:
        self.release()


class ChatLockRegistry:
    """Một `asyncio.Lock` cho mỗi chat, tạo lười.

    Đây là hiện thân trực tiếp của ràng buộc "không bao giờ có hai agent trên cùng
    một chat". Chat khác nhau → khoá khác nhau → chạy song song thật.
    """

    def __init__(self) -> None:
        self._locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)

    def get(self, chat_id: str) -> asyncio.Lock:
        return self._locks[chat_id]

    def busy(self, chat_id: str) -> bool:
        lock = self._locks.get(chat_id)
        return bool(lock and lock.locked())

    def busy_chats(self) -> list[str]:
        return [c for c, l in self._locks.items() if l.locked()]


class ResourceLock:
    """Khoá tài nguyên dùng chung, dạng file có heartbeat.

    Hợp đồng với script bên ngoài: file tồn tại **và** mtime mới hơn `STALE_SECONDS`
    nghĩa là đang bận. Chỉ có thế. Không JSON, không schema — để một hook PowerShell
    ba dòng cũng kiểm được.
    """

    def __init__(self, path: Path, name: str) -> None:
        self._path = path
        self._name = name
        self._beat: asyncio.Task | None = None

    @staticmethod
    def is_busy(path: Path) -> bool:
        if not path.exists():
            return False
        try:
            return (time.time() - path.stat().st_mtime) <= STALE_SECONDS
        except OSError:
            return False

    async def acquire(self, timeout: float) -> bool:
        deadline = time.time() + timeout
        self._path.parent.mkdir(parents=True, exist_ok=True)
        while time.time() < deadline:
            # O_EXCL, không phải `exists()` rồi `write()`: giữa hai lệnh đó có một khe
            # thời gian mà tiến trình khác chen vào và cả hai đều tin mình giữ khoá.
            try:
                fd = os.open(str(self._path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            except FileExistsError:
                if not self.is_busy(self._path):
                    _log.warning("dọn khoá tài nguyên chết: %s", self._name)
                    self._path.unlink(missing_ok=True)
                    continue
                await asyncio.sleep(2)
                continue
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(f"{os.getpid()}\n{self._name}\n{time.time()}\n")
            self._beat = asyncio.create_task(self._heartbeat())
            return True
        return False

    async def _heartbeat(self) -> None:
        """Chạm mtime đều đặn. Không có nhịp này thì một việc dài 40 phút sẽ bị chính
        ngưỡng stale của mình coi là chết và bị kẻ khác cướp khoá giữa chừng."""
        try:
            while True:
                await asyncio.sleep(HEARTBEAT_SECONDS)
                with contextlib.suppress(OSError):
                    self._path.touch()
        except asyncio.CancelledError:
            pass

    def release(self) -> None:
        if self._beat:
            self._beat.cancel()
            self._beat = None
        self._path.unlink(missing_ok=True)

    @contextlib.asynccontextmanager
    async def hold(self, timeout: float = 900):
        got = await self.acquire(timeout)
        try:
            yield got
        finally:
            if got:
                self.release()
