FROM python:3.12-slim

# Unbuffered is non-negotiable for stdio transport: buffered stdout means
# the gateway sees nothing until the buffer flushes, which reads as a hang.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

# Create the runtime user first so COPY --chown can reference it.
# Source files on the host are mode 600; without --chmod the image inherits
# that and user `mcp` can't read its own entrypoint.
RUN useradd --create-home --uid 10001 mcp

COPY --chown=mcp:mcp --chmod=0644 requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY --chown=mcp:mcp --chmod=0644 server.py .

USER mcp

# ENTRYPOINT in exec form (not shell form) — a shell wrapper becomes PID 1
# and can swallow or mangle the stdio stream.
ENTRYPOINT ["python", "-u", "server.py"]
