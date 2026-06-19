#!/usr/bin/env python3
"""Route selected DoraOS/OpenHuman notifications to LINE Messaging API.

Sources are read-only. Delivery state is kept in a local SQLite outbox so
launchd can run this frequently without repeating the same LINE messages.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable

from common import DEFAULT_ENV_FILE, LOG_DIR, build_logger, clip_text, ensure_dir, load_env_file, require_env, today_stamp


LINE_PUSH_URL = "https://api.line.me/v2/bot/message/push"
DEFAULT_SOURCES = {"discord", "calendar", "tasks"}
DEFAULT_STATUSES = {"unread", "read", "acted"}


@dataclass(frozen=True)
class LineCandidate:
    source_key: str
    source: str
    title: str
    body: str


def _split_csv(value: str, default: set[str]) -> set[str]:
    items = {item.strip().lower() for item in value.split(",") if item.strip()}
    return items or set(default)


def _state_path(config: dict[str, str]) -> Path:
    configured = config.get("LINE_ROUTER_STATE_PATH", "").strip()
    if configured:
        return Path(configured).expanduser()
    return Path(__file__).resolve().parents[1] / "state" / "line_notification_router.sqlite"


def _openhuman_notification_db(config: dict[str, str]) -> Path:
    configured = config.get("OPENHUMAN_NOTIFICATIONS_DB", "").strip()
    if configured:
        return Path(configured).expanduser()

    root_value = config.get("OPENHUMAN_WORKSPACE", "").strip()
    if root_value and "your-user" not in root_value:
        root = Path(root_value).expanduser()
        direct = root / "notifications" / "notifications.db"
        nested = root / "workspace" / "notifications" / "notifications.db"
        if direct.exists():
            return direct
        if nested.exists():
            return nested
        return direct

    candidates = [
        Path.home() / ".openhuman" / "workspace" / "notifications" / "notifications.db",
        Path.home() / ".openhuman" / "notifications" / "notifications.db",
        Path.home() / "Library" / "Application Support" / "openhuman" / "openhuman" / "workspace" / "notifications" / "notifications.db",
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return candidates[0]


def init_outbox(path: Path) -> sqlite3.Connection:
    ensure_dir(path.parent)
    conn = sqlite3.connect(path)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS sent_line_notifications (
            source_key TEXT PRIMARY KEY,
            source TEXT NOT NULL,
            title TEXT NOT NULL,
            body TEXT NOT NULL,
            sent_at TEXT NOT NULL
        )
        """
    )
    return conn


def already_sent(conn: sqlite3.Connection, source_key: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sent_line_notifications WHERE source_key = ?",
        (source_key,),
    ).fetchone()
    return row is not None


def mark_sent(conn: sqlite3.Connection, item: LineCandidate) -> None:
    conn.execute(
        """
        INSERT OR IGNORE INTO sent_line_notifications
            (source_key, source, title, body, sent_at)
        VALUES (?1, ?2, ?3, ?4, ?5)
        """,
        (
            item.source_key,
            item.source,
            item.title,
            item.body,
            datetime.now().isoformat(timespec="seconds"),
        ),
    )
    conn.commit()


def make_key(source: str, *parts: str) -> str:
    digest = hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()[:24]
    return f"{source}:{digest}"


def read_discord_notifications(config: dict[str, str], limit: int, logger) -> list[LineCandidate]:
    db_path = _openhuman_notification_db(config)
    if not db_path.exists():
        logger.warning("OpenHuman notifications DB not found: %s", db_path)
        return []

    statuses = _split_csv(config.get("LINE_ROUTER_DISCORD_STATUSES", ""), DEFAULT_STATUSES)
    placeholders = ",".join("?" for _ in statuses)
    query = f"""
        SELECT id, title, body, status, received_at
        FROM integration_notifications
        WHERE provider = ? AND status IN ({placeholders})
        ORDER BY received_at ASC
        LIMIT ?
    """
    params = ["discord", *sorted(statuses), limit]

    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute(query, params).fetchall()
    finally:
        conn.close()

    items: list[LineCandidate] = []
    for row in rows:
        notification_id, title, body, status, received_at = row
        items.append(
            LineCandidate(
                source_key=f"discord:{notification_id}",
                source="discord",
                title=clip_text(title or "Discord notification", 120),
                body=clip_text(f"{body or ''}\n\nstatus: {status} · received: {received_at}", 900),
            )
        )
    return items


def _todays_feed_file(config: dict[str, str], suffix: str) -> Path:
    vault_path = Path(require_env(config, "OBSIDIAN_VAULT_PATH")).expanduser()
    today, _ = today_stamp()
    return vault_path / "Resources" / "Operating Feed" / f"{today} {suffix}"


def _section_blocks(text: str) -> list[tuple[str, list[str]]]:
    blocks: list[tuple[str, list[str]]] = []
    current_title = ""
    current_lines: list[str] = []
    for raw in text.splitlines():
        line = raw.rstrip()
        if line.startswith("### "):
            if current_title:
                blocks.append((current_title, current_lines))
            current_title = line[4:].strip()
            current_lines = []
            continue
        if current_title:
            current_lines.append(line)
    if current_title:
        blocks.append((current_title, current_lines))
    return blocks


def read_calendar_items(config: dict[str, str], logger) -> list[LineCandidate]:
    path = _todays_feed_file(config, "Calendar View.md")
    if not path.exists():
        logger.warning("Calendar view missing: %s", path)
        return []
    text = path.read_text(encoding="utf-8", errors="ignore")
    if "status: api-failed" in text or "Google Calendar API failed" in text:
        return [
            LineCandidate(
                source_key=make_key("calendar-status", today_stamp()[0], "api-failed"),
                source="calendar",
                title="DoraOS Calendar sync failed",
                body="今天 Google Calendar 沒有成功抓到 live events，DoraOS 已保留明確失敗狀態。",
            )
        ]

    items: list[LineCandidate] = []
    for title, lines in _section_blocks(text):
        body = "\n".join(line for line in lines if line.strip())
        items.append(
            LineCandidate(
                source_key=make_key("calendar", today_stamp()[0], title, body),
                source="calendar",
                title=clip_text(f"Calendar: {title}", 120),
                body=clip_text(body or "No details", 900),
            )
        )
    return items


def read_task_items(config: dict[str, str], logger) -> list[LineCandidate]:
    path = _todays_feed_file(config, "Google Tasks.md")
    if not path.exists():
        logger.warning("Google Tasks view missing: %s", path)
        return []
    text = path.read_text(encoding="utf-8", errors="ignore")
    if "status: api-failed" in text or "Google Tasks API failed" in text:
        return [
            LineCandidate(
                source_key=make_key("tasks-status", today_stamp()[0], "api-failed"),
                source="tasks",
                title="DoraOS Google Tasks sync failed",
                body="今天 Google Tasks 沒有成功抓到 live reminders，DoraOS 已保留明確失敗狀態。",
            )
        ]

    items: list[LineCandidate] = []
    lines = text.splitlines()
    index = 0
    while index < len(lines):
        line = lines[index].rstrip()
        if not line.startswith("- [ ] "):
            index += 1
            continue
        title = line[6:].strip()
        notes: list[str] = []
        cursor = index + 1
        while cursor < len(lines) and lines[cursor].startswith("  - "):
            notes.append(lines[cursor].strip())
            cursor += 1
        body = "\n".join([title, *notes])
        items.append(
            LineCandidate(
                source_key=make_key("tasks", today_stamp()[0], body),
                source="tasks",
                title=clip_text(f"Task: {title}", 120),
                body=clip_text("\n".join(notes) if notes else "No notes", 900),
            )
        )
        index = cursor
    return items


def collect_candidates(config: dict[str, str], logger) -> list[LineCandidate]:
    sources = _split_csv(config.get("LINE_ROUTER_SOURCES", ""), DEFAULT_SOURCES)
    limit = int(config.get("LINE_ROUTER_MAX_ITEMS_PER_RUN", "") or "200")
    candidates: list[LineCandidate] = []
    if "discord" in sources:
        candidates.extend(read_discord_notifications(config, limit, logger))
    if "calendar" in sources:
        candidates.extend(read_calendar_items(config, logger))
    if "tasks" in sources:
        candidates.extend(read_task_items(config, logger))
    return candidates[:limit]


def format_line_message(item: LineCandidate) -> str:
    labels = {
        "discord": "Discord",
        "calendar": "Calendar",
        "tasks": "Tasks",
    }
    label = labels.get(item.source, item.source)
    return clip_text(f"[{label}] {item.title}\n\n{item.body}", 1800)


def push_line_message(token: str, to_id: str, text: str, timeout: int = 20) -> None:
    payload = json.dumps(
        {
            "to": to_id,
            "messages": [{"type": "text", "text": text}],
        },
        ensure_ascii=False,
    ).encode("utf-8")
    request = urllib.request.Request(
        LINE_PUSH_URL,
        data=payload,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}",
        },
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        if response.status >= 300:
            raise RuntimeError(f"LINE push failed with HTTP {response.status}")


def route_items(
    items: Iterable[LineCandidate],
    config: dict[str, str],
    *,
    dry_run: bool,
    logger,
) -> tuple[int, int]:
    conn = init_outbox(_state_path(config))
    token = config.get("LINE_CHANNEL_ACCESS_TOKEN", "").strip()
    to_id = config.get("LINE_TO_USER_ID", "").strip()
    delivery_enabled = config.get("LINE_ROUTER_ENABLED", "false").strip().lower() in {"1", "true", "yes"}
    sent = 0
    skipped = 0

    try:
        for item in items:
            if already_sent(conn, item.source_key):
                skipped += 1
                continue
            message = format_line_message(item)
            if dry_run:
                print(f"--- {item.source_key} ---\n{message}\n")
                logger.info("Dry-run LINE route for %s", item.source_key)
                sent += 1
                continue
            if not delivery_enabled or not token or not to_id:
                logger.warning("LINE router disabled or missing credentials; not pushing %s", item.source_key)
                skipped += 1
                continue
            else:
                push_line_message(token, to_id, message)
                logger.info("Pushed LINE notification for %s", item.source_key)
                mark_sent(conn, item)
                sent += 1
    finally:
        conn.close()
    return sent, skipped


def main() -> int:
    parser = argparse.ArgumentParser(description="Route Discord, Calendar, and Google Tasks notifications to LINE.")
    parser.add_argument("--env-file", default=str(DEFAULT_ENV_FILE))
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    logger = build_logger("doraos.line_notification_router", LOG_DIR / "dora_line_notification_router.log", verbose=args.verbose)
    config = load_env_file(Path(args.env_file))
    items = collect_candidates(config, logger)
    sent, skipped = route_items(items, config, dry_run=args.dry_run, logger=logger)
    logger.info("LINE router complete: routed=%s skipped=%s candidates=%s", sent, skipped, len(items))
    print(json.dumps({"routed": sent, "skipped": skipped, "candidates": len(items)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
