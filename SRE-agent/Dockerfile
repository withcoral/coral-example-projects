FROM python:3.14-slim

# curl is needed to fetch the Coral CLI installer; ca-certificates for HTTPS.
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Install the published Coral CLI and put the binary on PATH.
RUN curl -fsSL https://withcoral.com/install.sh | sh
ENV PATH="/root/.local/bin:${PATH}"

# Install uv (used to manage the Python application).
COPY --from=ghcr.io/astral-sh/uv:0.11.16 /uv /uvx /usr/local/bin/

WORKDIR /app

# Install production dependencies first for better layer caching.
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --no-install-project

# Install the application itself.
COPY src ./src
COPY scripts ./scripts
RUN uv sync --frozen --no-dev

# Run the app from the uv-managed virtualenv.
ENV PATH="/app/.venv/bin:${PATH}"

ENTRYPOINT ["./scripts/docker-entrypoint.sh"]
