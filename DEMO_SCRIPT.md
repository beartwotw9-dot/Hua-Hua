# DEMO_SCRIPT.md — SEO Automation MVP

A 2-minute live demo + Q&A prep, designed for technical interviews where you
have laptop + screen-share. Built for "what did you build in a day?" prompts.

---

## The 2-minute pitch (memorize this shape, not the words)

**0:00 — frame the problem (15s)**

> "SEO content teams keep doing the same loop: pick a keyword, look at
> Google's top 10, extract recurring entities, brief a writer, generate
> the article + image prompts + quality notes. I built a 1-day MVP that
> automates that loop end-to-end as a Google Sheet."

**0:15 — show the input (15s).** Click `SERP_Input`, point at the row:
`keyword=狗糧推薦, status=pending`. Say:

> "The whole interface is the sheet. A non-technical teammate types a keyword
> and sets status to pending. That's it."

**0:30 — run SERP (20s).** `Extensions → Apps Script → analyzeSerpEntities → Run`.
Switch to logs:

> "Mock mode is on by default — no API key, fully offline demo.
> The script picks the first pending row, calls SERP (or in mock, returns
> 10 canned results), runs a tiny entity heuristic, writes 10 rows back."

Switch to `SERP_Results`. Point at `top_entities` column.

**0:50 — show the status machine (10s).** Switch back to `SERP_Input`:

> "And the source row is now `done` with a timestamp. If anything fails the
> status becomes `error` and the error message lands in the next column —
> so the demo never just silently does nothing."

**1:00 — run SEO (30s).** Click `SEO_Input`, point at row:
`keyword=狗糧推薦, product=ProPlan, scenario=室內小型犬, status=pending`.
Run `generateSeoContent`. Switch to logs, then `SEO_Output`. Point at:

> "One row out: the article, three image prompts at 25/50/75% of the article,
> quality notes, mode=mock. The LLM returns a strict `===SECTION===` format
> that's model-agnostic — works the same on OpenAI or Anthropic."

**1:30 — close on the architecture (20s).** Open the README architecture
diagram or the n8n JSON:

> "If this graduates from internal helper to scheduled job, the same flow
> imports into n8n — that workflow is in the repo. Trade-offs are listed
> explicitly in the README: one row per run, heuristic entity extraction,
> no dedupe — all deliberate, all easy to grow."

**1:50 — invite questions (10s).**

> "Happy to dig into any part — prompt design, the entity heuristic, why
> Apps Script over a real backend, or what I'd change next."

---

## What to show, in order

1. The **sheet** (input row, pending status).
2. **Apps Script editor** — briefly, to prove it's real code, not a blob.
3. **Logs panel** showing `[SERP]` / `[SEO]` lines.
4. **Output sheets** with the appended rows.
5. **Source row flipped to `done`** (the status-machine moment).
6. The **README architecture diagram** OR the **n8n JSON**.

## What NOT to open in the first 2 minutes

- The entity heuristic body. It's a stopword list + bigram regex — interesting
  but a derail. Mention "it's a heuristic, see the README", move on.
- The provider-switching code in `callLlm_`. Mention provider abstraction;
  open it only if asked.
- The full `getMockSeoResponse_` body. Just say "canned response, identical
  shape to the real API output". Open if asked.
- Anything in the repo that isn't SEO MVP (`backend/`, `frontend/`,
  `competitions/`, etc.). Close those tabs before screen-share.

## What to defer (great answers when asked)

| If asked | Say |
| --- | --- |
| "Is the entity extraction real NLP?" | "No — Chinese bigrams + English tokens + stopword filter. It's a 1-day MVP; the heuristic proves the wiring. Real NLP is in the README under future directions: Cloud NL API or LLM embeddings." |
| "Why not JSON mode?" | "Model-agnostic and resilient to formatting drift. JSON mode is a clean upgrade path once we commit to one provider." |
| "Why Sheets, not a real DB?" | "The user is non-technical — Sheets *is* the UI and the DB and the dashboard. Three tools collapse into one. Cost: execution-time limits and no schema enforcement. Mitigated for headers via runtime header validation." |
| "What if the LLM returns malformed sections?" | "`parseSections_` returns empty strings for missing sections, but throws if `ARTICLE` is missing. The outer try/catch writes the error into `error_message` so the source row never silently disappears." |
| "How do you scale to 1000 keywords?" | "Today, you can't — one row per run. Two paths: (a) batch loop in Apps Script with a rate limiter, (b) promote to the n8n workflow on a 15-min schedule. Path (b) is in the repo as the import-ready JSON." |
| "Is the n8n workflow tested?" | "It imports cleanly and the flow matches the Apps Script. I've explicitly listed in the README what's NOT done in n8n: the source-row update and the entity extraction. Honest gaps, not surprises." |
| "What about cost?" | "Mock mode = $0. Live mode = SerpAPI tier + `gpt-4o-mini` (cheap). For a real deploy I'd put a daily cap in Script Properties and abort if exceeded." |

## Likely deeper questions + suggested answers

**"Walk me through what happens when a row fails."**
> "Outer try/catch in `analyzeSerpEntities` (and `generateSeoContent`). On
> error: source row status becomes `error`, `error_message` gets the first
> 500 chars of the exception, `updated_at` gets a timestamp, and the
> exception re-throws so it shows up red in the Apps Script execution log.
> So the user sees the problem in three places: the source sheet, the logs,
> and (if they're watching) the editor."

**"What's the failure mode you're most worried about?"**
> "Sheet header drift. If someone reorders a column in `SERP_Results`,
> `appendRow` would silently write to the wrong cell. So I added
> `assertSerpHeaders_` / `assertSeoHeaders_` — runs first thing, throws a
> clear 'expected X got Y' error before any data gets written. That's the
> single most likely demo-day failure and the cheapest one to prevent."

**"Why two .gs files instead of one?"**
> "Two independent flows, two manual triggers, two error surfaces. They
> share zero state. Putting them in the same file would just merge two
> namespaces that have nothing to do with each other. Helpers are prefixed
> `Serp` / `Seo` so even if Apps Script puts them in one global scope, no
> collision. I considered a shared `_helpers.gs` and decided against — for
> 600 lines total it's not worth the cognitive overhead."

**"What would you do differently with another day?"**
> "Three things, ranked: (1) batch processing — loop pending rows with a
> rate limiter, (2) onEdit trigger so setting status=pending auto-runs the
> pipeline, (3) replace `===SECTION===` with OpenAI structured outputs /
> Anthropic JSON mode. Each is small. None changes the architecture."

**"Did you use AI to write this?"**
> Be honest. Something like: "Yes — I used [tool] for parts of the prompt
> design and the regex parser. I reviewed every line, ran the demo end-to-end
> in mock mode, added the header validation and logging myself when I noticed
> appendRow could silently misalign. The tradeoffs and the architecture
> decisions are mine."

## Pre-demo checklist (run 5 minutes before)

- [ ] Sheet open, all 4 tabs created with correct headers
- [ ] `SERP_Input` row 2 cleared back to `pending` (or new row)
- [ ] `SEO_Input` row 2 cleared back to `pending`
- [ ] Apps Script editor open in another tab, both functions visible
- [ ] `SERP_MOCK_MODE = true` and `SEO_MOCK_MODE = true` confirmed
- [ ] Test run once — make sure logs show `[SERP]` / `[SEO]` lines
- [ ] Close every tab unrelated to the SEO MVP
- [ ] README and DEMO_SCRIPT open in a third tab as backup if screen-share lags

## If the demo breaks

Calm, then in this order:

1. Check **Logs**. The `[SERP]` / `[SEO]` lines tell you exactly how far it got.
2. If "Header mismatch" — the sheet headers got edited; copy them back from the README schema section.
3. If "Missing sheet" — one of the 4 tabs got renamed.
4. If "Missing keyword" — the row's keyword cell is empty.
5. If something else — show the error message, say *"this is exactly the
   path I was describing — the failure shows up in three places: source
   row, logs, and the execution stack"*. The error itself becomes the demo.
