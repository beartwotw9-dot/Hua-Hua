# Operating Feed Sync

Read-only Gmail, Google Calendar, research digest, and Zotero bridge infrastructure for DoraOS.

## Purpose

This module writes daily operating context into Obsidian without auto-creating tasks.

## What it writes

- `Resources/Operating Feed/Today Hub.md`
- `Resources/Operating Feed/YYYY-MM-DD Gmail Digest.md`
- `Resources/Operating Feed/YYYY-MM-DD Calendar View.md`
- `Resources/Operating Feed/YYYY-MM-DD Operating Feed.md`
- `Resources/Research Sources/Daily Digests/YYYY-MM-DD Research Digest.md`
- `Resources/Research Sources/Auto Papers/**/*.md`
- `Resources/Research Sources/Zotero/*.md`

## Boundary

- Gmail and Calendar are context only
- tasks remain manual
- no email sending
- no calendar mutation
- research is a reading radar, not an automatic reading assignment
- auto paper capture creates reading cards, not mandatory tasks
- Zotero sync creates reading notes, not automatic task creation

## Setup

1. Install dependencies

```bash
cd "/Users/youxinhua/Documents/New project/DoraOS/operating"
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

2. Configure OAuth environment

Use the root `.env` or a dedicated env file.

Required:

- `OBSIDIAN_VAULT_PATH`
- `GOOGLE_OAUTH_CREDENTIALS_PATH`
- `GOOGLE_TOKEN_PATH`

Optional:

- `GMAIL_DIGEST_QUERY`
- `GOOGLE_CALENDAR_TIMEZONE`
- `ZOTERO_LIBRARY_PATH`
- `ZOTERO_BRIDGE_CONFIG_PATH`

3. First run

```bash
cd "/Users/youxinhua/Documents/New project/DoraOS/operating"
./.venv/bin/python gmail_digest_sync.py --env-file "/Users/youxinhua/Documents/New project/DoraOS/.env"
./.venv/bin/python calendar_today_sync.py --env-file "/Users/youxinhua/Documents/New project/DoraOS/.env"
./.venv/bin/python research_digest_sync.py --env-file "/Users/youxinhua/Documents/New project/DoraOS/.env" --config "/Users/youxinhua/Documents/New project/DoraOS/config/research_digest.config.example.json"
./.venv/bin/python zotero_bridge_sync.py --env-file "/Users/youxinhua/Documents/New project/DoraOS/.env" --config "/Users/youxinhua/Documents/New project/DoraOS/config/zotero_bridge.config.example.json"
./.venv/bin/python operating_feed_compose.py --env-file "/Users/youxinhua/Documents/New project/DoraOS/.env"
```

The first run opens a browser for Google OAuth and writes a local token file.

## Cloud-first orchestration

If you want the system to run as a single cloud-first pipeline, use:

```bash
cd "/Users/youxinhua/Documents/New project/DoraOS"
dora autopilot --mode daily --verbose
```

This orchestrates:

- missing-page backfill
- Notion backup
- research digest
- Gmail digest
- calendar view
- Operating Feed compose
- daily brief

And then refreshes:

- `Resources/Operating Feed/Today Hub.md`
- `Resources/Operating Feed/Automation Status.md`

## Notes

- A plain Google API key is not enough for Gmail or Calendar private data.
- Use Google OAuth client credentials from Google Cloud Console.
- Keep the token local and out of git.
- Edit `config/research_digest.config.example.json` to match your research directions.
- `research_digest_sync.py` can now both write the daily digest and create individual Chinese paper notes automatically.
- If the main arXiv API is rate-limited, the script falls back to configured arXiv RSS categories.
- `cloud_autopilot.py` is the preferred orchestrator for Codex cron runs and off-machine reliability.
- `ensure_daily_pages.py` backfills recent missing daily files so the morning surface is less likely to break.
- Edit `config/zotero_bridge.config.example.json` to match your local Zotero source and topic keywords.
- The Zotero bridge supports either a local `zotero.sqlite` path or a local JSON export path.
