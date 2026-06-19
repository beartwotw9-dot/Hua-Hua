# DoraOS

DoraOS is an AI-native personal operating system built around a local-first Obsidian vault, explicit workflows, and a practical memory layer. This starter kit evaluates OpenHuman as the memory and orchestration layer while also defining a safer fallback architecture if OpenHuman beta edges become a maintenance burden.

## What is in this folder

- `setup.sh`: dependency checker and install helper
- `.env.example`: starter environment variables for DoraOS
- `docs/DORAOS_V0.3_OPERATING_MODEL.md`: the practical v0.3 operating model
- `docs/MEMORY_HYGIENE_AND_COMPRESSION.md`: rules for safe, useful memory
- `docs/KNOWLEDGE_ARCHITECTURE.md`: note types, schemas, folder roles, and AI write boundaries
- `docs/ARCHITECTURE.md`: recommended system design
- `docs/OPENHUMAN_EVALUATION.md`: production-readiness assessment
- `docs/INSTALLATION_CHECKLIST.md`: step-by-step install guide
- `docs/TROUBLESHOOTING_CHECKLIST.md`: common failure modes
- `templates/`: reusable daily brief, meeting note, and project brief templates
- `obsidian/DoraOS/`: recommended PARA vault scaffold
- `obsidian/DoraOS/Home.md`: recommended vault landing page inside Obsidian
- `docs/AUTOMATIONS.md`: local launchd automation guide
- `docs/CLOUD_AUTOPILOT.md`: cloud-first autopilot and off-machine reliability
- `recovery/README.md`: Healing Journal sync and weekly recovery review infrastructure
- `operating/README.md`: Gmail, Calendar, research digest, and Zotero bridge infrastructure

## Recommended strategy

Use a hybrid architecture:

1. Obsidian is the human-readable source of truth.
2. Git versions operational notes, prompts, and architecture.
3. OpenHuman is evaluated as an optional memory and connector sidecar.
4. MCP servers provide portable tool access across Codex, Claude Code, and other clients.
5. Local embeddings and lightweight RAG handle retrieval without depending on a fragile monolith.

For the current design target, start with [docs/DORAOS_V0.3_OPERATING_MODEL.md](</Users/youxinhua/Documents/New project/DoraOS/docs/DORAOS_V0.3_OPERATING_MODEL.md>) and [docs/MEMORY_HYGIENE_AND_COMPRESSION.md](</Users/youxinhua/Documents/New project/DoraOS/docs/MEMORY_HYGIENE_AND_COMPRESSION.md>).

If Obsidian is going to be your long-term primary database, also start with [docs/KNOWLEDGE_ARCHITECTURE.md](</Users/youxinhua/Documents/New project/DoraOS/docs/KNOWLEDGE_ARCHITECTURE.md>).

## Quick start

```bash
cd "/Users/youxinhua/Documents/New project/DoraOS"
chmod +x setup.sh
./setup.sh --check
./setup.sh --scaffold
```

## Recommended usage

Installable local CLI:

```bash
chmod +x scripts/install_dora_cli.sh bin/dora
./scripts/install_dora_cli.sh
source ~/.zshrc
which dora
```

Optional alias fallback:

```bash
alias dora='python3 "/Users/youxinhua/Documents/New project/DoraOS/scripts/dora.py"'
```

Core checks:

```bash
dora doctor
dora status
```

Daily brief:

Run the first DoraOS v0.4 loop in dry-run mode:

```bash
dora brief --dry-run --stdout
```

Run in local-only mode:

```bash
dora brief --dry-run --stdout --local-only
```

Write the brief into the Obsidian-safe folder:

```bash
dora brief
```

Test only the Linear connector path:

```bash
dora brief --dry-run --stdout --sources linear
dora brief --dry-run --stdout --sources linear --local-only
```

To enable real Linear reads, set `LINEAR_API_KEY` in `.env` and adjust the Linear scope block in [config/daily_brief.config.example.json](</Users/youxinhua/Documents/New project/DoraOS/config/daily_brief.config.example.json>).

Memory review queue:

```bash
dora memory review
dora memory list
dora memory approve CANDIDATE_ID
dora memory reject CANDIDATE_ID
dora memory review --dry-run
```

Approved memory is stored locally in [approved-memory.md](</Users/youxinhua/Documents/New project/DoraOS/obsidian/DoraOS/Resources/AI%20Memory/approved-memory.md>). Nothing is sent to external memory systems automatically.

Weekly review loop:

```bash
dora weekly --dry-run --stdout
dora weekly --start 2026-05-06 --end 2026-05-13
dora weekly --stdout
```

Weekly reviews are written to [AI Weekly Reviews](</Users/youxinhua/Documents/New project/DoraOS/obsidian/DoraOS/Resources/AI%20Weekly%20Reviews>) and only read from the safe AI-generated folders by default.

Research source integration:

```bash
dora research status
dora research sync --dry-run --stdout
dora research sync
dora research grep diffusion
dora research note CoDi
dora research project CoDi --topic multimodal
dora research shortlist --topic multimodal
dora research article https://example.com/some-article --topic ai-reading
```

This keeps `google-research` as a local vendor corpus and writes compact source notes into [Research Sources](</Users/youxinhua/Documents/New project/DoraOS/obsidian/DoraOS/Resources/Research%20Sources>) instead of pushing the whole repo into daily brief or memory scope.

Operating research sync:

```bash
cd "/Users/youxinhua/Documents/New project/DoraOS/operating"
./.venv/bin/python research_digest_sync.py --env-file "/Users/youxinhua/Documents/New project/DoraOS/.env" --config "/Users/youxinhua/Documents/New project/DoraOS/config/research_digest.config.example.json"
./.venv/bin/python zotero_bridge_sync.py --env-file "/Users/youxinhua/Documents/New project/DoraOS/.env" --config "/Users/youxinhua/Documents/New project/DoraOS/config/zotero_bridge.config.example.json"
./.venv/bin/python operating_feed_compose.py --env-file "/Users/youxinhua/Documents/New project/DoraOS/.env"
```

Cloud-first autopilot:

```bash
dora autopilot --mode daily --verbose
```

This is the recommended off-machine execution path for:

- missing-page backfill
- Gmail / Calendar daily context
- research digest
- Operating Feed
- Today Hub
- daily brief

Dry-run verification:

```bash
dora doctor
dora status
dora brief --dry-run --stdout
dora memory list
dora weekly --dry-run --stdout
```

Read the command center guide at [docs/COMMAND_CENTER.md](</Users/youxinhua/Documents/New project/DoraOS/docs/COMMAND_CENTER.md>).
Read the research source guide at [docs/RESEARCH_SOURCES.md](</Users/youxinhua/Documents/New project/DoraOS/docs/RESEARCH_SOURCES.md>).
Read the full pipeline guide at [docs/DAILY_BRIEF_PIPELINE.md](</Users/youxinhua/Documents/New project/DoraOS/docs/DAILY_BRIEF_PIPELINE.md>).
Read the knowledge architecture guide at [docs/KNOWLEDGE_ARCHITECTURE.md](</Users/youxinhua/Documents/New project/DoraOS/docs/KNOWLEDGE_ARCHITECTURE.md>).
Read the automation guide at [docs/AUTOMATIONS.md](</Users/youxinhua/Documents/New project/DoraOS/docs/AUTOMATIONS.md>).
Read the cloud-first autopilot guide at [docs/CLOUD_AUTOPILOT.md](</Users/youxinhua/Documents/New project/DoraOS/docs/CLOUD_AUTOPILOT.md>).
Read the Notion migration guide at [docs/NOTION_OBSIDIAN_MIGRATION_DECISION_TABLE.md](</Users/youxinhua/Documents/New project/DoraOS/docs/NOTION_OBSIDIAN_MIGRATION_DECISION_TABLE.md>).
Read the Notion hybrid integration guide at [docs/NOTION_HYBRID_V1.md](</Users/youxinhua/Documents/New project/DoraOS/docs/NOTION_HYBRID_V1.md>).
Read the daily Notion backup config at [config/notion_backup.config.example.json](</Users/youxinhua/Documents/New project/DoraOS/config/notion_backup.config.example.json>).
Read the recovery sync guide at [recovery/README.md](</Users/youxinhua/Documents/New project/DoraOS/recovery/README.md>).
Read the operating feed sync guide at [operating/README.md](</Users/youxinhua/Documents/New project/DoraOS/operating/README.md>).

Uninstall the CLI PATH wiring:

```bash
awk '/^# >>> DoraOS CLI >>>$/{skip=1;next}/^# <<< DoraOS CLI <<</{skip=0;next}!skip{print}' ~/.zshrc > ~/.zshrc.tmp && mv ~/.zshrc.tmp ~/.zshrc
source ~/.zshrc
```

To run the published OpenHuman installer:

```bash
./setup.sh --install-openhuman
```

To optionally clone the OpenHuman source code for deeper customization:

```bash
./setup.sh --clone-openhuman
```

## Current machine snapshot

Verified on this Mac on 2026-05-13:

- `node`: `v24.15.0`
- `npm`: `11.12.1`
- `git`: `2.50.1`
- `python3`: `3.12.6`
- Missing: `pnpm`, `rustc`, `cargo`, `brew`, `ollama`, `gh`

That means the published OpenHuman app installer can run, but source-level customization is not ready until `pnpm` and Rust are installed.
