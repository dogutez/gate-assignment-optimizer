# One image for both services: the API (default command) and the worker
# (docker-compose overrides the command).
FROM python:3.12-slim

# uv installs the exact versions pinned in uv.lock.
COPY --from=ghcr.io/astral-sh/uv:0.13.6 /uv /bin/uv

WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:$PATH" \
    JOBS_DB_PATH=/data/jobs.db

# Dependencies first: this layer is cached until pyproject.toml or uv.lock changes.
COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev

COPY app ./app

# Run as a non-root user; /data holds the SQLite file (a volume in docker-compose).
RUN useradd --create-home --uid 1000 appuser \
    && mkdir /data && chown appuser /data
USER appuser

EXPOSE 8000
# 0.0.0.0 inside the container is required for port publishing; docker-compose
# publishes it on the host as 127.0.0.1 only.
CMD ["python", "-m", "app.api", "--host", "0.0.0.0", "--port", "8000"]
