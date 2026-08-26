# Two-stage build so the final image doesn't carry uv or any build tooling.
# Follows the MCP-Toolkit conventions from the project's server guide:
# non-root user, exec-form ENTRYPOINT (shell wrapper as PID 1 mangles stdio),
# --chmod on COPY (host source files can be mode 600, which would leave the
# runtime user unable to read its own entrypoint).

# ---------- builder ----------
FROM python:3.12-slim AS builder

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    PYTHONDONTWRITEBYTECODE=1

RUN pip install --no-cache-dir "uv>=0.4"

WORKDIR /app

# Copy metadata first so `uv sync` layers on top of a stable dep set,
# not invalidated by every source edit.
COPY --chmod=0644 pyproject.toml uv.lock README.md ./
COPY --chmod=0644 src/ ./src/

# --frozen: use the lockfile as-is (fail if drift). --no-dev: skip test deps.
RUN uv sync --frozen --no-dev

# ---------- runtime ----------
FROM python:3.12-slim AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/app/.venv/bin:$PATH"

WORKDIR /app

# Create the runtime user BEFORE COPY so --chown can reference it, and
# create /data with correct ownership before the VOLUME directive so the
# unprivileged user can write the SQLite ledger.
RUN useradd --create-home --uid 10001 mcp \
    && mkdir -p /data \
    && chown -R mcp:mcp /data

COPY --from=builder --chown=mcp:mcp --chmod=0755 /app /app

# Declare after chown so bind-mounts inherit the right permissions.
VOLUME ["/data"]

USER mcp

# Exec form — a shell wrapper as PID 1 swallows or mangles stdio, which
# breaks the JSON-RPC transport in ways that surface as opaque parse errors.
ENTRYPOINT ["python", "-u", "-m", "trend_radar"]
