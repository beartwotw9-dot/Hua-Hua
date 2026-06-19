#!/usr/bin/env python3
"""Daily Notion backup into Obsidian-safe markdown snapshots."""

from __future__ import annotations

import argparse
import json
import re
import shutil
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from notion_client import Client

from common import DEFAULT_ENV_FILE, LOG_DIR, ROOT, STATE_DIR, build_logger, ensure_dir, load_env_file, normalize_space, read_json, require_env, retry_call, slugify, to_iso_date, unique_list, wait_for_network, write_json


SYNC_ROOT = ROOT / "obsidian" / "DoraOS" / "Resources" / "Notion Sync"
STATE_FILE = STATE_DIR / "notion_backup_state.json"
URL_RE = re.compile(r"https://www\.notion\.so/([a-f0-9]{32})", re.I)
COLLECTION_RE = re.compile(r"collection://([a-f0-9-]{36})", re.I)


@dataclass
class SourceRef:
    kind: str
    notion_id: str
    title: str
    source_note: str


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Backup Notion pages and databases into daily Obsidian snapshots.")
    parser.add_argument("--env-file", default=str(DEFAULT_ENV_FILE))
    parser.add_argument("--config", default=str(ROOT / "config" / "notion_backup.config.example.json"))
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--verbose", action="store_true")
    return parser.parse_args()


def load_config(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _normalize_id(value: str) -> str:
    raw = value.strip()
    if len(raw) == 32 and "-" not in raw:
        return f"{raw[:8]}-{raw[8:12]}-{raw[12:16]}-{raw[16:20]}-{raw[20:]}"
    return raw


def rich_text_to_plain(rich_text: List[Dict[str, Any]]) -> str:
    return "".join(segment.get("plain_text", "") for segment in rich_text)


def property_to_plain(prop: Dict[str, Any]) -> Any:
    if not prop:
        return None
    prop_type = prop.get("type")
    if prop_type == "title":
        return rich_text_to_plain(prop.get("title", []))
    if prop_type == "rich_text":
        return rich_text_to_plain(prop.get("rich_text", []))
    if prop_type == "number":
        return prop.get("number")
    if prop_type == "select":
        item = prop.get("select")
        return item.get("name") if item else None
    if prop_type == "multi_select":
        return [entry.get("name", "") for entry in prop.get("multi_select", [])]
    if prop_type == "date":
        item = prop.get("date")
        return item.get("start") if item else None
    if prop_type == "checkbox":
        return prop.get("checkbox")
    if prop_type == "status":
        item = prop.get("status")
        return item.get("name") if item else None
    if prop_type == "url":
        return prop.get("url")
    if prop_type == "email":
        return prop.get("email")
    if prop_type == "phone_number":
        return prop.get("phone_number")
    if prop_type == "people":
        return [entry.get("name", "") for entry in prop.get("people", [])]
    return None


def fetch_children(notion: Client, block_id: str) -> List[Dict[str, Any]]:
    blocks: List[Dict[str, Any]] = []
    cursor: Optional[str] = None
    while True:
        response = retry_call(
            lambda: notion.blocks.children.list(block_id=block_id, start_cursor=cursor, page_size=100),
            retries=3,
            base_delay=2.0,
            should_retry=_is_retryable_notion_error,
        )
        blocks.extend(response.get("results", []))
        if not response.get("has_more"):
            return blocks
        cursor = response.get("next_cursor")


def block_text(block: Dict[str, Any]) -> str:
    block_type = block.get("type", "")
    payload = block.get(block_type, {})
    if "rich_text" in payload:
        return rich_text_to_plain(payload.get("rich_text", []))
    if block_type == "child_page":
        return payload.get("title", "")
    return ""


def render_blocks_markdown(notion: Client, blocks: List[Dict[str, Any]], indent: int = 0) -> List[str]:
    lines: List[str] = []
    for block in blocks:
        block_type = block.get("type", "")
        text = block_text(block).strip()
        if block_type == "paragraph":
            if text:
                lines.append(text)
        elif block_type == "heading_1":
            lines.append(f"# {text}")
        elif block_type == "heading_2":
            lines.append(f"## {text}")
        elif block_type == "heading_3":
            lines.append(f"### {text}")
        elif block_type == "bulleted_list_item":
            lines.append(f"{'  ' * indent}- {text}")
        elif block_type == "numbered_list_item":
            lines.append(f"{'  ' * indent}1. {text}")
        elif block_type == "to_do":
            checked = block.get("to_do", {}).get("checked", False)
            lines.append(f"{'  ' * indent}- [{'x' if checked else ' '}] {text}")
        elif block_type == "quote":
            lines.append(f"> {text}")
        elif block_type == "callout":
            icon = block.get("callout", {}).get("icon", {}).get("emoji", "")
            lines.append(f"> {icon + ' ' if icon else ''}{text}")
        elif block_type == "divider":
            lines.append("---")
        elif block_type == "code":
            language = block.get("code", {}).get("language", "")
            lines.append(f"```{language}".rstrip())
            lines.append(text)
            lines.append("```")
        else:
            if text:
                lines.append(text)

        if block.get("has_children"):
            child_lines = render_blocks_markdown(notion, fetch_children(notion, block["id"]), indent + 1)
            if child_lines:
                lines.extend(child_lines)
    cleaned: List[str] = []
    prev_blank = False
    for line in lines:
        blank = not line.strip()
        if blank and prev_blank:
            continue
        cleaned.append(line)
        prev_blank = blank
    return cleaned


def fetch_page_markdown(notion: Client, page_id: str) -> str:
    return "\n".join(render_blocks_markdown(notion, fetch_children(notion, page_id))).strip()


def resolve_query_parent_id(notion: Client, identifier: str) -> Tuple[str, str]:
    normalized = identifier.replace("collection://", "").strip()
    if hasattr(notion.databases, "query"):
        return "database", normalized
    if hasattr(notion, "data_sources") and hasattr(notion.data_sources, "query"):
        try:
            database = retry_call(
                lambda: notion.databases.retrieve(database_id=normalized),
                retries=3,
                base_delay=2.0,
                should_retry=_is_retryable_notion_error,
            )
            for key in ("data_sources", "dataSources"):
                value = database.get(key)
                if isinstance(value, list) and value:
                    first = value[0]
                    if isinstance(first, dict) and first.get("id"):
                        return "data_source", first["id"]
        except Exception:
            pass
        return "data_source", normalized
    raise RuntimeError("This Notion client does not support database or data source queries.")


def query_database(notion: Client, database_id: str, page_size: int) -> List[Dict[str, Any]]:
    results: List[Dict[str, Any]] = []
    cursor: Optional[str] = None
    parent_type, parent_id = resolve_query_parent_id(notion, database_id)
    while True:
        payload: Dict[str, Any] = {"page_size": min(page_size, 100)}
        if parent_type == "database":
            payload["database_id"] = parent_id
            response = retry_call(
                lambda: notion.databases.query(**payload, start_cursor=cursor) if cursor else notion.databases.query(**payload),
                retries=3,
                base_delay=2.0,
                should_retry=_is_retryable_notion_error,
            )
        else:
            payload["data_source_id"] = parent_id
            response = retry_call(
                lambda: notion.data_sources.query(**payload, start_cursor=cursor) if cursor else notion.data_sources.query(**payload),
                retries=3,
                base_delay=2.0,
                should_retry=_is_retryable_notion_error,
            )
        results.extend(response.get("results", []))
        if len(results) >= page_size or not response.get("has_more"):
            return results[:page_size]
        cursor = response.get("next_cursor")


def discover_sources(notes_root: Path) -> List[SourceRef]:
    found: Dict[Tuple[str, str], SourceRef] = {}
    skipped_database_notes = {
        "2026-05-18 Notion Projects Schema.md",
        "2026-05-18 Notion Tasks HQ Schema.md",
        "2026-05-18 Notion Weekly Review Schema.md",
    }
    for path in sorted(notes_root.glob("*.md")):
        if path.name in {"Notion Sync Index.md", "Notion 全量鏡像索引.md", "Today Task Bridge.md", "Notion Backup Status.md"}:
            continue
        if path.name in skipped_database_notes:
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        title = clean_source_title(path.stem)
        if "## Source" in text and "Data source:" in text:
            match = COLLECTION_RE.search(text)
            if match:
                notion_id = _normalize_id(match.group(1))
                found[("database", notion_id)] = SourceRef("database", notion_id, title, path.name)
                continue
        url_match = URL_RE.search(text)
        if url_match:
            notion_id = _normalize_id(url_match.group(1))
            found[("page", notion_id)] = SourceRef("page", notion_id, title, path.name)
    return list(found.values())


def render_database_snapshot(notion: Client, ref: SourceRef, rows_limit: int) -> str:
    database = retry_call(
        lambda: notion.databases.retrieve(database_id=ref.notion_id),
        retries=3,
        base_delay=2.0,
        should_retry=_is_retryable_notion_error,
    )
    properties = database.get("properties", {})
    rows = query_database(notion, ref.notion_id, rows_limit)
    lines = [
        f"# {ref.title}",
        "",
        "## Backup Metadata",
        "",
        f"- source_note: `{ref.source_note}`",
        f"- notion_id: `{ref.notion_id}`",
        f"- backup_type: `database`",
        "",
        "## Schema",
        "",
    ]
    for name, prop in properties.items():
        lines.append(f"- `{name}`: `{prop.get('type', 'unknown')}`")
    lines += ["", "## Recent Rows", ""]
    if not rows:
        lines.append("- no rows returned")
    for row in rows:
        props = row.get("properties", {})
        title_value = ""
        summary_parts: List[str] = []
        for name, prop in props.items():
            plain = property_to_plain(prop)
            if plain in (None, "", []):
                continue
            if isinstance(plain, list):
                plain_text = ", ".join(str(item) for item in plain if str(item).strip())
            else:
                plain_text = str(plain)
            if prop.get("type") == "title" and not title_value:
                title_value = plain_text
            summary_parts.append(f"{name}: {normalize_space(plain_text)}")
        lines.append(f"### {title_value or row.get('id', 'Untitled row')}")
        lines.append("")
        for part in summary_parts[:12]:
            lines.append(f"- {part}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def render_page_snapshot(notion: Client, ref: SourceRef) -> str:
    page = retry_call(
        lambda: notion.pages.retrieve(page_id=ref.notion_id),
        retries=3,
        base_delay=2.0,
        should_retry=_is_retryable_notion_error,
    )
    page_md = fetch_page_markdown(notion, ref.notion_id)
    lines = [
        f"# {ref.title}",
        "",
        "## Backup Metadata",
        "",
        f"- source_note: `{ref.source_note}`",
        f"- notion_id: `{ref.notion_id}`",
        f"- backup_type: `page`",
        f"- last_edited_time: `{page.get('last_edited_time', '')}`",
        "",
        "## Page Content",
        "",
        page_md or "_No page content returned._",
        "",
    ]
    return "\n".join(lines)


def classify_failure(reason: str) -> tuple[str, str]:
    lowered = reason.lower()
    if "could not find database with id" in lowered:
        return ("share_required", "database not shared with integration")
    if "token is expired" in lowered or "token_expired" in lowered:
        return ("reauth_required", "notion connector needs re-auth")
    if "nodename nor servname provided" in lowered or "failed to resolve" in lowered:
        return ("network_issue", "network or dns issue")
    if "name resolution" in lowered or "temporarily unavailable" in lowered or "connection aborted" in lowered:
        return ("network_issue", "network or dns issue")
    if "timeout" in lowered or "timed out" in lowered or "remote end closed connection without response" in lowered:
        return ("network_issue", "network or dns issue")
    return ("other", "other")


def _retry_settings(config: Dict[str, Any], env: Dict[str, str]) -> tuple[int, float]:
    # Read from .env first (same keys as operating/ scripts), then fall back to config file keys
    retries = int(
        env.get("SYNC_RETRY_ATTEMPTS", "")
        or config.get("SYNC_RETRY_ATTEMPTS", "")
        or config.get("retries", 3)
        or 3
    )
    retry_delay = float(
        env.get("SYNC_RETRY_BACKOFF_SECONDS", "")
        or config.get("SYNC_RETRY_BACKOFF_SECONDS", "")
        or config.get("retry_delay_seconds", 2.0)
        or 2.0
    )
    return retries, retry_delay


def _is_retryable_notion_error(exc: Exception) -> bool:
    code, _ = classify_failure(str(exc))
    return code == "network_issue"


def clean_source_title(title: str) -> str:
    return re.sub(r"^\d{4}-\d{2}-\d{2}\s+", "", title).strip()


def write_status_note(
    vault_path: Path,
    today: str,
    results: List[Dict[str, str]],
    failures: List[Dict[str, str]],
    recovered_failures: List[Dict[str, str]],
) -> None:
    lines = [
        "# Notion Backup Status",
        "",
        f"- date: {today}",
        "",
        "## Today",
        "",
    ]
    if not results:
        lines.append("- no sources backed up")
    for result in results:
        status = result.get("status", "live")
        label = "fallback" if status == "fallback" else result["kind"]
        lines.append(f"- {label}: [[Resources/Notion Sync/Daily Backups/{today}/{result['filename'][:-3]}]]")
    limitations = failures + recovered_failures
    if limitations:
        grouped: Dict[str, List[str]] = {"share_required": [], "reauth_required": [], "network_issue": [], "other": []}
        for failure in limitations:
            code, _ = classify_failure(failure["reason"])
            grouped.setdefault(code, []).append(clean_source_title(failure["title"]))
        lines += [
            "",
            "## Limitations",
            "",
            f"- affected_sources: {len(limitations)}",
        ]
        if recovered_failures:
            lines.append(f"- fallback_snapshots_used: {len(recovered_failures)}")
        if grouped["share_required"]:
            lines += [
                f"- 需要分享給 integration `dog` 的資料庫 | databases that still need sharing: {', '.join(grouped['share_required'])}",
                "- 請在 Notion 將這些 database 分享給 `dog`，之後每日備份就會補齊 | Share these databases with `dog` in Notion, then daily backup will include them.",
            ]
        if grouped["reauth_required"]:
            lines += [
                f"- Notion 連線需要重新登入 | Notion connector needs re-auth: {', '.join(grouped['reauth_required'])}",
            ]
        if grouped["network_issue"]:
            lines += [
                f"- 網路 / DNS 問題影響備份 | network or DNS affected backup: {', '.join(grouped['network_issue'])}",
            ]
        other_failures = [failure for failure in limitations if classify_failure(failure["reason"])[0] == "other"]
        if other_failures:
            lines += [
                "",
                "### Technical Details",
                "",
            ]
            for failure in other_failures:
                lines.append(f"- {failure['kind']}: `{failure['title']}` -> {failure['reason']}")
        else:
            lines += [
                "",
                "### Technical Details",
                "",
                "- none",
            ]
    else:
        lines += [
            "",
            "## Limitations",
            "",
            "- none",
        ]
    lines.append("")
    status_path = vault_path / "Resources" / "Notion Sync" / "Notion Backup Status.md"
    ensure_dir(status_path.parent)
    status_path.write_text("\n".join(lines), encoding="utf-8")


def attempt_fetch_snapshot(notion: Client, ref: SourceRef, rows_limit: int, retries: int, retry_delay: float, logger) -> str:
    def _fetch() -> str:
        if ref.kind == "database":
            return render_database_snapshot(notion, ref, rows_limit)
        return render_page_snapshot(notion, ref)

    return retry_call(
        _fetch,
        retries=retries,
        base_delay=retry_delay,
        should_retry=_is_retryable_notion_error,
        on_retry=lambda exc, attempt, wait: logger.warning("Notion retry %s for %s (%s) after error: %s", attempt, ref.title, ref.kind, exc),
    )


def restore_previous_snapshot(ref: SourceRef, state: Dict[str, Any], out_dir: Path, today: str) -> Dict[str, str] | None:
    snapshot = state.get("snapshots", {}).get(f"{ref.kind}:{ref.notion_id}")
    if not isinstance(snapshot, dict):
        return None
    source_file = Path(str(snapshot.get("file", "")))
    if not source_file.exists():
        return None
    filename = f"{slugify(ref.title, 'notion-backup')}.md"
    target = out_dir / filename
    if source_file.resolve() != target.resolve():
        shutil.copy2(source_file, target)
    text = target.read_text(encoding="utf-8", errors="ignore")
    marker = f"- backup_date: `{today}`"
    if "## Backup Metadata" in text and marker not in text:
        text = text.replace("## Backup Metadata\n", f"## Backup Metadata\n\n{marker}\n- backup_status: `fallback`\n", 1)
        target.write_text(text, encoding="utf-8")
    return {"kind": ref.kind, "filename": filename, "status": "fallback"}


def main() -> int:
    args = parse_args()
    config = load_config(Path(args.config))
    if not config.get("enabled", True):
        return 0
    env = load_env_file(Path(args.env_file))
    notion_token = require_env(env, "NOTION_API_KEY")
    vault_path = Path(require_env(env, "OBSIDIAN_VAULT_PATH")).expanduser()
    logger = build_logger("doraos.notion_backup", LOG_DIR / "dora_notion_backup_sync.log", verbose=args.verbose)

    network_timeout = float(env.get("NETWORK_WARMUP_TIMEOUT", "120") or "120")
    wait_for_network(timeout=network_timeout, logger=logger)

    notion = Client(auth=notion_token)

    notes_root = vault_path / "Resources" / "Notion Sync"
    sources = discover_sources(notes_root) if config.get("discover_from_existing_notes", True) else []
    for raw in config.get("explicit_sources", []):
        if isinstance(raw, dict) and raw.get("kind") and raw.get("id"):
            sources.append(SourceRef(str(raw["kind"]), _normalize_id(str(raw["id"])), str(raw.get("title", raw["id"])), "config"))
    deduped: Dict[Tuple[str, str], SourceRef] = {(item.kind, item.notion_id): item for item in sources}
    sources = list(deduped.values())
    logger.info("Discovered %s Notion sources for backup", len(sources))

    today = datetime.now().date().isoformat()
    out_dir = vault_path / config.get("output_subdir", "Resources/Notion Sync/Daily Backups") / today
    ensure_dir(out_dir)
    state = read_json(STATE_FILE, {"last_run": None, "snapshots": {}})
    rows_limit = int(config.get("recent_rows_limit", 10))
    retries, retry_delay = _retry_settings(config, env)

    results: List[Dict[str, str]] = []
    failures: List[Dict[str, str]] = []
    recovered_failures: List[Dict[str, str]] = []
    for ref in sources:
        filename = f"{slugify(ref.title, 'notion-backup')}.md"
        target = out_dir / filename
        try:
            content = attempt_fetch_snapshot(notion, ref, rows_limit, retries, retry_delay, logger)
            if args.dry_run:
                logger.info("Dry run for %s -> %s", ref.title, target)
            else:
                target.write_text(content, encoding="utf-8")
            results.append({"kind": ref.kind, "filename": filename, "status": "live"})
            state["snapshots"][f"{ref.kind}:{ref.notion_id}"] = {
                "date": today,
                "file": str(target),
                "title": ref.title,
                "updated_at": datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z"),
            }
        except Exception as exc:
            logger.warning("Backup failed for %s (%s): %s", ref.title, ref.kind, exc)
            reason = normalize_space(str(exc))
            code, _ = classify_failure(reason)
            if code == "network_issue" and not args.dry_run:
                restored = restore_previous_snapshot(ref, state, out_dir, today)
                if restored:
                    logger.info("Restored fallback snapshot for %s (%s)", ref.title, ref.kind)
                    results.append(restored)
                    recovered_failures.append({"kind": ref.kind, "title": ref.title, "reason": reason})
                    continue
            failures.append({"kind": ref.kind, "title": ref.title, "reason": reason})

    if not args.dry_run:
        write_status_note(vault_path, today, results, failures, recovered_failures)
        state["last_run"] = datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")
        write_json(STATE_FILE, state)
    logger.info("Notion backup complete. wrote=%s failed=%s dry_run=%s", len(results), len(failures), args.dry_run)
    live_count = sum(1 for r in results if r.get("status") == "live")
    fallback_count = sum(1 for r in results if r.get("status") == "fallback")
    print(f"backed_up={len(results)} live={live_count} fallback_used={fallback_count} failed={len(failures)} output={out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
