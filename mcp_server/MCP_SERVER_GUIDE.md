# Custom MCP Server Guide — Python + Docker MCP Toolkit

Two things live here:

1. **The reusable prompt** (Part 1). Paste it into a fresh Claude / Claude Code chat, replace the two placeholders, and Claude will scaffold a working server that plugs cleanly into Docker MCP Toolkit and Claude on the first try.
2. **The install-and-invoke steps** (Part 2) that turn the generated files into a live server your profile can call.

The debugging checklist in Part 3 captures the specific failure modes that cost us time on the dice server. Every rule in the prompt exists because breaking it produces one of them.

---

## Part 1 — The reusable prompt

Copy everything between the fences into Claude. Replace `<SERVER_NAME>` and `<WHAT IT DOES>`; leave the rest verbatim.

````
Build me a Python MCP server that will run in Docker MCP Toolkit and be
invoked from Claude.

Purpose: <WHAT IT DOES — one to three sentences describing tools and behavior>
Server name (kebab-case): <SERVER_NAME>

Produce exactly four files in the current directory. Nothing else.

────────────────────────────────────────────────
FILE 1 — server.py
────────────────────────────────────────────────
- Uses the Python `mcp` SDK. Default to `mcp>=2.0.0` with:
    from mcp.server.mcpserver import MCPServer
    mcp = MCPServer(name="<SERVER_NAME>", version="0.1.0")
    @mcp.tool()
    def my_tool(...): ...
    if __name__ == "__main__":
        mcp.run(transport="stdio")
  (If the user pins `mcp<2`, swap to `from mcp.server.fastmcp import FastMCP`
  and `mcp = FastMCP(...)`. Same decorator, same run signature.)
- STDIO RULE — non-negotiable: stdout is the JSON-RPC transport. Every
  diagnostic goes to stderr (`print(..., file=sys.stderr)` or the `logging`
  module writing to stderr). A single stray `print()` to stdout corrupts the
  protocol stream and the gateway drops the server with an opaque parse error.
- Every tool: type-annotated parameters (the SDK derives the JSON schema from
  them), a docstring whose first paragraph is the tool description shown to
  the model, and a return value that is JSON-serializable (dict, list, str,
  int, float, bool, None — no dataclasses, no datetime without a serializer).
- Raise `ValueError` for bad user input. The SDK surfaces it cleanly as a
  tool error to the model.

────────────────────────────────────────────────
FILE 2 — requirements.txt
────────────────────────────────────────────────
mcp>=2.0.0
<any other deps>

────────────────────────────────────────────────
FILE 3 — Dockerfile
────────────────────────────────────────────────
Exact shape. The chown/chmod, user ordering, and exec-form ENTRYPOINT all
prevent specific failure modes — do not "clean up" any of them.

    FROM python:3.12-slim

    ENV PYTHONUNBUFFERED=1 \
        PYTHONDONTWRITEBYTECODE=1

    WORKDIR /app

    # Create the runtime user BEFORE COPY so --chown can reference it.
    # Host source files may be mode 600; COPY preserves mode, so without
    # --chmod the unprivileged user cannot read its own entrypoint and
    # the gateway sees "Permission denied" instead of a running server.
    RUN useradd --create-home --uid 10001 mcp

    COPY --chown=mcp:mcp --chmod=0644 requirements.txt .
    RUN pip install --no-cache-dir -r requirements.txt

    COPY --chown=mcp:mcp --chmod=0644 server.py .

    USER mcp

    # Exec form — a shell wrapper as PID 1 can swallow or mangle stdio.
    ENTRYPOINT ["python", "-u", "server.py"]

────────────────────────────────────────────────
FILE 4 — <SERVER_NAME>.yaml   (the Docker MCP catalog entry)
────────────────────────────────────────────────
CRITICAL: the Docker MCP Toolkit UI reads the tool list from THIS FILE, not
by live-probing the container. If the `tools:` block is missing or wrong,
the server appears greyed out in the UI even though the container is healthy
and the gateway can call it. Every tool defined in server.py must appear
here with matching name and arguments.

    name: <SERVER_NAME>
    title: <Human-Readable Title>
    type: server
    image: <SERVER_NAME>:latest
    description: <One-line description shown in the toolkit list>
    tools:
      - name: <tool_name_matching_server.py>
        description: <shown to the model — be specific about when to use it>
        arguments:
          - name: <arg_name>
            type: <string|integer|number|boolean|array|object>
            desc: <what the arg controls>
            optional: true   # omit or set false for required args

Ask me before writing if my purpose statement is missing anything you'd need
to define the tool signatures (arg names, types, required vs optional).
Otherwise, write all four files and print nothing else.
````

That prompt is the whole contract. Follow it and Part 2 will "just work."

---

## Part 2 — Install and invoke (per new server)

Run these from the directory containing the four generated files. Substitute your `<SERVER_NAME>` (the value you gave in the prompt).

```bash
# 1. Build the image. The tag must match the `image:` field in the YAML.
docker build -t <SERVER_NAME>:latest .

# 2. Sanity-check the container as the runtime user.
#    Should exit 0. If it prints "Permission denied", the Dockerfile got
#    edited — restore the --chown/--chmod on the COPY lines.
docker run --rm --entrypoint sh <SERVER_NAME>:latest \
  -c 'timeout 2 python -u /app/server.py < /dev/null; echo exit=$?'

# 3. Place the catalog file where Docker MCP Toolkit looks for it.
#    Every file under ~/.docker/mcp/catalogs is a candidate `file://` source.
mkdir -p ~/.docker/mcp/catalogs
cp <SERVER_NAME>.yaml ~/.docker/mcp/catalogs/

# 4. Attach the server to a profile. Replace <PROFILE_ID> (e.g. ai_handyman).
#    Use file:// not docker:// — docker:// requires self-describing OCI
#    labels on the image, which our lean Dockerfile does not add.
docker mcp profile server add <PROFILE_ID> --server file://<SERVER_NAME>.yaml

# 5. Confirm the snapshot has the tools block. If `tools:` is missing here,
#    the UI will still show 0 tools — go fix the YAML and re-add.
docker mcp profile show <PROFILE_ID> | awk '/name: <SERVER_NAME>$/,/^secrets:$/'
```

Then in Docker Desktop → MCP Toolkit → the profile: the server should show `N/N enabled` where N is the tool count. Toggle tools on if they default off.

To make Claude Code (or Claude Desktop) see the new tools, restart the client — MCP clients cache the tool list at session start and don't re-probe mid-session.

**Iterating on server.py:** rebuild the image and restart the Claude client. No profile re-add needed — the gateway resolves `<SERVER_NAME>:latest` fresh each session.

**Changing tool signatures or adding tools:** update `server.py` AND the YAML `tools:` block, then `docker build`, then remove + re-add on the profile so the snapshot refreshes:

```bash
docker mcp profile server remove <PROFILE_ID> <SERVER_NAME>
docker mcp profile server add    <PROFILE_ID> --server file://<SERVER_NAME>.yaml
```

---

## Part 3 — Debugging checklist

When something is off, the symptom usually maps 1:1 to one of these.

| Symptom | Cause | Fix |
|---|---|---|
| Server row is greyed out with no `N/N enabled` label | Container failed to start — usually permissions | `docker run --rm <img>` — if you see `Permission denied` on `/app/server.py`, restore `COPY --chown --chmod` in the Dockerfile |
| UI shows the row but says `0/N` or no tools | `tools:` block missing from the catalog YAML | Add the block, remove + re-add server on profile |
| Client sees the server but tool call hangs or errors out with a parse error | Something wrote to stdout in server.py | Grep for bare `print(` — route all diagnostics to stderr |
| `docker mcp profile server add ... --server docker://...` fails with "not a self-describing image" | Image lacks MCP OCI labels | Use `file://<catalog>.yaml` instead |
| Container starts but exits immediately | Buffered stdio, or shell-form ENTRYPOINT ate stdin | Confirm `ENV PYTHONUNBUFFERED=1` and exec-form `ENTRYPOINT ["python", "-u", ...]` |
| Tools work standalone (`docker run` + JSON-RPC probe) but not through the gateway | Cached bad snapshot on the profile | Remove + re-add the server on the profile |
| Claude client doesn't see the new tool after editing server.py | Client cached tools at startup | Restart the Claude client |

**End-to-end probe (skip Claude, test the gateway directly):**

```bash
( printf '%s\n' \
    '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"probe","version":"0.0.0"}}}' \
    '{"jsonrpc":"2.0","method":"notifications/initialized"}' \
    '{"jsonrpc":"2.0","id":2,"method":"tools/list"}'; \
  sleep 15 ) \
  | docker mcp gateway run --profile <PROFILE_ID> 2>/dev/null \
  | grep -o '"name":"[^"]*"' | sort -u
```

You should see every tool name across every attached server.

---

## Appendix — Secrets and environment variables

For servers that need API keys or config (e.g. a GitHub server needing `GITHUB_TOKEN`), extend two things.

**In the catalog YAML:**

```yaml
name: <SERVER_NAME>
# ...existing fields...
secrets:
  - name: <server_name>.api_key      # dot-namespaced; toolkit stores it in the OS keychain
    env: <SERVER_NAME>_API_KEY       # environment variable name inside the container
env:
  - name: <SERVER_NAME>_API_URL      # non-secret config, prompted per profile
    value: '{{<server_name>.api_url}}'
```

**In server.py:**

```python
import os
API_KEY = os.environ["<SERVER_NAME>_API_KEY"]  # fail loudly at boot if missing
```

**Set the secret once per host:**

```bash
docker mcp secret set <server_name>.api_key
# (interactive prompt — the value goes to the OS keychain, never a file)
```

The `env:` values with `{{...}}` templating get prompted per profile via `docker mcp profile config <profile>` or the Docker Desktop UI. Never hardcode secrets in the Dockerfile or the catalog YAML — both get shared / committed and both are the wrong place.
