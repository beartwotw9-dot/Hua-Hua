# DoraOS LINE AI Webhook

Purpose: upgrade DoraOS from one-way LINE push into an interactive LINE assistant.

## What It Does

Endpoint:

```text
POST /line/webhook
GET /health
```

The webhook:

- verifies LINE signature when `LINE_CHANNEL_SECRET` is configured
- reads today's DoraOS context:
  - Today Hub
  - Calendar
  - Google Tasks
  - Gmail Digest
  - Market News
  - Daily Brief
- answers in mobile-friendly Traditional Chinese
- uses OpenRouter / OpenAI-compatible chat completions
- falls back to simple local replies if the AI provider is unavailable
- can save an explicit LINE capture into Notion when enabled

Default behavior is read-only. It does not send email, mutate Calendar, change
Tasks, or approve memory. Notion writes require both an environment flag and an
explicit LINE command.

## Required Environment

```text
LINE_CHANNEL_ACCESS_TOKEN=
LINE_CHANNEL_SECRET=
OPENROUTER_API_KEY=
OPENROUTER_BASE_URL=https://openrouter.ai/api/v1
LINE_AI_MODEL=openai/gpt-4.1-mini
LINE_AI_ALLOW_EXTERNAL_CONTEXT=false
LINE_AI_NOTION_WRITES_ENABLED=false
LINE_AI_NOTION_NOTES_DB_ID=
LINE_AI_NOTION_PARENT_PAGE_ID=
OBSIDIAN_VAULT_PATH=/path/to/DoraOS/obsidian/DoraOS
```

`LINE_CHANNEL_SECRET` comes from LINE Developers → Basic settings → Channel secret.

Set `LINE_AI_ALLOW_EXTERNAL_CONTEXT=true` only after Dora explicitly agrees
that today's DoraOS context, including compressed Gmail / Calendar / Tasks, may
be sent to the configured external AI provider.

Set `LINE_AI_NOTION_WRITES_ENABLED=true` only after confirming the target
database schema. `LINE_AI_NOTION_NOTES_DB_ID` should point to the Notion database
used for LINE captures / inbox notes, not Healing Journal.

If the target is a normal Notion page rather than a database, leave
`LINE_AI_NOTION_NOTES_DB_ID` empty and set `LINE_AI_NOTION_PARENT_PAGE_ID`.
The webhook will create child pages under that page.

## Local Run

```bash
cd "/Users/youxinhua/Documents/New project/DoraOS"
./operating/.venv/bin/python operating/line_ai_webhook.py --env-file .env --port 8789
```

Health check:

```bash
curl http://127.0.0.1:8789/health
```

## LINE Developers

Set webhook URL to:

```text
https://YOUR_DOMAIN/line/webhook
```

Current user-facing target from prior setup:

```text
https://huaauh.zeabur.app/line/webhook
```

That Zeabur service must deploy this webhook or proxy to it.

## Runs When Mac Is Off

Two different pieces need cloud hosting:

- Daily push: GitHub Actions can run `operating/cloud_autopilot.py` every day and
  then push the LINE Morning Page.
- Interactive replies: Zeabur or another always-on web service must run
  `operating/line_ai_webhook.py` so LINE webhook requests have a live endpoint.

GitHub Actions workflow:

```text
.github/workflows/doraos-line-daily.yml
```

Schedule:

```text
08:05 Asia/Taipei
```

Required GitHub secrets:

```text
DORAOS_ENV_B64
DORAOS_GOOGLE_CREDENTIALS_B64
DORAOS_GOOGLE_TOKEN_B64
```

Create them from the local DoraOS files without printing the values:

```bash
cd "/Users/youxinhua/Documents/New project/DoraOS"
base64 -i .env | pbcopy
```

Paste as `DORAOS_ENV_B64`, then repeat for:

```bash
base64 -i operating/credentials.json | pbcopy
```

Paste as `DORAOS_GOOGLE_CREDENTIALS_B64`.

```bash
base64 -i operating/google-token.json | pbcopy
```

Paste as `DORAOS_GOOGLE_TOKEN_B64`.

Use GitHub repository Settings → Secrets and variables → Actions → New
repository secret.

The workflow also overrides `OBSIDIAN_VAULT_PATH`,
`GOOGLE_OAUTH_CREDENTIALS_PATH`, and `GOOGLE_TOKEN_PATH` inside the temporary
runner `.env`, so local Mac paths do not leak into GitHub Actions. It appends
`CLOUD_AUTOPILOT_SEND_LINE_DAILY=true` only inside the runner, so local launchd
does not accidentally duplicate the LINE daily push.

## Useful LINE Messages

- `晨報`
- `今天先做什麼`
- `信箱要處理哪些`
- `今天行程`
- `股票狀況`
- `幫我整理今天待辦`
- `存到 Notion：這裡放要捕捉的內容`

## Notion Capture Rules

The webhook only writes Notion when the message starts with one of these
explicit prefixes:

```text
存到 Notion：
記到 Notion：
notion:
```

When writing to a database, it retrieves the target database schema, finds the
live title property, and only fills optional fields whose names and types match:

| Local field | Notion property candidates | Type |
| --- | --- | --- |
| title | `標題`, `名稱`, `Name`, `Title` | `title` |
| content | `內容`, `Content`, `備註`, `Notes`, `摘要` | `rich_text` |
| source | `來源`, `Source` | `rich_text`, `select`, `multi_select` |
| type | `類型`, `Type`, `分類`, `Category` | `rich_text`, `select`, `multi_select` |
| status | `狀態`, `Status` | `status`, `select`, `rich_text` |
| date | `日期`, `Date`, `Created`, `建立日期` | `date` |

Diary, counseling, nightmare, anxiety, burnout, psychiatry, and recovery-like
messages are not written into the general Notion capture database. Route those
through the Healing Journal workflow.

## Safety

- Never expose LINE tokens or user id.
- Keep mental-health / diary data out unless Dora explicitly sends it in LINE.
- Treat Gmail as context, not automatic commitments.
- External mutations require explicit commands and enabled environment flags.
