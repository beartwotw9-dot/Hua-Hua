#!/usr/bin/env python3
"""Sync Notion Healing Journal entries into the DoraOS Obsidian vault."""

from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
import re
from typing import Any, Dict, Iterable, List, Optional

import frontmatter
from notion_client import Client

from common import (
    DEFAULT_ENV_FILE,
    GENERATED_END,
    GENERATED_START,
    LOG_DIR,
    ROOT,
    STATE_DIR,
    as_int,
    build_logger,
    ensure_dir,
    hash_signature,
    load_env_file,
    normalize_space,
    read_json,
    require_env,
    retry_call,
    split_generated_and_manual,
    to_iso_date,
    unique_list,
    wait_for_network,
    wrap_generated,
    write_json,
)


OUTPUT_DIR = Path("Areas") / "Mental Health" / "Healing Journal"
QUALITY_REPORT_PATH = Path("Resources") / "Notion Sync" / "Healing Journal Data Quality.md"
STATE_FILE = STATE_DIR / "healing_journal_sync_state.json"

FIELD_ALIASES = {
    "date": ["date", "日期", "Date 日期", "Date 日期 1", "date 日期", "date 日期 1", "date 日期1"],
    "mood": ["mood", "心情"],
    "energy": ["energy", "能量", "energy level"],
    "anxiety": ["anxiety", "焦慮"],
    "brain_fog": ["brain fog", "brain_fog", "腦霧"],
    "sleep": ["sleep", "睡眠"],
    "risk": ["risk", "風險"],
    "body_notes": ["body notes", "body", "身體 notes", "身體"],
    "stressors": ["stressors", "壓力源"],
    "wins": ["wins", "小勝利", "positive signals"],
    "tomorrow_top_3": ["tomorrow top 3", "tomorrow top3", "明日 top 3", "tomorrow"],
    "title": ["name", "title", "標題"],
}


def _is_retryable_notion_error(exc: Exception) -> bool:
    lowered = str(exc).lower()
    markers = [
        "timed out",
        "timeout",
        "request to notion api has timed out",
        "temporarily unavailable",
        "connection reset",
        "connection aborted",
        "remote end closed connection without response",
        "nodename nor servname provided",
        "name resolution",
    ]
    return any(marker in lowered for marker in markers)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Sync the Notion Healing Journal into Obsidian.")
    parser.add_argument("--env-file", default=str(DEFAULT_ENV_FILE))
    parser.add_argument("--vault-path", default="")
    parser.add_argument("--database-id", default="")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--verbose", action="store_true")
    return parser.parse_args()


def normalized_name(text: str) -> str:
    return normalize_space(text).casefold()


def get_property(page: Dict[str, Any], aliases: Iterable[str]) -> Optional[Dict[str, Any]]:
    properties = page.get("properties", {})
    alias_set = {normalized_name(alias) for alias in aliases}
    for name, prop in properties.items():
        if normalized_name(name) in alias_set:
            return prop
    return None


def rich_text_to_plain(rich_text: List[Dict[str, Any]]) -> str:
    return "".join(segment.get("plain_text", "") for segment in rich_text)


def property_to_value(prop: Optional[Dict[str, Any]]) -> Any:
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
        select = prop.get("select")
        return select.get("name") if select else None
    if prop_type == "multi_select":
        return [item.get("name", "") for item in prop.get("multi_select", [])]
    if prop_type == "date":
        date_obj = prop.get("date")
        return date_obj.get("start") if date_obj else None
    if prop_type == "checkbox":
        return prop.get("checkbox")
    if prop_type == "status":
        status = prop.get("status")
        return status.get("name") if status else None
    if prop_type == "people":
        return [person.get("name", "") for person in prop.get("people", [])]
    return None


def extract_field(page: Dict[str, Any], key: str) -> Any:
    prop = get_property(page, FIELD_ALIASES[key])
    return property_to_value(prop)


def resolve_query_parent_id(notion: Client, identifier: str) -> tuple[str, str]:
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
        except Exception:  # noqa: BLE001
            pass
        return "data_source", normalized
    raise RuntimeError("This Notion client does not support database or data source queries.")


def paginate_database(notion: Client, database_id: str, limit: int = 0) -> List[Dict[str, Any]]:
    pages: List[Dict[str, Any]] = []
    cursor: Optional[str] = None
    parent_type, parent_id = resolve_query_parent_id(notion, database_id)
    while True:
        payload: Dict[str, Any] = {
            "page_size": 100,
            "sorts": [{"timestamp": "last_edited_time", "direction": "descending"}],
        }
        if parent_type == "database":
            payload["database_id"] = parent_id
        else:
            payload["data_source_id"] = parent_id
        if cursor:
            payload["start_cursor"] = cursor
        if parent_type == "database":
            response = retry_call(
                lambda: notion.databases.query(**payload),
                retries=3,
                base_delay=2.0,
                should_retry=_is_retryable_notion_error,
            )
        else:
            response = retry_call(
                lambda: notion.data_sources.query(**payload),
                retries=3,
                base_delay=2.0,
                should_retry=_is_retryable_notion_error,
            )
        pages.extend(response.get("results", []))
        if limit and len(pages) >= limit:
            return pages[:limit]
        if not response.get("has_more"):
            return pages
        cursor = response.get("next_cursor")


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
    if block_type == "to_do":
        return rich_text_to_plain(payload.get("rich_text", []))
    if block_type == "child_page":
        return payload.get("title", "")
    return ""


def render_blocks_markdown(notion: Client, blocks: List[Dict[str, Any]], indent: int = 0) -> List[str]:
    lines: List[str] = []
    for block in blocks:
        block_type = block.get("type", "")
        text = block_text(block).strip()
        prefix = ""

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
            prefix = "  " * indent + "- "
            lines.append(f"{prefix}{text}")
        elif block_type == "numbered_list_item":
            prefix = "  " * indent + "1. "
            lines.append(f"{prefix}{text}")
        elif block_type == "to_do":
            checked = block.get("to_do", {}).get("checked", False)
            prefix = "  " * indent + f"- [{'x' if checked else ' '}] "
            lines.append(f"{prefix}{text}")
        elif block_type == "quote":
            lines.append(f"> {text}")
        elif block_type == "callout":
            icon = ""
            callout = block.get("callout", {})
            if callout.get("icon", {}).get("emoji"):
                icon = callout["icon"]["emoji"] + " "
            lines.append(f"> {icon}{text}")
        elif block_type == "code":
            language = block.get("code", {}).get("language", "")
            lines.append(f"```{language}".rstrip())
            lines.append(text)
            lines.append("```")
        elif block_type == "divider":
            lines.append("---")
        else:
            if text:
                lines.append(text)

        if block.get("has_children"):
            children = fetch_children(notion, block["id"])
            child_lines = render_blocks_markdown(notion, children, indent + 1)
            if child_lines:
                lines.extend(child_lines)

    return lines


def fetch_page_markdown(notion: Client, page_id: str) -> str:
    blocks = fetch_children(notion, page_id)
    lines = render_blocks_markdown(notion, blocks)
    cleaned: List[str] = []
    previous_blank = False
    for line in lines:
        blank = not line.strip()
        if blank and previous_blank:
            continue
        cleaned.append(line)
        previous_blank = blank
    return "\n".join(cleaned).strip()


def safe_fetch_page_markdown(notion: Client, page_id: str, logger) -> str:
    try:
        return retry_call(
            lambda: fetch_page_markdown(notion, page_id),
            retries=3,
            base_delay=3.0,
            should_retry=_is_retryable_notion_error,
            on_retry=lambda exc, attempt, wait: logger.warning(
                "Healing Journal page render retry %s for %s after error: %s",
                attempt,
                page_id,
                exc,
            ),
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("Failed to fetch Notion page content for %s: %s", page_id, exc)
        return ""


def normalize_listish(value: Any) -> List[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return unique_list(str(item) for item in value if item is not None)
    text = str(value)
    if not text.strip():
        return []
    parts = [chunk.strip(" -") for chunk in text.replace("•", "\n").splitlines()]
    return unique_list(parts)


def build_entry(page: Dict[str, Any]) -> Dict[str, Any]:
    title = extract_field(page, "title") or "Healing Journal"
    raw_date_value = extract_field(page, "date")
    extracted_date = to_iso_date(raw_date_value)
    if extracted_date and not re.match(r"^\d{4}-\d{2}-\d{2}$", extracted_date):
        extracted_date = None
    if not extracted_date:
        title_match = re.search(r"(20\d{2})[/-](\d{2})[/-](\d{2})", title)
        if title_match:
            extracted_date = f"{title_match.group(1)}-{title_match.group(2)}-{title_match.group(3)}"
    if not extracted_date and raw_date_value:
        raw_date_text = str(raw_date_value)
        raw_match = re.search(r"(20\d{2})[/-](\d{2})[/-](\d{2})", raw_date_text)
        if raw_match:
            extracted_date = f"{raw_match.group(1)}-{raw_match.group(2)}-{raw_match.group(3)}"
    if not extracted_date and len(title) >= 10:
        extracted_date = title[:10]
    if extracted_date and not re.match(r"^\d{4}-\d{2}-\d{2}$", extracted_date):
        extracted_date = None
    return {
        "page_id": page["id"],
        "last_edited_time": page.get("last_edited_time", ""),
        "created_time": page.get("created_time", ""),
        "title": title,
        "date": extracted_date,
        "mood": extract_field(page, "mood"),
        "energy": as_int(extract_field(page, "energy")),
        "anxiety": as_int(extract_field(page, "anxiety")),
        "brain_fog": extract_field(page, "brain_fog"),
        "sleep": extract_field(page, "sleep"),
        "risk": extract_field(page, "risk"),
        "body_notes": normalize_space(str(extract_field(page, "body_notes") or "")),
        "stressors": normalize_listish(extract_field(page, "stressors")),
        "wins": normalize_listish(extract_field(page, "wins")),
        "tomorrow_top_3": normalize_listish(extract_field(page, "tomorrow_top_3")),
    }


def merge_entries(entries: List[Dict[str, Any]], page_markdowns: Dict[str, str]) -> Dict[str, Any]:
    ordered = sorted(
        entries,
        key=lambda item: (item.get("last_edited_time", ""), item.get("created_time", "")),
    )
    latest = ordered[-1]

    merged = {
        "date": latest["date"],
        "mood": latest.get("mood"),
        "energy": latest.get("energy"),
        "anxiety": latest.get("anxiety"),
        "brain_fog": latest.get("brain_fog"),
        "sleep": latest.get("sleep"),
        "risk": latest.get("risk"),
        "body_notes": "\n".join(unique_list(item["body_notes"] for item in ordered if item.get("body_notes"))),
        "stressors": unique_list(stressor for item in ordered for stressor in item.get("stressors", [])),
        "wins": unique_list(win for item in ordered for win in item.get("wins", [])),
        "tomorrow_top_3": unique_list(task for item in ordered for task in item.get("tomorrow_top_3", [])),
        "source_page_ids": [item["page_id"] for item in ordered],
    }

    sections: List[str] = []
    for item in ordered:
        title = normalize_space(str(item.get("title") or "Healing Journal"))
        content = page_markdowns.get(item["page_id"], "").strip()
        if len(ordered) > 1:
            sections.append(f"## Source Entry: {title}")
        if content:
            sections.append(content)
        else:
            sections.append("_No Notion page content available._")
    merged["content_markdown"] = "\n\n".join(section for section in sections if section.strip()).strip()
    return merged


def render_generated_body(entry: Dict[str, Any]) -> str:
    sections: List[str] = ["# 🌙 Healing Journal", "", entry["content_markdown"]]
    if entry.get("body_notes"):
        sections.extend(["", "## Body Notes", "", entry["body_notes"]])
    if entry.get("stressors"):
        sections.extend(["", "## Stressors", ""] + [f"- {item}" for item in entry["stressors"]])
    if entry.get("wins"):
        sections.extend(["", "## Wins", ""] + [f"- {item}" for item in entry["wins"]])
    if entry.get("tomorrow_top_3"):
        sections.extend(["", "## Tomorrow Top 3", ""] + [f"- {item}" for item in entry["tomorrow_top_3"]])
    sections.extend(["", "## Source Log", ""] + [f"- Notion page: `{page_id}`" for page_id in entry["source_page_ids"]])
    return "\n".join(sections).strip() + "\n"


def build_frontmatter(entry: Dict[str, Any]) -> Dict[str, Any]:
    data = {
        "date": entry["date"],
        "mood": entry.get("mood"),
        "energy": entry.get("energy"),
        "anxiety": entry.get("anxiety"),
        "brain_fog": entry.get("brain_fog"),
        "sleep": entry.get("sleep"),
        "risk": entry.get("risk"),
        "body_notes": entry.get("body_notes") or "",
        "stressors": entry.get("stressors") or [],
        "wins": entry.get("wins") or [],
        "tomorrow_top_3": entry.get("tomorrow_top_3") or [],
        "source_page_ids": entry.get("source_page_ids") or [],
        "tags": ["healing-journal", "mental-health", "doraos"],
    }
    return {key: value for key, value in data.items() if value not in (None, "", [])}


def upsert_markdown_file(path: Path, metadata: Dict[str, Any], generated_body: str, dry_run: bool) -> None:
    manual_section = "\n## Manual Notes\n\n"
    if path.exists():
        existing = frontmatter.load(path)
        _, manual_body = split_generated_and_manual(existing.content)
        merged_metadata = {**existing.metadata, **metadata}
        manual_content = manual_body.strip()
        if "## Manual Notes" not in manual_content:
            if manual_content:
                manual_content = f"{manual_section}{manual_content}\n"
            else:
                manual_content = manual_section
        content = wrap_generated(generated_body) + "\n\n" + manual_content.strip() + "\n"
        post = frontmatter.Post(content, **merged_metadata)
    else:
        content = wrap_generated(generated_body) + manual_section + "\n"
        post = frontmatter.Post(content, **metadata)

    if not dry_run:
        ensure_dir(path.parent)
        path.write_text(frontmatter.dumps(post), encoding="utf-8")


def write_quality_report(vault_path: Path, skipped_entries: List[Dict[str, str]], dry_run: bool) -> None:
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    report_path = vault_path / QUALITY_REPORT_PATH
    lines = [
        "# Healing Journal Data Quality",
        "",
        f"- updated: {now}",
        f"- missing_date_count: {len(skipped_entries)}",
        "",
        "## Missing Date",
        "",
    ]
    if not skipped_entries:
        lines.append("- none")
    else:
        lines.append("These Notion journal rows were skipped because DoraOS could not infer a valid YYYY-MM-DD date.")
        lines.append("")
        for entry in skipped_entries:
            lines.append(f"- `{entry['page_id']}` | title: {entry['title']} | last edited: {entry['last_edited_time']}")
    lines.append("")
    if not dry_run:
        ensure_dir(report_path.parent)
        report_path.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    args = parse_args()
    env = load_env_file(Path(args.env_file).expanduser())
    notion_token = require_env(env, "NOTION_API_KEY")
    database_id = args.database_id or require_env(env, "NOTION_HEALING_DB_ID")
    vault_path = Path(args.vault_path or require_env(env, "OBSIDIAN_VAULT_PATH")).expanduser()

    logger = build_logger("healing_journal_sync", LOG_DIR / "healing_journal_sync.log", args.verbose)
    state = read_json(STATE_FILE, {"dates": {}, "last_run": None})
    network_timeout = float(env.get("NETWORK_WARMUP_TIMEOUT", "120") or "120")
    wait_for_network(timeout=network_timeout, logger=logger)

    notion = Client(auth=notion_token)
    pages = paginate_database(notion, database_id, args.limit)
    logger.info("Fetched %s Notion journal rows", len(pages))

    grouped: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    skipped_pages = 0
    skipped_entries: List[Dict[str, str]] = []
    for page in pages:
        entry = build_entry(page)
        if not entry["date"]:
            skipped_pages += 1
            logger.warning("Skipping page %s because Date is missing", page["id"])
            skipped_entries.append(
                {
                    "page_id": page["id"],
                    "title": normalize_space(str(entry.get("title") or "Untitled")),
                    "last_edited_time": page.get("last_edited_time", ""),
                }
            )
            continue
        grouped[entry["date"]].append(entry)

    output_root = vault_path / OUTPUT_DIR
    ensure_dir(output_root)
    ensure_dir(STATE_DIR)

    changed_dates: List[str] = []
    for journal_date, entries in grouped.items():
        signature = hash_signature(
            [{"page_id": item["page_id"], "last_edited_time": item["last_edited_time"]} for item in entries]
        )
        output_file = str(output_root / f"{journal_date}.md")
        known = state.get("dates", {}).get(journal_date, {})
        if args.force or known.get("signature") != signature or known.get("output_file") != output_file:
            changed_dates.append(journal_date)

    logger.info("Detected %s changed journal day(s)", len(changed_dates))

    written = 0
    page_fetch_failures = 0
    for journal_date in sorted(changed_dates):
        entries = grouped[journal_date]
        page_markdowns: Dict[str, str] = {}
        for entry in entries:
            markdown = safe_fetch_page_markdown(notion, entry["page_id"], logger)
            if not markdown:
                page_fetch_failures += 1
            page_markdowns[entry["page_id"]] = markdown
        merged = merge_entries(entries, page_markdowns)
        metadata = build_frontmatter(merged)
        generated_body = render_generated_body(merged)
        target = output_root / f"{journal_date}.md"
        upsert_markdown_file(target, metadata, generated_body, args.dry_run)
        written += 1
        signature = hash_signature(
            [{"page_id": item["page_id"], "last_edited_time": item["last_edited_time"]} for item in entries]
        )
        state.setdefault("dates", {})[journal_date] = {
            "signature": signature,
            "output_file": str(target),
            "page_ids": [item["page_id"] for item in entries],
            "updated_at": datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z"),
        }
        logger.info("Synced journal file: %s", target)

    state["last_run"] = datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")
    if not args.dry_run:
        write_json(STATE_FILE, state)
        write_quality_report(vault_path, skipped_entries, args.dry_run)

    logger.info(
        "Done. written=%s skipped_missing_date=%s page_fetch_failures=%s dry_run=%s",
        written,
        skipped_pages,
        page_fetch_failures,
        args.dry_run,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
