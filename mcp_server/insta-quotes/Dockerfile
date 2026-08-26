FROM python:3.12-slim

# Unbuffered is non-negotiable for stdio transport: buffered stdout means
# the gateway sees nothing until the buffer flushes, which reads as a hang.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

RUN useradd --create-home --uid 10001 mcp

COPY --chown=mcp:mcp --chmod=0644 requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY --chown=mcp:mcp --chmod=0644 server.py founders.json ./

USER mcp

ENTRYPOINT ["python", "-u", "server.py"]
