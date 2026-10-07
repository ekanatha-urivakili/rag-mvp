# One image; api / worker / ui selected by command.
FROM docker.io/library/python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    FASTEMBED_CACHE_PATH=/models \
    TIKTOKEN_CACHE_DIR=/opt/tiktoken \
    PATH="/app/.venv/bin:$PATH"

# OpenCV (pulled in by RapidOCR for receipt OCR) needs these shared libraries at import time.
RUN apt-get update \
 && apt-get install -y --no-install-recommends libgl1 libglib2.0-0t64 \
 && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv
WORKDIR /app

COPY pyproject.toml uv.lock* ./
RUN uv sync --no-dev --no-install-project $( [ -f uv.lock ] && echo --frozen )

COPY src ./src
COPY config ./config
COPY ui ./ui
COPY alembic.ini ./
RUN uv sync --no-dev $( [ -f uv.lock ] && echo --frozen ) \
 && python -c "import tiktoken; tiktoken.get_encoding('cl100k_base')"

# Unprivileged runtime user (rootless Podman maps this to an unprivileged host UID).
RUN useradd --system --uid 10001 --home /app app \
 && mkdir -p /models && chown -R app /models /opt/tiktoken
USER app

EXPOSE 8000 8501
CMD ["uvicorn", "rag.api.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-server-header", "--no-access-log"]
