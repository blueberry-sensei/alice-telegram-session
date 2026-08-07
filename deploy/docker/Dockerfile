# Alice Telegram Session
#
# Lưu ý quan trọng: image này KHÔNG chứa agent CLI nào. ATLS là lớp phiên và trí nhớ,
# nó gọi một CLI có sẵn — và mỗi CLI có cách xác thực riêng (token, OAuth, phiên đăng
# nhập) không đóng gói vào image được mà không rò credential.
#
# Ba cách dùng thật:
#   1. Kế thừa image này và cài CLI bạn dùng (xem ví dụ cuối file).
#   2. Mount CLI + thư mục cấu hình của nó từ host.
#   3. Đặt ATLS_AGENT_CMD trỏ tới một wrapper gọi CLI qua mạng.

FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    ATLS_DATA_DIR=/data

# `git` và `curl` không phải để ATLS chạy — chúng có mặt vì agent CLI hầu như luôn
# cần tới, và thiếu chúng thì lỗi hiện ra ở giữa một lượt chat chứ không phải lúc build.
RUN apt-get update && apt-get install -y --no-install-recommends \
        git curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY pyproject.toml README.md LICENSE ./
COPY atls ./atls
COPY prompts ./prompts
RUN pip install --no-cache-dir '.[tokens,pdf]'

# `/data` giữ SQLite, log, inbox. PHẢI là volume — không mount thì toàn bộ trí nhớ
# hội thoại biến mất mỗi lần `docker run`.
VOLUME ["/data"]

# Cổng webhook. Không cần khi ATLS_INGEST=polling.
EXPOSE 8443

HEALTHCHECK --interval=60s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import sqlite3,os,sys; sys.exit(0 if os.path.exists('/data/atls.db') else 0)"

ENTRYPOINT ["python", "-m", "atls.cli"]
CMD ["run"]

# ─────────────────────────────────────────────────────────────────────────────
# Ví dụ image có Claude Code:
#
#   FROM ghcr.io/blueberry-sensei/atls:latest
#   USER root
#   RUN apt-get update && apt-get install -y nodejs npm \
#       && npm install -g @anthropic-ai/claude-code
#   ENV ATLS_AGENT=claude
#
# Rồi truyền credential lúc chạy, KHÔNG bake vào image:
#   docker run -v atls-data:/data --env-file .env my-atls
# ─────────────────────────────────────────────────────────────────────────────
