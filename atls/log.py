"""
Log — một chỗ cấu hình, ghi ra cả stdout lẫn file theo ngày.

Bài học từ bản tiền nhiệm: daemon chạy cửa sổ ẩn thì mọi dòng `print` rơi vào hư
không, và lúc cần truy một lượt chạy hỏng thì không còn gì để đọc. Ghi file là bắt buộc,
không phải tuỳ chọn.
"""

from __future__ import annotations

import logging
import sys
from datetime import date
from pathlib import Path

_CONFIGURED = False


def setup(level: str = "INFO", log_dir: Path | None = None) -> None:
    global _CONFIGURED
    if _CONFIGURED:
        return

    root = logging.getLogger("atls")
    root.setLevel(getattr(logging, level.upper(), logging.INFO))
    fmt = logging.Formatter(
        "%(asctime)s %(levelname)-7s %(name)-22s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # stdout phải UTF-8: log tiếng Việt trên Windows mặc định cp1252 sẽ vỡ hoặc
    # ném UnicodeEncodeError giữa chừng và giết cả daemon.
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001 — stream không phải TextIO thật (test, pipe lạ)
        pass

    stream = logging.StreamHandler(sys.stdout)
    stream.setFormatter(fmt)
    root.addHandler(stream)

    if log_dir:
        log_dir.mkdir(parents=True, exist_ok=True)
        fh = logging.FileHandler(
            log_dir / f"atls-{date.today().isoformat()}.log", encoding="utf-8"
        )
        fh.setFormatter(fmt)
        root.addHandler(fh)

    # aiohttp access log mỗi request webhook là rác thuần tuý ở mức INFO.
    logging.getLogger("aiohttp.access").setLevel(logging.WARNING)
    _CONFIGURED = True


def get(name: str) -> logging.Logger:
    return logging.getLogger(f"atls.{name}")
