# Build context is the repository root: docker build -f backend/Dockerfile .
# One image for api, worker and migrate; the command decides which one runs.

# The path prefix the API and dashboard are served under (DESIGN 12.3). The only place it is set:
# the dashboard is built with it and the backend reads it from the image's environment. Override
# with --build-arg BASE_PATH=... (empty serves at the root).
ARG BASE_PATH=/aws-inventory

# Stage 1: build the dashboard
FROM docker.io/library/node:26-slim AS frontend
WORKDIR /frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
# Declared after `npm ci` so changing it keeps the dependency layer cached. Vite reads it as base.
ARG BASE_PATH
RUN npm run build

# Stage 2: Python runtime
FROM docker.io/library/python:3.14-slim AS runtime
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

ARG BASE_PATH
ENV BASE_PATH=${BASE_PATH} \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Dependencies first so they stay cached until the lock file changes.
COPY backend/pyproject.toml backend/uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-project

COPY backend/alembic.ini ./
COPY backend/app ./app
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev

COPY --from=frontend /frontend/dist ./frontend/dist

RUN useradd --uid 10001 --no-create-home --shell /usr/sbin/nologin app
USER app

EXPOSE 8000
# This is the api's check. The worker overrides it (compose.yaml and deploy/ecs/worker.json) with
# `python -m app.cli healthcheck`, which looks at the worker's heartbeat file.
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import os,sys,urllib.request; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000' + os.environ.get('BASE_PATH', '') + '/api/v1/health', timeout=4).status == 200 else 1)"

# The ALB terminates HTTPS: trust its X-Forwarded-* headers so redirects keep https. Port 8000 is
# reachable only from the ALB's security group.
CMD ["fastapi", "run", "--port", "8000", "--forwarded-allow-ips", "*"]
