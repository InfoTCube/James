FROM python:3.12-slim
COPY --from=ghcr.io/astral-sh/uv:0.12 /uv /bin/uv
# Claude Code CLI for core/ai.py (auth: CLAUDE_CODE_OAUTH_TOKEN in .env); PortAudio for voice
RUN apt-get update && apt-get install -y --no-install-recommends curl ca-certificates libportaudio2 \
    && curl -fsSL https://claude.ai/install.sh | bash \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy PATH="/app/.venv/bin:/root/.local/bin:$PATH" TZ=Europe/Warsaw
COPY pyproject.toml uv.lock .python-version ./
RUN uv sync --frozen --no-dev --no-install-project
COPY src ./src
RUN uv sync --frozen --no-dev
