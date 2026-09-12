# syntax=docker/dockerfile:1

# ---------------------------------------------------------------------------
# Build stage: install Python dependencies into an isolated virtualenv.
# Everything from requirements.txt ships as wheels (psycopg2-binary bundles
# libpq), so no compiler toolchain is needed and the result can be copied
# into a clean runtime image.
# ---------------------------------------------------------------------------
FROM python:3.11-slim AS builder

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /build

RUN python -m venv /opt/venv

COPY requirements.txt .
RUN /opt/venv/bin/pip install -r requirements.txt

# ---------------------------------------------------------------------------
# Runtime stage: slim image, non-root user, only the venv + application code.
# ---------------------------------------------------------------------------
FROM python:3.11-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH"

RUN groupadd --system --gid 1001 app \
    && useradd --system --uid 1001 --gid app --home-dir /app --shell /usr/sbin/nologin app

# Mirror the repo layout (<root>/backend/app) so app.config.ROOT_DIR resolves
# to /app and an optional .env can be mounted at /app/.env.
WORKDIR /app/backend

COPY --from=builder /opt/venv /opt/venv
COPY --chown=app:app app ./app

# Upload directories are relative to the working directory (see app/config.py)
# and must be writable by the non-root user.
RUN mkdir -p uploads && chown -R app:app /app

USER app

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD ["python", "-c", "import sys, urllib.request; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=4).status == 200 else 1)"]

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
