# Handoff — DoraOS LINE Morning Page

Date: 2026-06-18
Owner context: shared handoff for Codex / Claude Code

## User Requests

Dora wants the DoraOS daily LINE Morning Page to:

- Fix remaining market / stock / news fetch failures.
- Use Dora's real stock holdings list.
- List all Calendar events.
- List all Google Tasks, not only the first few.
- Include at least three news articles.
- For each news article, include key point and summary.
- Keep the LINE layout readable on mobile.

## Do Not Expose

- Do not print or expose `LINE_CHANNEL_ACCESS_TOKEN`.
- Do not print or expose `LINE_TO_USER_ID`.
- Real holdings are stored in `DoraOS/.env`, which is gitignored.
- `.env.example` should only show field formats, not Dora's full holdings.

## Files Already Updated

- `DoraOS/operating/weather_market_sync.py`
- `DoraOS/operating/line_daily_brief.py`
- `DoraOS/.env`
- `DoraOS/.env.example`
- `DoraOS/obsidian/DoraOS/Resources/AI Briefs/2026-06-18 Claude Handoff - DoraOS LINE Morning Page.md`

## Current Stock Holdings

The full holdings are configured privately in `DoraOS/.env` as `DORAOS_MARKET_HOLDINGS`.

Holdings requested by Dora:

- 0050 元大台灣50
- 00931B 統一美債20年
- 1519 華城電機
- 2454 聯發科
- QQQ Nasdaq 100 ETF
- SPCX The Acquirers Fund
- VT Vanguard 全球股票
- VOO Vanguard S&P 500

`weather_market_sync.py` now parses the holdings and should show:

- latest price
- daily change
- holding quantity
- estimated market value
- rough P/L percentage

Fetch logic:

- Yahoo Finance chart endpoint first.
- TWSE `STOCK_DAY` fallback for `.TW` / `.TWO`.
- Individual ticker failures should show only that ticker as `報價未取得`; one ticker must not break the whole stock section.

## Current News Logic

`weather_market_sync.py` now uses Google News RSS.

Configured query env:

```text
DORAOS_NEWS_QUERIES=台股 AI 半導體;美股 AI 科技;台灣 金融市場
```

Expected output section:

```markdown
## News Radar | 今日新聞

### 1. ...
- 重點：...
- 摘要：...
- 來源：...
```

Need verify that live RSS returns at least three articles. If fewer than three are fetched, the script should clearly mark shortage and not invent news.

## Current LINE Logic

`line_daily_brief.py` now:

- Preserves line breaks.
- Splits long LINE output into multiple text messages.
- Lists all Calendar events.
- Lists all Google Tasks.
- Removes long Calendar URLs from message display.
- Renders news as title / 重點 / 摘要 / 來源.

Do not reintroduce `clip_text()` from `operating/common.py`; it flattens all line breaks and caused the ugly one-block LINE message.

## Verification Needed

Codex was blocked from final live refresh because escalated network execution hit a usage limit until around 18:19.

After network execution is available, run:

```bash
cd "/Users/youxinhua/Documents/New project/DoraOS"
./operating/.venv/bin/python -m py_compile operating/weather_market_sync.py operating/line_daily_brief.py
./operating/.venv/bin/python operating/weather_market_sync.py --env-file .env --verbose
./operating/.venv/bin/python operating/line_daily_brief.py --env-file .env --dry-run
```

If preview is good, send a forced LINE test:

```bash
./operating/.venv/bin/python operating/line_daily_brief.py --env-file .env --force
```

Expected success criteria:

- Stock section shows all 8 holdings or isolates only failed tickers.
- News section contains at least 3 articles with key point and summary.
- Calendar events are all listed.
- Google Tasks are all listed.
- LINE message preserves readable spacing and sections.

## Launchd

Existing job:

- `com.doraos.line-daily-brief`
- Daily at 08:05

Next scheduled run should pick up the updated code automatically.

## LINE AI Webhook / Notion Capture

New interactive webhook file:

- `DoraOS/operating/line_ai_webhook.py`
- docs: `DoraOS/docs/LINE_AI_WEBHOOK.md`

Supported LINE commands:

- `晨報`
- `信箱要處理哪些`
- `今天行程`
- `股票狀況`
- `存到 Notion：...`

Notion write behavior:

- Default chat remains read-only.
- Notion writes only happen with explicit capture prefixes such as `存到 Notion：`.
- General capture target is configured in `.env` as `LINE_AI_NOTION_PARENT_PAGE_ID`.
- Current target is the Notion Knowledge Vault parent page, so captures become child pages.
- `LINE_AI_NOTION_NOTES_DB_ID` is supported for database-backed capture, but leave it empty unless a database schema is confirmed.
- Recovery / diary-like text is blocked from the general Notion capture path and should go through Healing Journal instead.

Verification performed:

- `py_compile operating/line_ai_webhook.py` passed.
- Notion parent page read access passed via Notion API.
- Local dry-run showed the webhook creates a `POST /pages` payload with `parent.page_id` and two child blocks.
- A live Notion test page was not created because the safety reviewer rejected creating a real test page without Dora's explicit approval.
