# Daily run setup

This wires the four pipeline stages into a single command that runs every day at 21:00 via **launchd** (macOS's scheduler — chosen over cron because it catches up on wake if the Mac is asleep at the scheduled time).

## What runs

```
mcp_server/trend-radar-mcp/scripts/fetch.py     (new)
     → pipeline/writer      (existing)
     → pipeline/renderer    (existing)
     → pipeline/delivery    (existing)
```

Each stage reads JSON on stdin, writes JSON on stdout. `run_daily.sh` is the shell orchestrator that pipes them together and writes a timestamped log to `pipeline/logs/`.

## One-time setup

1. **Sync all four packages** (once per checkout):
   ```bash
   (cd mcp_server/trend-radar-mcp && uv sync)
   (cd pipeline/writer            && uv sync)
   (cd pipeline/renderer          && uv sync)
   (cd pipeline/delivery          && uv sync --extra dev)
   ```

2. **Install the Apple Notes bridge** (once per Mac):
   ```bash
   cd mcp_server/apple-notes/bridge && ./install-bridge.sh
   ```
   Generates `mcp_server/apple-notes/bridge/.secret` — `run_daily.sh` reads it automatically.

3. **Create `pipeline/.env`** with your API key:
   ```bash
   cp pipeline/.env.example pipeline/.env
   # then edit and set ANTHROPIC_API_KEY=sk-ant-...
   ```

4. **Install the launchd job** (schedules 21:00 daily):
   ```bash
   cd pipeline/launchd
   sed "s|__REPO__|$(cd ../.. && pwd)|g" \
     com.nitin.ai-ranbhoomi.daily.plist.template \
     > ~/Library/LaunchAgents/com.nitin.ai-ranbhoomi.daily.plist

   launchctl bootstrap "gui/$(id -u)" \
     ~/Library/LaunchAgents/com.nitin.ai-ranbhoomi.daily.plist
   ```

5. **Verify it's registered**:
   ```bash
   launchctl list | grep ai-ranbhoomi
   ```

## Testing before you trust it

**Dry-run the whole pipeline** (no LLM cost, no notes created):
```bash
DRY_RUN=1 pipeline/run_daily.sh
```
Check `pipeline/logs/<timestamp>.log`. You should see fetch output → writer stub drafts → renderer output → delivery report with `status=dry-run`.

**Real run, on demand** (creates actual notes — do it once to prove the end-to-end works):
```bash
pipeline/run_daily.sh
```

**Trigger the launchd job as if it were 21:00**:
```bash
launchctl kickstart -k "gui/$(id -u)/com.nitin.ai-ranbhoomi.daily"
tail -f pipeline/logs/launchd.stderr.log
```

## Tuning

Edit `pipeline/.env` to override any of:

| Env var | Default | Effect |
|---|---|---|
| `LOOKBACK_HOURS` | 24 | Trend-radar window |
| `TOPIC_LIMIT` | 5 | Max non-suppressed topics per run |
| `APPLE_NOTES_FOLDER` | `Daily Brief` | Target folder in Notes |
| `WRITER_MODEL` | `anthropic:claude-sonnet-4-6` | Override the writer's LLM |
| `TREND_RADAR_DB` | `pipeline/data/trend_radar.db` | Path to the novelty ledger |

Changing the schedule: edit `Hour`/`Minute` in the plist, then `launchctl bootout` + `bootstrap` to reload.

## Uninstall

```bash
launchctl bootout "gui/$(id -u)/com.nitin.ai-ranbhoomi.daily"
rm ~/Library/LaunchAgents/com.nitin.ai-ranbhoomi.daily.plist
```

## Troubleshooting

- **Job didn't run at 21:00** → `launchctl list | grep ai-ranbhoomi`. If the exit code column shows non-zero, look at `pipeline/logs/launchd.stderr.log` first (captures env/startup errors), then the latest timestamped log (captures pipeline output).
- **Writer fails with "no API key"** → `pipeline/.env` isn't being sourced, or `ANTHROPIC_API_KEY` isn't set in it. `run_daily.sh` sources it early; check the log's first "=== …daily run ===" block.
- **Delivery fails with 401** → bridge secret mismatch. `curl -s http://localhost:48213/health` should say `reachable`; check `mcp_server/apple-notes/bridge/.secret` matches what the bridge process is using.
- **Delivery says "skipped: already delivered"** → that's the `pipeline/data/delivery.db` ledger doing its job. Delete the file to re-deliver the same run, or bump `LOOKBACK_HOURS` to pull a fresh trending run.
- **No topics after novelty gate** → likely covered everything recently. Check with `sqlite3 pipeline/data/trend_radar.db 'SELECT * FROM covered ORDER BY covered_on DESC LIMIT 20;'`.

## What's NOT wired yet

- **`trend-radar.mark_covered` loop-close.** After delivery succeeds, nothing marks the topics as covered — so the next run may re-surface them. Two ways to close it: subprocess-invoke `trend_radar` and call `mark_covered`, or write directly to the SQLite ledger. Not built; see `pipeline/delivery/README.md`.
- **Notion delivery.** Only `apple-notes` today.
