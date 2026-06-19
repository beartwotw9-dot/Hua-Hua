#!/usr/bin/env python3
"""Ensure today's Notion Morning Brief Archive row exists.

This is a guardrail, not the long-form editorial brief.  It prevents the daily
Notion board from silently skipping a date when the richer Codex automation
misses its write-back step.
"""

from __future__ import annotations

import argparse
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

from notion_client import Client

from common import DEFAULT_ENV_FILE, LOG_DIR, build_logger, load_env_file, normalize_space, require_env, wait_for_network


MORNING_BRIEF_DATA_SOURCE_ID = "34d76488-1ec8-81e3-8fa5-000b034af734"


def _read_text(path: Path) -> str:
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8", errors="ignore")


def _read_scalar(text: str, label: str) -> str:
    for line in text.splitlines():
        if line.strip().startswith(label):
            return line.split(":", 1)[1].strip() if ":" in line else ""
    return ""


def _extract_headings(text: str, limit: int = 4) -> list[str]:
    headings: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("### "):
            headings.append(stripped[4:].strip())
        if len(headings) >= limit:
            break
    return headings


def _extract_check_items(text: str, limit: int = 8) -> list[str]:
    items: list[str] = []
    in_section = False
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("## 提醒清單") or stripped.startswith("## Reminder List"):
            in_section = True
            continue
        if in_section and stripped.startswith("## "):
            break
        if in_section and stripped.startswith("- ["):
            items.append(stripped[5:].strip())
        if len(items) >= limit:
            break
    return items


def _fallback_markers(text: str) -> list[str]:
    markers = []
    for marker in ("status: fallback", "status: api-failed", "status: api-disabled", "status: auth-required", "status: missing-live-data"):
        if marker in text:
            markers.append(marker.removeprefix("status: "))
    return markers


def _source_unavailable_summary(source: str, markers: list[str], noun: str) -> str:
    marker_text = ", ".join(markers) if markers else "unavailable"
    return f"{source} source status={marker_text}; no live {noun} were written. Blank does not mean none."


def _has_hard_failure(markers: list[str]) -> bool:
    return any(marker in {"api-failed", "api-disabled", "auth-required", "missing-live-data"} for marker in markers)


def _weather_fallback(today: str) -> str:
    if today == "2026-06-12":
        return (
            "fallback source: wttr.in Taipei. 台北目前約 25°C，體感約 28°C，濕度 89%，多雲；"
            "今日區間約 21-23°C，中午有輕雨訊號，傍晚仍可能毛毛雨。帶傘，外出保留移動緩衝。"
        )
    return "天氣 fallback 尚未寫入；請補最新可用天氣來源，不要留下空白。"


def _market_fallback(today: str) -> str:
    if today == "2026-06-12":
        return (
            "fallback source: TWSE latest available 2026-06-11 + AP 2026-06-11 US close. "
            "台股加權指數 43,149.46，跌 76.08 點，-0.18%；6/12 TWSE 指數表查詢時尚無資料。"
            "美股 6/11 強彈：S&P 500 +1.8%、Dow +1.9%、Nasdaq +2.5%。今天只做風險掃描，不追價。"
        )
    return "市場 fallback 尚未寫入；請補最近可用市場資料，不要留下空白。"


def _clip(text: str, limit: int = 420) -> str:
    compact = normalize_space(text)
    if len(compact) <= limit:
        return compact
    return compact[: limit - 1].rstrip() + "…"


def _extract_section(text: str, heading: str, limit: int = 900) -> str:
    lines = text.splitlines()
    in_section = False
    collected: list[str] = []
    for line in lines:
        stripped = line.strip()
        if stripped == heading:
            in_section = True
            continue
        if in_section and stripped.startswith("## "):
            break
        if in_section:
            collected.append(stripped)
    body = "\n".join(line for line in collected if line)
    return _clip(body, limit=limit) if body else ""


def _extract_list_section(text: str, heading: str, limit: int = 420) -> str:
    section = _extract_section(text, heading, limit=900)
    if not section:
        return ""
    lines = []
    for line in section.splitlines():
        stripped = line.strip()
        if stripped.startswith("- "):
            lines.append(stripped[2:].strip())
    return _clip(" ".join(lines) if lines else section, limit=limit)


def build_properties(vault_path: Path, today: str, generated_at: str) -> Dict[str, str]:
    feed_dir = vault_path / "Resources" / "Operating Feed"
    gmail_text = _read_text(feed_dir / f"{today} Gmail Digest.md")
    calendar_text = _read_text(feed_dir / f"{today} Calendar View.md")
    tasks_text = _read_text(feed_dir / f"{today} Google Tasks.md")
    op_text = _read_text(feed_dir / f"{today} Operating Feed.md")
    weather_text = _read_text(feed_dir / f"{today} Weather.md")
    market_text = _read_text(feed_dir / f"{today} Market News.md")
    api_text = _read_text(feed_dir / "API Health.md")

    gmail_count = _read_scalar(gmail_text, "- 郵件數量 | messages") or _read_scalar(gmail_text, "- messages")
    calendar_count = _read_scalar(calendar_text, "- 事件數量 | events") or _read_scalar(calendar_text, "- events")
    tasks_count = _read_scalar(tasks_text, "- 任務數量 | tasks") or _read_scalar(tasks_text, "- tasks")

    calendar_events = _extract_headings(calendar_text, limit=4)
    email_titles = _extract_headings(gmail_text, limit=4)
    task_items = _extract_check_items(tasks_text, limit=6)
    gmail_flags = _fallback_markers(gmail_text)
    calendar_flags = _fallback_markers(calendar_text)
    tasks_flags = _fallback_markers(tasks_text)
    op_flags = _fallback_markers(op_text)
    source_flags = {
        "Gmail": gmail_flags,
        "Calendar": calendar_flags,
        "Google Tasks": tasks_flags,
        "Operating Feed": op_flags,
    }
    flagged = [f"{name}={','.join(values)}" for name, values in source_flags.items() if values]
    api_date = _read_scalar(api_text, "- 日期 | date")

    if flagged:
        analysis = f"Morning guard 建立/更新；來源仍有 fallback：{'; '.join(flagged)}。"
    elif api_date == today:
        analysis = "Morning guard 建立/更新；本機 API Health 為今日，Gmail / Calendar / Google Tasks / Operating Feed 已有今日資料。"
    else:
        analysis = "Morning guard 建立/更新；本機資料存在，但 API Health 尚未確認為今日。"

    if calendar_flags:
        calendar_summary = _source_unavailable_summary("Google Calendar", calendar_flags, "events")
    elif calendar_events:
        calendar_summary = f"{calendar_count or len(calendar_events)} events: " + "；".join(calendar_events)
    else:
        calendar_summary = f"{calendar_count or 0} events."

    if gmail_flags and not email_titles and _has_hard_failure(gmail_flags):
        important_emails = _source_unavailable_summary("Gmail", gmail_flags, "messages")
    elif email_titles:
        source_mode = "fallback snapshot" if gmail_flags else "local/live page"
        important_emails = f"Gmail {source_mode} shows {gmail_count or len(email_titles)} messages. " + "；".join(email_titles)
    else:
        important_emails = f"Gmail local/live page shows {gmail_count or 0} messages."

    if tasks_flags:
        tasks_summary = _source_unavailable_summary("Google Tasks", tasks_flags, "reminders")
    elif task_items:
        tasks_summary = f"Google Tasks: {tasks_count or len(task_items)} open reminders. " + "；".join(task_items)
    else:
        tasks_summary = f"Google Tasks: {tasks_count or 0} open reminders."
    today_top_3 = _extract_list_section(op_text, "## Today Top 3") or _clip(
        f"1. 看 Calendar / Gmail 摘要。 2. 確認 Google Tasks 狀態：{tasks_summary} 3. 再決定今天真正要做的一件事。"
    )
    weather_summary = _extract_section(weather_text, "## Weather | 台北天氣") or _extract_section(op_text, "## Weather | 台北天氣") or _weather_fallback(today)
    market_summary = _extract_section(market_text, "## News Radar | 今日新聞") or _extract_section(op_text, "## News Radar | 今日新聞") or _market_fallback(today)

    return {
        "Date 日期": today,
        "AI Analysis 我的分析": _clip(f"{analysis} generated={generated_at}"),
        "Calendar Summary 行程摘要": _clip(calendar_summary),
        "Founder Note Founder 提醒": _clip("先看 Today Top 3；只挑真正要承諾的事進 Today Manual Tasks。"),
        "Important Emails 重要信件": _clip(important_emails),
        "Market News 金融新聞": _clip(market_summary),
        "TOEIC Daily 多益每日練習": _clip("Word: verify. Sentence: Please verify that today's brief exists before starting work."),
        "Today Top 3 今日三大重點": _clip(today_top_3),
        "Weather 天氣": _clip(weather_summary),
    }


def find_existing_page(notion: Client, data_source_id: str, today: str) -> str | None:
    payload = {
        "filter": {
            "property": "Date 日期",
            "title": {
                "contains": today,
            },
        },
        "page_size": 5,
    }
    response = notion.data_sources.query(data_source_id=data_source_id, **payload)
    for page in response.get("results", []) or []:
        title_items = page.get("properties", {}).get("Date 日期", {}).get("title", []) or []
        title = "".join(item.get("plain_text", "") for item in title_items)
        if today in title:
            return page.get("id")
    return None


def create_or_update_page(notion: Client, data_source_id: str, properties: Dict[str, str], content: str, dry_run: bool, logger) -> str:
    today_match = re.search(r"\d{4}-\d{2}-\d{2}", properties["Date 日期"])
    if not today_match:
        raise ValueError("Date property does not include an ISO date.")
    today = today_match.group(0)
    existing_page_id = find_existing_page(notion, data_source_id, today)
    if dry_run:
        action = "update" if existing_page_id else "create"
        return f"dry_run action={action} date={today}"

    notion_properties = {name: {"rich_text": [{"text": {"content": value}}]} for name, value in properties.items() if name != "Date 日期"}
    notion_properties["Date 日期"] = {"title": [{"text": {"content": properties["Date 日期"]}}]}

    if existing_page_id:
        notion.pages.update(page_id=existing_page_id, properties=notion_properties)
        logger.info("Updated existing Morning Brief row for %s: %s", today, existing_page_id)
        return existing_page_id

    page = notion.pages.create(
        parent={"data_source_id": data_source_id},
        icon={"type": "emoji", "emoji": "☀️"},
        properties=notion_properties,
        children=[
            {
                "object": "block",
                "type": "paragraph",
                "paragraph": {"rich_text": [{"type": "text", "text": {"content": content}}]},
            }
        ],
    )
    page_id = page.get("id", "")
    logger.info("Created Morning Brief row for %s: %s", today, page_id)
    return page_id


def main() -> int:
    parser = argparse.ArgumentParser(description="Ensure today's Notion Morning Brief row exists.")
    parser.add_argument("--env-file", default=str(DEFAULT_ENV_FILE))
    parser.add_argument("--data-source-id", default=MORNING_BRIEF_DATA_SOURCE_ID)
    parser.add_argument("--date", help="ISO date to sync, defaults to today.")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    env = load_env_file(Path(args.env_file))
    logger = build_logger("doraos.morning_brief_notion_sync", LOG_DIR / "dora_morning_brief_notion_sync.log", verbose=args.verbose)
    wait_for_network(timeout=float(env.get("NETWORK_WARMUP_TIMEOUT", "120") or "120"), logger=logger)

    notion_token = require_env(env, "NOTION_API_KEY")
    vault_path = Path(require_env(env, "OBSIDIAN_VAULT_PATH")).expanduser()
    today = args.date or datetime.now().strftime("%Y-%m-%d")
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", today):
        raise ValueError("--date must use YYYY-MM-DD.")
    generated_at = datetime.now().strftime("%Y-%m-%d %H:%M")
    properties = build_properties(vault_path, today, generated_at)
    content = f"Morning guard synced this row from local DoraOS outputs at {generated_at}. See Obsidian Today Hub for full source context."

    notion = Client(auth=notion_token)
    page_id = create_or_update_page(notion, args.data_source_id, properties, content, args.dry_run, logger)
    print(f"morning_brief_page={page_id} date={today}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
