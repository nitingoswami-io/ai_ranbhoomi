# Apple Notes MCP Server

An MCP server for **Apple Notes** on macOS, runnable from the **Docker MCP Toolkit**.

## Why the two-piece architecture

Apple Notes only speaks AppleScript / JXA — both are macOS-host-only. Docker Desktop runs containers inside a Linux VM, which cannot execute `osascript`. So the setup is split in two:

```
Claude / MCP client
      │  (stdio MCP)
      ▼
┌──────────────────────┐
│  Docker container    │   apple-notes-mcp  ← runs in Docker MCP Toolkit
│  (MCP server)        │
└──────────┬───────────┘
           │ HTTP + shared secret
           ▼ host.docker.internal:48213
┌──────────────────────┐
│  macOS host          │
│  notes-bridge (py)   │   LaunchAgent
│    → osascript       │
│      → Notes.app     │
└──────────────────────┘
```

## Tools exposed

| Tool           | What it does                                                |
| -------------- | ----------------------------------------------------------- |
| `list_notes`   | List notes (optionally scoped to a folder).                 |
| `get_note`     | Fetch one note's full body (HTML + plaintext).              |
| `create_note`  | Create a note in the default folder or a named folder.      |
| `update_note`  | Change title/body. `mode="append"` adds instead of replaces. |
| `delete_note`  | Move to Recently Deleted (30-day recoverable).              |
| `list_folders` | Enumerate folders.                                          |
| `search_notes` | Case-insensitive title + plaintext search.                  |

## Setup

### 1. Install the host bridge

```bash
cd bridge
./install-bridge.sh
```

That script:
- generates a shared secret into `bridge/.secret` (mode 600)
- writes a LaunchAgent plist to `~/Library/LaunchAgents/com.local.notes-bridge.plist`
- loads and starts the service (auto-starts at login)

Verify it's up:

```bash
curl -s http://127.0.0.1:48213/health
# {"ok": true, "notes": "reachable"}
```

**First run will trigger a macOS Automation permission prompt** ("`notes-bridge` wants to control Notes"). Approve it. If you miss the prompt or click Deny, grant it manually:

> System Settings → Privacy & Security → Automation → *(the Python binary shown in your plist)* → check **Notes**.

### 2. Build the MCP server image

```bash
cd server
docker build -t apple-notes-mcp:0.1.0 .
```

### 3. Register the secret with Docker MCP

```bash
docker mcp secret set apple-notes.bridge-secret "$(cat bridge/.secret)"
```

### 4. Register the server with Docker MCP Toolkit

```bash
docker mcp catalog import ./apple-notes-server.yaml
```

Or, to add to a specific profile:

```bash
docker mcp profile server add <profile> --server file://./apple-notes-server.yaml
```

## Verifying end-to-end

Quick smoke test from the host (bypassing Docker) to prove the bridge works:

```bash
SECRET=$(cat bridge/.secret)
curl -s -X POST http://127.0.0.1:48213/call \
  -H "Content-Type: application/json" \
  -H "X-Bridge-Secret: $SECRET" \
  -d '{"action":"list_folders","params":{}}' | python3 -m json.tool
```

Then, in your MCP client, call `list_folders` — you should see the same folders.

## Configuration

Env vars on the container (set via the catalog YAML or `docker mcp` overrides):

| Var                     | Default                                | Notes                          |
| ----------------------- | -------------------------------------- | ------------------------------ |
| `NOTES_BRIDGE_URL`      | `http://host.docker.internal:48213`    | Point elsewhere for testing.   |
| `NOTES_BRIDGE_SECRET`   | *(unset)*                              | Must match the bridge's.       |
| `NOTES_BRIDGE_TIMEOUT`  | `60`                                   | Seconds per osascript call.    |

Env vars on the bridge (set inside the plist, edit `install-bridge.sh` to change):

| Var                     | Default    | Notes                                                |
| ----------------------- | ---------- | ---------------------------------------------------- |
| `NOTES_BRIDGE_HOST`     | `0.0.0.0`  | Must not be 127.0.0.1 — `host.docker.internal` needs it. |
| `NOTES_BRIDGE_PORT`     | `48213`    | Any unused high port works.                           |
| `NOTES_BRIDGE_SECRET`   | *(unset)*  | Empty = auth disabled (**do not do this**).           |
| `NOTES_BRIDGE_TIMEOUT`  | `60`       | Seconds per osascript call.                           |

## Uninstalling

```bash
cd bridge && ./uninstall-bridge.sh
docker mcp secret rm apple-notes.bridge-secret
docker rmi apple-notes-mcp:0.1.0
```

## Troubleshooting

**`{"ok":true,"notes":"not-reachable"}` from `/health`** — Notes.app is being blocked by Automation permissions. Open a note in Notes.app once, then re-run the health check; macOS will prompt.

**Container can't reach `host.docker.internal`** — Docker Desktop must be running. The bridge must be bound to `0.0.0.0`, not `127.0.0.1` (this is the default).

**Empty `search_notes` results** — search only reads `plaintext`, which is empty for notes containing only attachments/checklists.

**Bridge logs** — `~/Library/Logs/notes-bridge.log` and `~/Library/Logs/notes-bridge.err`.

## Security notes

- The bridge listens on `0.0.0.0` so Docker's VM can reach it. Anyone else on your machine (or LAN, if your firewall is permissive) could reach the port too — the shared secret is the only gate. Keep `.secret` readable only by your user, and rotate by deleting `.secret` and re-running `install-bridge.sh`.
- The container image runs as an unprivileged user and has no host mounts. All I/O flows through the authenticated HTTP endpoint.
- `delete_note` moves notes to Recently Deleted, not a hard delete.
