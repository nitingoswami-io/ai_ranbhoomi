# Custom MCP server in Docker Desktop — end to end

Requires **Docker Desktop 4.62+** (the `docker mcp` CLI surface below landed there).
Verify with `docker mcp --version`. If the plugin is missing, enable **MCP Toolkit**
in Docker Desktop settings, or build it from `github.com/docker/mcp-gateway`.

---

## The architecture, in one paragraph

Your client (Claude Desktop, VS Code, Cursor) does **not** talk to your server.
It talks to a single **MCP Gateway** process, which multiplexes N containerized
servers behind one connection. The gateway starts your container, attaches to its
stdin/stdout, speaks JSON-RPC over that pipe, and aggregates your tools into the
list the client sees. That indirection is the whole product: one client config
entry, N servers, each isolated in its own container, secrets injected by the
toolkit rather than pasted into a client config file.

Three nouns you need to keep straight:

| Noun | What it is | Scope |
|---|---|---|
| **Server** | One containerized MCP implementation (yours) | The unit of work |
| **Profile** | A named set of enabled servers + their config | Per-project / per-workflow |
| **Catalog** | A distributable index of server definitions, shipped as an OCI artifact | Per-team / per-org |

For local development you need a server and a profile. Catalogs only matter when
you want other people to get the same set.

---

## 1. Build the image

The dice server's sources (`Dockerfile`, `server.py`, `requirements.txt`,
`dice-server.yaml`) live in `./dice/`. Run the build from there:

```bash
cd dice
docker build -t dice-mcp:0.1.0 .
```

Sanity-check that it speaks the protocol before involving the gateway. A correct
stdio server responds to `initialize` on stdout and stays silent otherwise:

```bash
printf '%s\n' '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"probe","version":"0"}}}' \
  | docker run -i --rm dice-mcp:0.1.0
```

You should get a single JSON line back containing `serverInfo`. If you get
nothing, your stdout is buffered. If you get JSON *plus* log noise, something is
printing to stdout — move it to stderr.

## 2. Create a profile

```bash
docker mcp profile create --name tabletop
docker mcp profile list
```

## 3. Register the server into the profile

Servers are referenced by URI. The scheme tells the toolkit where the definition
comes from:

| URI form | Source |
|---|---|
| `catalog://<catalog-ref>/<server-id>` | A server from an OCI catalog |
| `docker://<image>:<tag>` | A Docker image directly |
| `file://<path>` | A local YAML/JSON definition |
| `https://<url>/v0/servers/<uuid>` | The MCP community registry |

For a local custom server, `file://` pointing at your YAML is the shortest path
(run from `./dice/`, or adjust the path if you're at the repo root):

```bash
docker mcp profile server add tabletop --server file://./dice-server.yaml
docker mcp profile server ls --filter profile=tabletop
```

`docker://dice-mcp:0.1.0` also works and skips the YAML entirely, but you lose
the description and title metadata that make the server legible in the Toolkit UI.

## 4. Run the gateway

```bash
docker mcp gateway run --profile tabletop
```

Leave it in the foreground the first time and watch the log as it starts your
container and enumerates tools. This is where a broken server announces itself.

## 5. Connect a client

For a client Docker knows about:

```bash
docker mcp client connect claude-desktop --profile tabletop
# or, for a repo-local config:
docker mcp client connect vscode --profile tabletop   # writes .vscode/mcp.json
echo ".vscode/mcp.json" >> .gitignore
```

For anything else, point it at the gateway over stdio yourself:

```json
{
  "mcpServers": {
    "MCP_DOCKER": {
      "command": "docker",
      "args": ["mcp", "gateway", "run", "--profile", "tabletop"]
    }
  }
}
```

Note the client only ever sees `MCP_DOCKER`. Adding a second server later means
one more `profile server add` and zero client changes.

---

## Distributing to a team: custom catalogs

A profile is yours; a catalog is what you hand to other people. Catalogs are OCI
artifacts, so they live in whatever registry you already run.

Build one from scratch containing exactly your servers plus whatever public ones
you approve:

```bash
docker mcp catalog create registry.example.com/mcp/tabletop:latest \
  --title "Tabletop Tools" \
  --server docker://registry.example.com/mcp/dice-mcp:0.1.0 \
  --server catalog://mcp/docker-mcp-catalog/sequentialthinking

docker mcp catalog show registry.example.com/mcp/tabletop:latest
docker mcp catalog push registry.example.com/mcp/tabletop:latest
```

Or fork Docker's 300+ server catalog and prune it down to an approved set:

```bash
docker mcp catalog tag mcp/docker-mcp-catalog registry.example.com/mcp/approved:latest
docker mcp catalog server ls registry.example.com/mcp/approved:latest
docker mcp catalog server remove registry.example.com/mcp/approved:latest --name <server>
docker mcp catalog server add registry.example.com/mcp/approved:latest \
  --server docker://registry.example.com/mcp/dice-mcp:0.1.0
docker mcp catalog push registry.example.com/mcp/approved:latest
```

Consumers pull it (`docker mcp catalog pull <ref>`) or import via
**MCP Toolkit → Catalog → Import catalog**.

The governance payoff shows up with Dynamic MCP: run the gateway with
`--catalog <ref>` and the agent's `mcp-find` discovery tool searches only your
catalog. A 20-server curated set is a materially different attack surface and a
materially different context budget than 300+.

---

## Failure modes worth knowing before you hit them

| Symptom | Cause |
|---|---|
| Gateway hangs, no tools listed | stdout buffered. `PYTHONUNBUFFERED=1` and `python -u`. |
| "Failed to parse message" / protocol errors | Something logged to stdout. Route all logging to stderr. |
| Server starts then immediately exits | Shell-form `ENTRYPOINT`. Use exec form so your process is PID 1. |
| Tools appear with no descriptions | Missing docstrings — the SDK derives tool descriptions from them, and the model reads those descriptions to decide when to call. Treat them as prompt surface, not comments. |
| Works locally, fails for teammates | `image:` in the YAML resolves only on your machine. Push to a registry. |
| `docker mcp` subcommand not found | Docker Desktop older than 4.62, or Toolkit not enabled. |
| Tool call fails only under the gateway | Your container assumes network or filesystem access the gateway doesn't grant. Declare what you need explicitly. |

## Iterating

The gateway caches the running container. After editing `dice/server.py`:

```bash
cd dice && docker build -t dice-mcp:0.1.0 . && docker mcp gateway run --profile tabletop
```

Restart the client too if it caches the tool list. Bumping the image tag on each
change (`0.1.1`, `0.1.2`) and updating the YAML is more keystrokes but removes a
whole class of "am I running the old code" confusion.
