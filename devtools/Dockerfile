# Build context is the repository root: docker build -f devtools/Dockerfile .
# One image for fake-aws (default command) and the one-shot seed.
FROM docker.io/library/python:3.14-slim
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    PATH="/app/devtools/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1

WORKDIR /app/devtools

COPY devtools/pyproject.toml devtools/uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev

COPY devtools/fake_aws ./fake_aws
COPY devtools/seed.py ./
# seed.py reads the official-doc fixtures from the same relative path as in the repository.
COPY backend/tests/fixtures/aws /app/backend/tests/fixtures/aws

RUN useradd --uid 10001 --no-create-home --shell /usr/sbin/nologin app
USER app

EXPOSE 4567
HEALTHCHECK --interval=5s --timeout=3s --start-period=5s --retries=10 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:4567/health', timeout=2).status == 200 else 1)"

CMD ["uvicorn", "fake_aws.app:app", "--host", "0.0.0.0", "--port", "4567"]
