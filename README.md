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

---

# Chandra OCR

Chandra is the document ingestion layer for the AI research workflow in this repo:

```text
Raw PDF / Image
-> Chandra OCR
-> Markdown / HTML / JSON
-> Research notes / RAG / Notion-ready summaries
```

Use Chandra OCR when you need stronger document intelligence than plain text extraction:

- complex PDFs
- scanned forms
- tables
- handwriting
- math-heavy documents
- RAG preprocessing

Chandra converts PDFs and images into structured Markdown, HTML, and JSON while preserving layout details. In this repo, outputs should go under `outputs/chandra/<source_file_name>/`.

The reusable ingestion module is [scripts/chandra_ingest.py](/Users/youxinhua/Documents/New%20project/scripts/chandra_ingest.py). It runs Chandra and writes a small `ingestion_manifest.json` inside each document output folder so downstream AI workflows can reliably discover the generated artifacts.

## Install

Choose the install that matches your runtime:

```bash
# HuggingFace local inference (recommended for simple local use)
pip install "chandra-ocr[hf]"

# Basic install for vLLM-backed usage
pip install chandra-ocr

# Optional app UI
pip install "chandra-ocr[app]"
```

Environment notes:

- Python `>=3.10` is required by `chandra-ocr`.
- `--method hf` runs locally and is the easiest CLI-first option.
- `--method vllm` expects a running vLLM server. You can start the packaged helper with `chandra_vllm`.

## Output Convention

Send Chandra output to `./outputs/chandra`.

For each source file, Chandra creates a subfolder such as:

```text
outputs/chandra/invoice.pdf/
├── invoice.pdf.md
├── invoice.pdf.html
├── invoice.pdf_metadata.json
├── ingestion_manifest.json
└── extracted images...
```

That gives you Markdown, HTML, metadata JSON, extracted images when available, plus a manifest that points downstream tools to the right files.

The root output folder also gets:

```text
outputs/chandra/ingestion_index.json
```

This makes it easier to pass a batch of OCR results into research pipelines or RAG indexing jobs.

## Run A Single File

Use the repo wrapper:

```bash
# Default method is hf
npm run ocr:chandra -- ./path/to/input.pdf

# Explicit hf
npm run ocr:chandra -- ./path/to/input.pdf --method hf

# vLLM
npm run ocr:chandra -- ./path/to/input.pdf --method vllm
```

Or call the ingestion module directly:

```bash
python3 scripts/chandra_ingest.py ./path/to/input.pdf --method hf
```

You can still call Chandra directly if you only want raw OCR output:

```bash
chandra input.pdf ./outputs/chandra --method hf
chandra input.pdf ./outputs/chandra --method vllm
```

## Run A Folder

Process an entire directory of PDFs or images:

```bash
npm run ocr:chandra -- ./documents --method hf

# Direct CLI equivalent
chandra ./documents ./outputs/chandra --method hf

# Ingestion module
python3 scripts/chandra_ingest.py ./documents --method hf
```

## Method Choice: `hf` vs `vllm`

- Use `hf` for the lightest local workflow and simplest setup on one machine.
- Use `vllm` when you already have GPU inference infrastructure or want a separate inference server for batch processing.
- Start the helper server with `chandra_vllm`, then run `chandra ... --method vllm`.

## Research Workflow Notes

This setup is intended to be the first stage of a document intelligence pipeline:

1. Put raw PDFs, scans, or images into a source folder.
2. Run the Chandra ingestion script to create structured outputs.
3. Feed the generated Markdown or HTML into AI research, note extraction, RAG chunking, or Notion-ready summary workflows.
4. Use `*_metadata.json` and `ingestion_manifest.json` when you need file-level provenance or artifact discovery.

## Wrapper Script

This repo includes a small wrapper at [scripts/chandra_ocr.sh](/Users/youxinhua/Documents/New%20project/scripts/chandra_ocr.sh) to keep commands consistent.

Examples:

```bash
# Single file
bash scripts/chandra_ocr.sh input.pdf

# Folder
bash scripts/chandra_ocr.sh ./documents --method hf

# Pass through extra Chandra flags such as page ranges
bash scripts/chandra_ocr.sh input.pdf --method hf --page-range 1-5

# Rebuild manifests without rerunning OCR
python3 scripts/chandra_ingest.py ./documents --skip-ocr
```
