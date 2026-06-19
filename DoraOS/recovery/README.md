# Healing Journal Sync System

Local DoraOS recovery infrastructure for syncing a Notion Healing Journal database into Obsidian and generating weekly recovery reviews.

## What this includes

- `healing_journal_sync.py`
  - reads the Notion Healing Journal database
  - maps structured properties into frontmatter
  - converts Notion page content into markdown
  - writes daily journal files into Obsidian
  - uses duplicate protection by date
  - preserves manual notes when updating existing files
  - keeps incremental sync state in a local cache

- `weekly_review_generator.py`
  - scans the last 7 days of synced journal files
  - analyzes trends for mood, anxiety, sleep, brain fog, and risk
  - extracts repeated stressors and positive signals
  - writes a weekly recovery review note into Obsidian

- `common.py`
  - shared env loading, logging, state, and markdown region helpers

## Folder structure

```text
DoraOS/
├── recovery/
│   ├── .env.example
│   ├── README.md
│   ├── common.py
│   ├── healing_journal_sync.py
│   ├── requirements.txt
│   ├── state/
│   │   └── healing_journal_sync_state.json
│   └── weekly_review_generator.py
├── logs/
│   ├── healing_journal_sync.log
│   └── weekly_recovery_review.log
└── obsidian/
    └── DoraOS/
        └── Areas/
            └── Mental Health/
                ├── Healing Journal/
                │   └── YYYY-MM-DD.md
                └── Weekly Reviews/
                    └── YYYY-WW Weekly Recovery Review.md
```

## Obsidian output paths

- Daily journals:
  - `Areas/Mental Health/Healing Journal/YYYY-MM-DD.md`

- Weekly reviews:
  - `Areas/Mental Health/Weekly Reviews/YYYY-WW Weekly Recovery Review.md`

## Setup

### 1. Install dependencies

```bash
cd "/Users/youxinhua/Documents/New project/DoraOS/recovery"
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 2. Configure environment variables

You can either:

- add these values to the root DoraOS `.env`
- or create a dedicated env file and pass `--env-file`

Required variables:

- `NOTION_API_KEY`
- `NOTION_HEALING_DB_ID`
- `OBSIDIAN_VAULT_PATH`

Example:

```bash
cp .env.example .env.recovery
```

### 3. Share the Healing Journal database with your Notion integration

Make sure the Notion integration behind `NOTION_API_KEY` has access to:

- `📔 Healing Journal 療癒日記`

## Run

### Sync healing journals from Notion into Obsidian

Using the root DoraOS `.env`:

```bash
cd "/Users/youxinhua/Documents/New project/DoraOS/recovery"
python3 healing_journal_sync.py
```

Using a dedicated env file:

```bash
python3 healing_journal_sync.py --env-file .env.recovery
```

Preview only:

```bash
python3 healing_journal_sync.py --dry-run --verbose
```

Force a full rebuild:

```bash
python3 healing_journal_sync.py --force --verbose
```

### Generate weekly recovery review

```bash
cd "/Users/youxinhua/Documents/New project/DoraOS/recovery"
python3 weekly_review_generator.py
```

Preview only:

```bash
python3 weekly_review_generator.py --dry-run --verbose
```

Custom window ending on a given date:

```bash
python3 weekly_review_generator.py --end-date 2026-05-18 --days 7
```

## Cron example

macOS usually works better with `launchd`, but here is a cron-style example if you want one.

Daily sync at 07:30:

```cron
30 7 * * * cd /Users/youxinhua/Documents/New\ project/DoraOS/recovery && /Users/youxinhua/Documents/New\ project/DoraOS/recovery/.venv/bin/python healing_journal_sync.py >> /Users/youxinhua/Documents/New\ project/DoraOS/logs/healing_journal_sync.log 2>&1
```

Weekly review every Sunday at 19:00:

```cron
0 19 * * 0 cd /Users/youxinhua/Documents/New\ project/DoraOS/recovery && /Users/youxinhua/Documents/New\ project/DoraOS/recovery/.venv/bin/python weekly_review_generator.py >> /Users/youxinhua/Documents/New\ project/DoraOS/logs/weekly_recovery_review.log 2>&1
```

## Design notes

- Notion remains the structured capture database
- Obsidian becomes the durable local journal archive
- daily sync updates generated content but keeps manual annotations
- duplicate protection groups entries by journal date
- incremental sync uses a local date signature cache

## Future roadmap

This design is ready to extend with:

- sentiment analysis
- embeddings over journal history
- AI weekly summarization
- dashboard trends
- longitudinal recovery tracking
- crisis-signal alerts with human approval gates

## Important privacy boundary

The synced journal folder is meant to stay outside the default Daily Brief ingestion path.

That keeps healing notes local and retrievable without turning them into general AI operating context by default.
