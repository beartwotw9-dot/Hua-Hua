# DoraOS Automations

## Purpose

DoraOS automations let the system generate safe local outputs without requiring you to open Terminal manually.

There are now two layers:

- local `launchd` redundancy on your Mac
- Codex cloud-first autopilot for off-machine repair and continuity

These automations are intentionally conservative:

- local only
- no external mutations
- no memory auto-approval
- no email sending
- no task changes

## Current automations

### Daily brief

- command: `dora brief`
- schedule: weekdays at `08:00`
- output: `Resources/AI Briefs/`

### Weekly review

- command: `dora weekly`
- schedule: Sundays at `20:00`
- output: `Resources/AI Weekly Reviews/`

### Healing Journal sync

- command: `recovery/.venv/bin/python recovery/healing_journal_sync.py --env-file .env`
- schedule: every day at `07:15`
- output: `Areas/Mental Health/Healing Journal/`

### Notion daily backup

- command: `recovery/.venv/bin/python recovery/notion_backup_sync.py --env-file .env --config config/notion_backup.config.example.json`
- schedule: every day at `07:05`
- output: `Resources/Notion Sync/Daily Backups/YYYY-MM-DD/`
- output: `Resources/Notion Sync/Notion Backup Status.md`
- note: backs up accessible Notion pages and databases into Obsidian-safe markdown snapshots

### Weekly recovery review

- command: `recovery/.venv/bin/python recovery/weekly_review_generator.py --env-file .env`
- schedule: Sundays at `19:15`
- output: `Areas/Mental Health/Weekly Reviews/`

### Gmail digest sync

- command: `operating/.venv/bin/python operating/gmail_digest_sync.py --env-file .env`
- schedule: every day at `07:35`
- output: `Resources/Operating Feed/YYYY-MM-DD Gmail Digest.md`
- note: requires Google OAuth credentials, not just an API key

### Research digest sync

- command: `operating/.venv/bin/python operating/research_digest_sync.py --env-file .env --config config/research_digest.config.example.json`
- schedule: every day at `07:30`
- output: `Resources/Research Sources/Daily Digests/YYYY-MM-DD Research Digest.md`
- output: `Resources/Research Sources/Auto Papers/**/*.md`
- note: uses your configured research directions to fetch a small, focused reading radar and auto-create paper reading cards

### Zotero bridge sync

- command: `operating/.venv/bin/python operating/zotero_bridge_sync.py --env-file .env --config config/zotero_bridge.config.example.json`
- schedule: every day at `07:25`
- output: `Resources/Research Sources/Zotero/*.md`
- note: reads local Zotero metadata and turns relevant items into Chinese research notes while preserving your manual reading notes

### Calendar today sync

- command: `operating/.venv/bin/python operating/calendar_today_sync.py --env-file .env`
- schedule: every day at `07:40`
- output: `Resources/Operating Feed/YYYY-MM-DD Calendar View.md`
- note: requires Google OAuth credentials, not just an API key

### Official weather and market sync

- command: `operating/.venv/bin/python operating/weather_market_sync.py --env-file .env`
- schedule: before Operating feed compose
- output: `Resources/Operating Feed/YYYY-MM-DD Weather.md`
- output: `Resources/Operating Feed/YYYY-MM-DD Market News.md`
- note: weather uses CWA Open Data and requires `CWA_API_KEY`; market uses TWSE `MI_INDEX` and does not require a key

### Operating feed compose

- command: `operating/.venv/bin/python operating/operating_feed_compose.py --env-file .env`
- schedule: every day at `07:45`
- output: `Resources/Operating Feed/YYYY-MM-DD Operating Feed.md`

### Cloud Autopilot

- command: `dora autopilot --mode daily`
- primary output: `Resources/Operating Feed/Today Hub.md`
- role: run the cloud-first orchestrated morning pipeline
- role: backfill missing daily pages
- role: refresh Automation Status

### Rollover guard

- command: `operating/.venv/bin/python operating/ensure_daily_pages.py --env-file .env --days 7`
- schedule: every day at `00:10`
- role: ensure the new day gets a usable `Today.html` and dated pages even before fresh APIs succeed

### Morning verify

- command: `operating/.venv/bin/python operating/cloud_autopilot.py --env-file .env --mode daily`
- schedule: every day at `08:20`
- role: verify that today's files actually landed; if not, rerun the daily pipeline and refresh `Today.html`

### Missing-page backfill

- command: `operating/.venv/bin/python operating/ensure_daily_pages.py --env-file .env --days 7`
- role: repair recent missing daily pages
- note: used by Cloud Autopilot before the main daily pipeline

## Installation

From the repo root:

```bash
chmod +x scripts/install_dora_automations.sh scripts/uninstall_dora_automations.sh
./scripts/install_dora_automations.sh
```

This installs four `launchd` agents into:

```text
~/Library/LaunchAgents/
```

## Verification

```bash
launchctl list | grep doraos
tail -n 50 "/Users/youxinhua/Documents/New project/DoraOS/logs/dora_daily_brief.log"
tail -n 50 "/Users/youxinhua/Documents/New project/DoraOS/logs/dora_weekly_review.log"
tail -n 50 "/Users/youxinhua/Documents/New project/DoraOS/logs/dora_healing_journal_sync.log"
tail -n 50 "/Users/youxinhua/Documents/New project/DoraOS/logs/dora_weekly_recovery_review.log"
tail -n 50 "/Users/youxinhua/Documents/New project/DoraOS/logs/dora_gmail_digest_sync.log"
tail -n 50 "/Users/youxinhua/Documents/New project/DoraOS/logs/dora_calendar_today_sync.log"
tail -n 50 "/Users/youxinhua/Documents/New project/DoraOS/logs/dora_weather_market_sync.log"
tail -n 50 "/Users/youxinhua/Documents/New project/DoraOS/logs/dora_operating_feed_compose.log"
```

## Uninstall

```bash
./scripts/uninstall_dora_automations.sh
```

## Notes

- the Mac should be awake and logged in for scheduled runs to execute reliably
- Codex cloud-first automations reduce the risk of gaps when the Mac is off, but local-only sources can still be limited
- For Mac-off LINE delivery, use `.github/workflows/doraos-line-daily.yml`; it runs the cloud daily pipeline at 08:05 Asia/Taipei and pushes LINE after `CLOUD_AUTOPILOT_SEND_LINE_DAILY=true` is added in the temporary runner env.
- these automations generate files only in safe Obsidian folders
- if the machine is asleep, the run may be delayed or skipped depending on system state
- Healing Journal outputs stay in the private Mental Health area and are excluded from general brief ingestion by default
- Notion backup is read-only and may skip databases that are not shared with the current Notion integration
- Gmail and Calendar sync require Google OAuth client credentials and a local token file
- Research digest is pulled before Operating Feed so your morning page can show fresh paper highlights
- Zotero bridge runs before Research Digest so your own library notes can also surface in the morning page
- `Today Hub.md` is now the main daily page; dated files remain as source artifacts

## Why this is the right level

This gives you the practical benefit you want:

- open Obsidian later and see results

Without introducing the risk you do not want:

- uncontrolled autonomous behavior
