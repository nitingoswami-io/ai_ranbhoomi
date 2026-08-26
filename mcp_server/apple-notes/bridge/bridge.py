"""Host-side HTTP bridge that runs osascript against Apple Notes.

Runs on macOS (must — Notes.app + osascript are host-only).
The Docker MCP server container calls this over HTTP via
host.docker.internal.

Wire format:
    POST /call        {"action": "list_notes", "params": {...}}
                   -> {"ok": true,  "data": ...}
                   -> {"ok": false, "error": "..."}

    GET  /health   -> {"ok": true, "notes": "reachable" | "not-reachable"}

The port is bound on 0.0.0.0 so Docker's VM can reach it via
host.docker.internal. Access is gated by a shared secret in the
X-Bridge-Secret header — anyone on localhost could still see the port,
so treat the secret as required.
"""

import json
import os
import subprocess
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent / "notes.js"
SECRET = os.environ.get("NOTES_BRIDGE_SECRET", "")
PORT = int(os.environ.get("NOTES_BRIDGE_PORT", "48213"))
# 0.0.0.0 is intentional: Docker Desktop's VM reaches the host via
# host.docker.internal, which requires the listener to bind beyond 127.0.0.1.
HOST = os.environ.get("NOTES_BRIDGE_HOST", "0.0.0.0")
TIMEOUT = int(os.environ.get("NOTES_BRIDGE_TIMEOUT", "60"))


def call_osascript(action: str, params: dict) -> dict:
    cmd = [
        "osascript",
        "-l", "JavaScript",
        str(SCRIPT),
        action,
        json.dumps(params or {}),
    ]
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=TIMEOUT
        )
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": f"osascript timed out after {TIMEOUT}s"}
    except FileNotFoundError:
        return {"ok": False, "error": "osascript not found — is this running on macOS?"}

    if result.returncode != 0:
        err = result.stderr.strip() or f"osascript exited {result.returncode}"
        return {"ok": False, "error": err}
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        # osascript emitted something we can't parse — surface first 200 chars
        # for debugging without dumping a huge payload back to the client.
        return {
            "ok": False,
            "error": f"non-JSON output from osascript: {result.stdout[:200]!r}",
        }


class BridgeHandler(BaseHTTPRequestHandler):
    def _write_json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _auth_ok(self) -> bool:
        # Empty SECRET means auth is disabled — allowed for local testing only.
        if not SECRET:
            return True
        return self.headers.get("X-Bridge-Secret") == SECRET

    def do_GET(self):
        if self.path == "/health":
            probe = call_osascript("ping", {})
            self._write_json(
                200,
                {"ok": True, "notes": "reachable" if probe.get("ok") else "not-reachable"},
            )
            return
        self._write_json(404, {"ok": False, "error": "not found"})

    def do_POST(self):
        if self.path != "/call":
            self._write_json(404, {"ok": False, "error": "not found"})
            return
        if not self._auth_ok():
            self._write_json(401, {"ok": False, "error": "unauthorized"})
            return

        length = int(self.headers.get("Content-Length", "0") or "0")
        if length <= 0:
            self._write_json(400, {"ok": False, "error": "empty body"})
            return
        try:
            payload = json.loads(self.rfile.read(length))
        except json.JSONDecodeError:
            self._write_json(400, {"ok": False, "error": "invalid JSON body"})
            return

        action = payload.get("action")
        params = payload.get("params") or {}
        if not action:
            self._write_json(400, {"ok": False, "error": "missing 'action'"})
            return

        result = call_osascript(action, params)
        status = 200 if result.get("ok") else 500
        self._write_json(status, result)

    def log_message(self, format, *args):
        sys.stderr.write("[notes-bridge] " + (format % args) + "\n")


def main() -> None:
    if not SCRIPT.exists():
        sys.stderr.write(f"[notes-bridge] fatal: {SCRIPT} not found\n")
        sys.exit(1)
    if not SECRET:
        sys.stderr.write(
            "[notes-bridge] WARNING: NOTES_BRIDGE_SECRET is empty — auth disabled\n"
        )
    server = ThreadingHTTPServer((HOST, PORT), BridgeHandler)
    sys.stderr.write(f"[notes-bridge] listening on {HOST}:{PORT}\n")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.server_close()


if __name__ == "__main__":
    main()
