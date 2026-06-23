#!/usr/bin/env python3
"""Send today's Google Tasks reminder list to LINE as a separate message."""

from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import datetime
from pathlib import Path

from common import DEFAULT_ENV_FILE, LOG_DIR, build_logger, ensure_dir, load_env_file, today_stamp
from line_daily_brief import (
    _vault,
    clean_task,
    extract_meta_value,
    extract_tasks,
    push_line_message,
    read_text,
)


def tasks_source_failed(tasks_text: str) -> bool:
    return any(
        marker in tasks_text
        for marker in [
            "status: api-failed",
            "status: api-disabled",
            "status: auth-required",
            "Google Tasks API failed",
        ]
    )


def _state_path(config: dict[str, str]) -> Path:
    configured = config.get("LINE_TASKS_BRIEF_STATE_PATH", "").strip()
    if configured:
        return Path(configured).expanduser()
    return Path(__file__).resolve().parents[1] / "state" / "line_tasks_brief.sqlite"


def init_state(path: Path) -> sqlite3.Connection:
    ensure_dir(path.parent)
    conn = sqlite3.connect(path)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS sent_daily_line_tasks (
            brief_date TEXT PRIMARY KEY,
            task_count TEXT NOT NULL,
            sent_at TEXT NOT NULL
        )
        """
    )
    return conn


def already_sent(conn: sqlite3.Connection, brief_date: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sent_daily_line_tasks WHERE brief_date = ?",
        (brief_date,),
    ).fetchone()
    return row is not None


def mark_sent(conn: sqlite3.Connection, brief_date: str, task_count: str) -> None:
    conn.execute(
        """
        INSERT OR REPLACE INTO sent_daily_line_tasks
            (brief_date, task_count, sent_at)
        VALUES (?1, ?2, ?3)
        """,
        (brief_date, task_count, datetime.now().isoformat(timespec="seconds")),
    )
    conn.commit()


def build_tasks_line_brief(config: dict[str, str]) -> tuple[str, str, str]:
    today, generated_at = today_stamp()
    tasks_text = read_text(_vault(config) / "Resources" / "Operating Feed" / f"{today} Google Tasks.md")
    task_items = [clean_task(item) for item in extract_tasks(tasks_text)]
    task_count = extract_meta_value(tasks_text, "任務數量 | tasks") or str(len(task_items))

    lines = [
        f"☑️ {today} 今日待辦提醒",
        f"更新 {generated_at[-5:]}",
        f"待辦 {task_count} 件",
        "",
    ]

    if tasks_source_failed(tasks_text):
        lines += [
            "⚠️ Google Tasks 今天抓取失敗",
            "不要把這當成 0 件；請打開 Google Tasks 手動確認。",
        ]
    elif task_items:
        lines += [f"☐ {item}" for item in task_items]
    elif tasks_text:
        lines.append("目前沒有找到未完成 Google Tasks。")
    else:
        lines += [
            "⚠️ 今天還沒有 Google Tasks 同步檔",
            "請先跑 DoraOS daily pipeline 或手動檢查 Google Tasks。",
        ]

    lines += ["", "DoraOS Tasks → LINE"]
    return today, "\n".join(lines).strip(), task_count


def main() -> int:
    parser = argparse.ArgumentParser(description="Send today's Google Tasks reminder list to LINE.")
    parser.add_argument("--env-file", default=str(DEFAULT_ENV_FILE))
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--strict", action="store_true", help="Exit non-zero when no LINE message is actually pushed.")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    logger = build_logger("doraos.line_tasks_brief", LOG_DIR / "dora_line_tasks_brief.log", verbose=args.verbose)
    config = load_env_file(Path(args.env_file))
    brief_date, body, task_count = build_tasks_line_brief(config)
    tasks_text = read_text(_vault(config) / "Resources" / "Operating Feed" / f"{brief_date} Google Tasks.md")

    if args.dry_run:
        print(body)
        return 3 if args.strict and tasks_source_failed(tasks_text) else 0

    enabled = config.get("LINE_TASKS_BRIEF_ENABLED", "true").strip().lower() in {"1", "true", "yes"}
    token = config.get("LINE_CHANNEL_ACCESS_TOKEN", "").strip()
    to_id = config.get("LINE_TO_USER_ID", "").strip()
    if not enabled:
        logger.info("LINE tasks brief disabled.")
        print(json.dumps({"sent": False, "reason": "disabled", "date": brief_date}, ensure_ascii=False))
        return 2 if args.strict else 0
    if not token or not to_id:
        logger.warning("LINE tasks brief missing credentials.")
        print(json.dumps({"sent": False, "reason": "missing_credentials", "date": brief_date}, ensure_ascii=False))
        return 2 if args.strict else 0
    if args.strict and tasks_source_failed(tasks_text):
        logger.warning("LINE tasks brief blocked because Google Tasks source failed for %s.", brief_date)
        print(json.dumps({"sent": False, "reason": "tasks_source_failed", "date": brief_date}, ensure_ascii=False))
        return 3

    conn = init_state(_state_path(config))
    try:
        if already_sent(conn, brief_date) and not args.force:
            logger.info("LINE tasks brief already sent for %s.", brief_date)
            print(json.dumps({"sent": False, "reason": "already_sent", "date": brief_date}, ensure_ascii=False))
            return 0
        push_line_message(token, to_id, body)
        mark_sent(conn, brief_date, task_count)
    finally:
        conn.close()

    logger.info("Pushed LINE tasks brief for %s.", brief_date)
    print(json.dumps({"sent": True, "date": brief_date, "task_count": task_count}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
