# Migrating to a Mac mini (or any fresh Mac)

Goal: replicate the daily-brief pipeline (and any MCP servers you use from Claude Desktop) on a fresh Mac. `scripts/migrate-mac.sh` handles the mechanical parts; this doc covers what macOS won't let a script do and what you'd want to configure on a headless always-on machine.

## Before you run the script

On the new Mac:

1. **Xcode Command Line Tools** — provides `git` and a working compiler:
   ```bash
   xcode-select --install
   ```
2. **Sign in to iCloud** and open Notes.app once so its data store initializes. If notes should sync from your main Mac, wait for that sync to complete before continuing.
3. **Have your Anthropic API key ready** (`sk-ant-...`). The script will prompt if you don't set `ANTHROPIC_API_KEY` in the environment.
4. **Clone the repo** to a stable location. The script uses the repo's own path in the LaunchAgent plists, so don't move the checkout after running it.
   ```bash
   git clone git@github.com:nitingoswami-io/ai_ranbhoomi.git ~/ai_ranbhoomi
   ```

## Run the script

```bash
cd ~/ai_ranbhoomi
scripts/migrate-mac.sh
```

What it does:

1. Installs `uv` if missing.
2. `uv sync`s every package (`mcp_server/trend-radar-mcp`, `pipeline/writer`, `pipeline/renderer`, `pipeline/delivery`, and any others with a `pyproject.toml`).
3. Creates `pipeline/.env` from the template, prompts for `ANTHROPIC_API_KEY`.
4. Runs `install-bridge.sh` — writes a shared secret to `mcp_server/apple-notes/bridge/.secret`, installs `~/Library/LaunchAgents/com.local.notes-bridge.plist`, starts the bridge.
5. Installs `~/Library/LaunchAgents/com.nitin.ai-ranbhoomi.daily.plist` — fires at 21:00 daily.
6. Runs `install-bridge.sh --doctor` and reports overall status.

The script is idempotent — re-run it any time you pull new code or think something has drifted.

## Two things you have to do by hand

### 1. Grant Automation permission to the bridge

The first real bridge call (any osascript that touches Notes) will trigger a macOS permission dialog:

> "python3" wants to control "Notes.app". Allow / Deny.

Click **Allow**. If you missed the dialog or clicked deny, fix it in:

**System Settings → Privacy & Security → Automation** → find the Python interpreter the bridge uses (the script prefers `~/.local/bin/python3.12` from uv, then Homebrew) → toggle **Notes** on.

Sanity-check:
```bash
curl -s http://localhost:48213/health
# want: {"ok": true, "notes": "reachable"}
```

If `notes` says `not-reachable`, that's this permission missing. Nothing else will make the pipeline work.

### 2. Create the "Daily Brief" folder in Notes.app

Open Notes → File → New Folder → name it exactly **Daily Brief** (matches the default `APPLE_NOTES_FOLDER` in `pipeline/.env`; change both if you prefer a different name). Without this, delivery fails with `folder not found: Daily Brief`.

## Recommended for a headless Mac mini

Notes.app + osascript need an active user session — meaning a logged-in user, even if there's no monitor attached.

- **Auto-login** — System Settings → Users & Groups → set your user to auto-login on boot. This is the single most important setting; without it, the pipeline can't reach Notes after a reboot.
- **Disable "Prevent computer from sleeping automatically when the display is off"** — actually, on a Mac mini this is usually off by default, but double-check: System Settings → Displays / Lock Screen. You want the Mac awake at 21:00. Alternatively, use `pmset` to schedule wake.
- **Screen Sharing** — if you'll manage the Mac mini remotely, enable Screen Sharing (or SSH). System Settings → General → Sharing.
- **iCloud sync** — this is what carries the "Daily Brief" notes to your phone. Make sure Notes is enabled in iCloud settings on both the Mac mini and your phone.
- **App Nap** — Notes.app may nap when unused; the bridge calls should wake it, but if you see occasional `not-reachable` failures, right-click Notes.app in Finder → Get Info → check "Prevent App Nap". Or use `defaults write` for a global disable.

## Verify

```bash
scripts/migrate-mac.sh --doctor
```

This runs the full validation — venvs, .env, bridge health + doctor, LaunchAgent state, Notes folder presence — without touching anything. Use it whenever "did this drift?" comes up.

For a real dry-run of the pipeline (fetches for real, no LLM cost, no notes created):
```bash
DRY_RUN=1 pipeline/run_daily.sh
```

Then a real run:
```bash
pipeline/run_daily.sh
```

If both work, the launchd job will do the same thing at 21:00.

## Uninstall

```bash
# Remove daily-runner
launchctl bootout "gui/$(id -u)/com.nitin.ai-ranbhoomi.daily"
rm ~/Library/LaunchAgents/com.nitin.ai-ranbhoomi.daily.plist

# Remove notes-bridge
mcp_server/apple-notes/bridge/uninstall-bridge.sh

# Optional — clear the trend-radar + delivery ledgers
rm -f pipeline/data/*.db
```

Leaves the repo, venvs, and .env alone — remove those manually if you're wiping.
