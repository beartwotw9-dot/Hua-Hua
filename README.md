# DoraOS Notion Dashboard

Minimal CLI setup for DoraOS, a Notion-based personal operating system for tasks, projects, healing journal, finance notes, morning briefs, weekly review, and TOEIC 475 → 750 practice.

## Setup

1. Create a Notion integration at <https://www.notion.so/my-integrations>.
2. Copy the integration token.
3. Share your target parent Notion page with the integration.
4. Create a local env file:

```bash
cp .env.example .env
```

5. Edit `.env`:

```bash
NOTION_API_KEY=secret_your_notion_integration_token_here
NOTION_PARENT_PAGE_ID=your_parent_page_id_here
```

6. Install dependencies and run:

```bash
npm install
npm run setup:doraos
```

## Required Environment Variables

- `NOTION_API_KEY`: Notion integration token. 不要提交到 Git。
- `NOTION_PARENT_PAGE_ID`: The page ID where DoraOS should be created.

## How To Find `NOTION_PARENT_PAGE_ID`

Open the parent page in Notion and copy its URL. The page ID is the long 32-character ID near the end of the URL.

Examples:

```text
https://www.notion.so/workspace/My-Page-0123456789abcdef0123456789abcdef
NOTION_PARENT_PAGE_ID=0123456789abcdef0123456789abcdef
```

If the URL has hyphens, either format usually works:

```text
01234567-89ab-cdef-0123-456789abcdef
```

## Safety Notes

- Do not hardcode secrets.
- Do not commit `.env`.
- The script reads `NOTION_API_KEY` and `NOTION_PARENT_PAGE_ID` only from environment variables or a local `.env` file.
- The script is idempotent where practical: it reuses an existing `DoraOS` child page, reuses databases with the same names, and avoids reseeding pages with the same title.
- Notion’s public API does not reliably create custom database views or native database templates. The script adds suggested view names and creates usable template pages for the journal, weekly review, and morning brief.

## What Gets Created

- `DoraOS` root page: `Life Operating System 人生作業系統`
- `✅ Tasks HQ 任務中樞`
- `🧠 Projects 專案系統`
- `📔 Healing Journal 療癒日記`
- `🔁 Weekly Review 每週回顧`
- `📈 Finance & Market Notes 金融市場筆記`
- `☀️ Morning Brief Archive 晨報封存`
- `📚 TOEIC Vocabulary Bank 多益單字庫`
- `📖 Reading Practice 閱讀練習`
- `🎯 TOEIC Progress 多益進度`

TOEIC content is intentionally small and steady: high-frequency vocabulary, short business reading, weekly review, and daily retrieval practice.

---

# Competition Archive

## How to add a new competition

1. Drop files into any subfolder under `04_競賽作品/`
2. Add one entry to `competitions/index.json`
3. Run: `node competitions/generate.js`
4. Done - new page is live at `competitions/[id].html`

Optional: add `meta.json` inside a competition source folder with:

```json
{ "reflection": "我在這場比賽學到..." }
```

To serve locally: `npx serve .` (or VS Code Live Server)

To auto-watch: `node competitions/watch.js`

## Current archive source

The portfolio now indexes:

- `04_競賽作品/`
- `05_進修學習/`

These are symlinks to the organized folder on Desktop, so the archive can display the files without duplicating or modifying the original materials.

Use `filePatterns` in `competitions/index.json` when multiple portfolio pages share the same source folder but should show different files.
