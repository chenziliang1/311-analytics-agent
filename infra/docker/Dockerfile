# One image, two AgentCore runtimes: AGENT_APP_ROLE=agent serves the agent
# (HTTP protocol, port 8080), AGENT_APP_ROLE=mcp serves the data tools (MCP
# protocol, port 8000). Built for ARM64, which AgentCore Runtime requires.
#
# Build context is the repo root, filtered by the allow-list in /.dockerignore:
# nothing reaches the image unless it is named there, so .env and the SQLite
# seed cannot leak in by accident.

FROM --platform=linux/arm64 python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:0.9 /uv /usr/local/bin/uv

ENV PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PATH="/app/.venv/bin:$PATH"

WORKDIR /app

# Dependencies first, in their own layer: they change far less often than the
# code. Core dependencies only -- no test or local-login extras.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY agent_app ./agent_app
COPY data ./data
COPY .env.defaults ./
RUN uv sync --frozen --no-dev

COPY infra/docker/entrypoint.sh /entrypoint.sh

# Not root. tmp/ is the one place the app writes (query log, debug report).
RUN useradd --create-home --uid 1000 app \
    && mkdir -p /app/tmp \
    && chown -R app:app /app/tmp \
    && chmod 0755 /entrypoint.sh
USER app

EXPOSE 8080 8000
ENTRYPOINT ["/entrypoint.sh"]
