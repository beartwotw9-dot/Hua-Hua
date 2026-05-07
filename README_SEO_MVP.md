# SEO Automation MVP

> A 1-day, demo-ready SEO content pipeline built on Google Sheets + Apps Script,
> with an importable n8n workflow as the "next step" path.

**The 60-second pitch.** SEO content teams spend hours doing the same loop:
pick a keyword → look at Google's top 10 → notice which entities/topics keep
showing up → brief a writer → generate an article + image prompts → log
quality notes. This MVP automates that loop end-to-end as a Google Sheet you
can hand to a non-technical teammate. Drop a keyword in, click Run, get back
10 SERP rows with extracted entities, then an article + 3 image prompts +
quality notes — all written back to the sheet. **`SERP_MOCK_MODE=true` and
`SEO_MOCK_MODE=true` by default, so the entire demo runs with zero API keys.**

## What this demonstrates

| Skill | Where to look |
| --- | --- |
| Pragmatic system design (Sheet-as-DB) | This README + both `.gs` files |
| Google Apps Script (production-shape: status machine, error rows, header validation) | `apps-script/` |
| LLM prompt design with parseable output format | `seo_content_generator.gs` `buildSeoPrompt_` |
| Provider abstraction (SerpAPI/ValueSERP, OpenAI/Anthropic) | both `.gs` files |
| Mock-first methodology (offline demo path) | `getMockSerpResults_`, `getMockSeoResponse_` |
| Workflow tooling (n8n equivalent path) | `n8n/seo_content_generator.workflow.json` |

## Architecture

```
                   ┌────────────────────────┐
                   │   Google Sheet (DB)    │
                   │                        │
   user types  ──► │  SERP_Input  pending   │ ◄── status: pending → processing
                   │  SEO_Input   pending   │     → done | error
                   └─────────┬──────────────┘
                             │
              ┌──────────────┴──────────────┐
              ▼                             ▼
   ┌──────────────────────┐      ┌──────────────────────┐
   │ analyzeSerpEntities  │      │  generateSeoContent  │
   │  (Apps Script)       │      │   (Apps Script)      │
   │                      │      │                      │
   │  pick first pending  │      │  pick first pending  │
   │  call SERP API ───┐  │      │  build prompt        │
   │  (or mock)        │  │      │  call LLM ───┐       │
   │  bigram + token   │  │      │  (or mock)   │       │
   │  entity heuristic │  │      │  parse ===SECTION=== │
   │  append 10 rows ◄─┘  │      │  append 1 row ◄──────┘│
   └──────────┬───────────┘      └────────────┬──────────┘
              ▼                                ▼
       SERP_Results                       SEO_Output
       (timestamp, position,              (timestamp, article,
        title, link, snippet,              image_prompt_25/50/75,
        top_entities, mode)                quality_notes, mode)

   Same flow as importable n8n workflow:
   n8n/seo_content_generator.workflow.json
```

## Why these choices

**Why Google Apps Script?** Three reasons, in order: (1) the user is a non-technical
SEO/content teammate — Google Sheets is already their workspace, no new UI to
learn, (2) zero infra: no server, no auth, no deploy, runs on Google's runtime
for free, (3) the sheet is both the input form and the output dashboard, which
collapses three tools into one. The cost is execution-time limits and no
real package manager — accepted for an MVP.

**Why mock mode first?** So the demo runs offline. An interview demo that
depends on a live API key on someone else's network is a bad bet. With
`SERP_MOCK_MODE=true` and `SEO_MOCK_MODE=true` the whole thing works with
zero credentials, and the live path is a one-property-flip away.

**Why a custom `===SECTION===` format instead of JSON mode?** Model-agnostic
(works the same on OpenAI / Anthropic), survives formatting drift better than
strict JSON in a 1-day build, and the parser is ~20 lines.

**Why ship the n8n workflow at all?** It maps the same flow onto a no-code
tool, which is the realistic next step if this graduates from "internal helper"
to "scheduled background job". Importable JSON is the proof.

## MVP tradeoffs (intentional)

| What's intentionally simple | Why | Where to grow |
| --- | --- | --- |
| Entity extraction = bigrams + English tokens + stopwords | Real NLP needs a service / model. Heuristic is enough to show top-5 recurring entities. | Cloud Natural Language API or LLM embeddings |
| One pending row per run | Easier to demo, easier to reason about | Loop until none, with rate-limit guard |
| No dedupe | Keeps logic readable | Hash on (keyword, link) before append |
| Position-based `appendRow` | Matches the obvious sheet layout | Already mitigated: `assertSheetHeaders_` validates header order at runtime |
| n8n workflow doesn't update source rows to `done` | Apps Script path covers this; n8n is the "next step" sketch | Add Google Sheets update node |

## Files

```
apps-script/serp_entity_analyzer.gs        SERP entity flow
apps-script/seo_content_generator.gs       SEO article flow
n8n/seo_content_generator.workflow.json    Equivalent n8n workflow (import-ready)
README_SEO_MVP.md                          This file
DEMO_SCRIPT.md                             2-minute interview demo script + Q&A
```

## Setup

1. Open a Google Sheet.
2. Create these 4 sheets exactly:
   - `SERP_Input`
   - `SERP_Results`
   - `SEO_Input`
   - `SEO_Output`
3. Copy the headers from the schema section below.
4. Open **Extensions → Apps Script**.
5. Create two script files: `serp_entity_analyzer.gs` and `seo_content_generator.gs`.
6. Paste the matching contents from `apps-script/`.
7. Leave `SERP_MOCK_MODE = true` and `SEO_MOCK_MODE = true` for the first demo run.
8. Save and run each function once to approve Apps Script permissions.

## API keys (only needed for live mode)

| Use case | Property name | Required when | Notes |
| --- | --- | --- | --- |
| SERP API | `SERP_API_KEY` | `SERP_MOCK_MODE=false` | SerpAPI by default |
| SERP provider switch | `SERP_PROVIDER` | optional | Set `valueserp` to use ValueSERP |
| LLM API | `LLM_API_KEY` | `SEO_MOCK_MODE=false` | OpenAI or Anthropic |
| LLM provider switch | `LLM_PROVIDER` | optional | Default `openai`, set `anthropic` for Claude |
| SERP location / language / country | `SERP_LOCATION` / `SERP_LANGUAGE` / `SERP_COUNTRY` | optional | Defaults: `Taiwan` / `zh-tw` / `tw` |

Add properties in **Apps Script → Project Settings → Script Properties**.

## Sheet schemas

### `SERP_Input`
| keyword | status | error_message | updated_at |

### `SERP_Results`
| timestamp | input_row | keyword | position | title | link | snippet | top_entities | token_count | mode |

### `SEO_Input`
| keyword | product | scenario | status | error_message | updated_at |

### `SEO_Output`
| timestamp | input_row | keyword | product | scenario | article | image_prompt_25 | image_prompt_50 | image_prompt_75 | quality_notes | mode |

> Header order matters. Both `.gs` files validate it at runtime via
> `assertSerpHeaders_` / `assertSeoHeaders_` and throw a clear error if you
> reorder a column — so you'll never silently get data in the wrong cell.

## 5-minute demo

1. In `SERP_Input` row 2, set `keyword = 狗糧推薦`, `status = pending`.
2. Run `analyzeSerpEntities`. **View → Logs** — you should see
   `[SERP] --- analyzeSerpEntities start (mode=MOCK) ---`
   then `[SERP] Wrote 10 rows to SERP_Results, marked input row 2 as done.`
3. Confirm `SERP_Results` now has 10 rows; `SERP_Input` row 2 status flipped to `done`.
4. In `SEO_Input` row 2, set `keyword = 狗糧推薦`, `product = ProPlan`,
   `scenario = 室內小型犬`, `status = pending`.
5. Run `generateSeoContent`. **View → Logs** — `[SEO] Wrote 1 row to SEO_Output...`.
6. Confirm `SEO_Output` row 2 has the article + 3 image prompts + quality notes;
   `SEO_Input` row 2 status flipped to `done`.

If anything fails, the source row's `status` becomes `error` and `error_message`
contains the reason — the demo never just "does nothing".

## Completed

- Apps Script SERP flow with status machine + error logging
- Apps Script SEO flow with strict-format LLM parsing
- Mock paths for both flows (zero-API-key demo)
- Provider switching for live mode (SerpAPI/ValueSERP, OpenAI/Anthropic)
- Header validation that catches misaligned sheet columns
- `[SERP]` / `[SEO]` execution logs at every meaningful step
- Importable n8n starter workflow (same flow, different runtime)

## Not completed (and why)

- **Real NLP entity extraction** — heuristic is enough for the demo; real NLP
  is a separate decision (Cloud NL API vs. embeddings vs. self-host).
- **Batch processing** — one row per run keeps the demo deterministic. The
  loop is trivial to add once we agree on rate limits.
- **Output dedupe** — would need a key strategy first; not worth a guess.
- **n8n source-row update node** — Apps Script path covers it; n8n is the
  "next step" sketch.
- **Direct image generation** — we generate prompts, not images. Splits the
  decision (DALL·E? Midjourney? in-house diffusion?) cleanly.
- **No UI / no dashboard / no auth / no DB** — the sheet *is* the UI. Adding
  more was explicitly out of scope.

## Future directions

- Swap heuristic for Cloud Natural Language `analyzeEntities` (one URL, same
  shape).
- Add a `BATCH_LIMIT` script property and process N pending rows per run.
- Promote the n8n workflow with the missing update + entity nodes; schedule
  it every 15 minutes so the sheet becomes a "drop in keywords, walk away" tool.
- Move from per-row Apps Script triggers to a `onEdit`-driven flow where
  setting `status = pending` auto-runs the relevant pipeline.
- Replace `===SECTION===` with structured outputs / function calling for
  schema-validated LLM responses.

## Live mode notes

When switching to live mode:

1. Set `SERP_MOCK_MODE = false` or `SEO_MOCK_MODE = false` for the flow you want.
2. Add `SERP_API_KEY` and `LLM_API_KEY` to Script Properties.
3. Optionally set `SERP_PROVIDER=valueserp` or `LLM_PROVIDER=anthropic`.

The code expects:

- SerpAPI or ValueSERP for search
- OpenAI `gpt-4o-mini` or Anthropic `claude-haiku-4-5` for content generation

## n8n notes

The included workflow is intentionally minimal:

- Schedule trigger (every 15 min)
- Read `SERP_Input` and `SEO_Input` in parallel
- Pick the first pending row in each branch
- Call SERP API and OpenAI
- Parse responses
- Append to `SERP_Results` and `SEO_Output`

Open TODOs in the workflow:

- Google Sheets update node to mark source rows as `done`
- Same heuristic entity extraction as the Apps Script version
