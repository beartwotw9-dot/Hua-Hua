#!/usr/bin/env python3
"""Send one daily DoraOS morning brief to LINE."""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import urllib.request
from datetime import datetime
from pathlib import Path

from common import DEFAULT_ENV_FILE, LOG_DIR, build_logger, ensure_dir, load_env_file, require_env, today_stamp


LINE_PUSH_URL = "https://api.line.me/v2/bot/message/push"
MAX_LINE_TEXT_CHARS = 4500
MAX_LINE_MESSAGES_PER_PUSH = 5


def _state_path(config: dict[str, str]) -> Path:
    configured = config.get("LINE_DAILY_BRIEF_STATE_PATH", "").strip()
    if configured:
        return Path(configured).expanduser()
    return Path(__file__).resolve().parents[1] / "state" / "line_daily_brief.sqlite"


def init_state(path: Path) -> sqlite3.Connection:
    ensure_dir(path.parent)
    conn = sqlite3.connect(path)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS sent_daily_line_briefs (
            brief_date TEXT PRIMARY KEY,
            content_hash TEXT NOT NULL,
            sent_at TEXT NOT NULL
        )
        """
    )
    return conn


def already_sent(conn: sqlite3.Connection, brief_date: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sent_daily_line_briefs WHERE brief_date = ?",
        (brief_date,),
    ).fetchone()
    return row is not None


def mark_sent(conn: sqlite3.Connection, brief_date: str, body: str) -> None:
    digest = hashlib.sha256(body.encode("utf-8")).hexdigest()[:24]
    conn.execute(
        """
        INSERT OR REPLACE INTO sent_daily_line_briefs
            (brief_date, content_hash, sent_at)
        VALUES (?1, ?2, ?3)
        """,
        (brief_date, digest, datetime.now().isoformat(timespec="seconds")),
    )
    conn.commit()


def _vault(config: dict[str, str]) -> Path:
    return Path(require_env(config, "OBSIDIAN_VAULT_PATH")).expanduser()


def read_text(path: Path) -> str:
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8", errors="ignore")


def clip_preserve_lines(text: str, limit: int = MAX_LINE_TEXT_CHARS) -> str:
    compact_lines = [line.rstrip() for line in str(text or "").splitlines()]
    cleaned = "\n".join(compact_lines).strip()
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: limit - 12].rstrip() + "\n…"


def split_line_messages(text: str, limit: int = MAX_LINE_TEXT_CHARS) -> list[str]:
    lines = str(text or "").splitlines()
    chunks: list[str] = []
    current: list[str] = []
    current_len = 0
    for line in lines:
        line_len = len(line) + 1
        if current and current_len + line_len > limit:
            chunks.append("\n".join(current).strip())
            current = []
            current_len = 0
        if line_len > limit:
            chunks.append(clip_preserve_lines(line, limit))
            continue
        current.append(line)
        current_len += line_len
    if current:
        chunks.append("\n".join(current).strip())
    if len(chunks) > MAX_LINE_MESSAGES_PER_PUSH:
        kept = chunks[:MAX_LINE_MESSAGES_PER_PUSH]
        kept[-1] = clip_preserve_lines(kept[-1] + "\n\n…內容過長，剩餘項目請看 Today Hub。", limit)
        return kept
    return chunks or [""]


def clip_item(text: str, limit: int = 52) -> str:
    value = " ".join(str(text or "").split())
    if len(value) <= limit:
        return value
    return value[: limit - 1].rstrip() + "…"


def extract_meta_value(text: str, label: str) -> str:
    prefix = f"- {label}"
    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith(prefix) and ":" in line:
            return line.split(":", 1)[1].strip().strip("`")
    return ""


def extract_count_value(text: str, label: str) -> str:
    raw = extract_meta_value(text, label)
    if raw:
        return raw
    prefix = f"- {label}"
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith(prefix) and "|" in stripped and ":" in stripped:
            return stripped.split(":", 1)[1].strip().strip("`")
    return ""


def extract_bullets_after_heading(text: str, heading: str, limit: int = 8) -> list[str]:
    lines = text.splitlines()
    in_section = False
    bullets: list[str] = []
    for raw in lines:
        line = raw.rstrip()
        if line.strip() == heading:
            in_section = True
            continue
        if in_section and line.startswith("## "):
            break
        if in_section and line.strip().startswith("- "):
            bullets.append(line.strip()[2:].strip())
            if len(bullets) >= limit:
                break
    return bullets


def extract_news_radar(text: str, limit: int = 5) -> list[str]:
    lines = text.splitlines()
    in_section = False
    items: list[str] = []
    for raw in lines:
        line = raw.rstrip()
        stripped = line.strip()
        if stripped == "## News Radar | 今日新聞":
            in_section = True
            continue
        if in_section and stripped.startswith("## "):
            break
        if not in_section:
            continue
        if stripped.startswith("### "):
            items.append(stripped[4:].strip())
        elif stripped.startswith("- "):
            items.append(stripped[2:].strip())
        if len(items) >= limit:
            break
    return items


def extract_news_articles(text: str, limit: int = 3) -> list[dict[str, str]]:
    lines = text.splitlines()
    in_section = False
    articles: list[dict[str, str]] = []
    current: dict[str, str] | None = None
    for raw in lines:
        stripped = raw.strip()
        if stripped == "## News Radar | 今日新聞":
            in_section = True
            continue
        if in_section and stripped.startswith("## "):
            break
        if not in_section:
            continue
        if stripped.startswith("### "):
            if current:
                articles.append(current)
                if len(articles) >= limit:
                    return articles
            current = {"title": stripped[4:].strip(), "point": "", "summary": "", "source": ""}
            continue
        if current and stripped.startswith("- 重點："):
            current["point"] = stripped.replace("- 重點：", "", 1).strip()
        elif current and stripped.startswith("- 摘要："):
            current["summary"] = stripped.replace("- 摘要：", "", 1).strip()
        elif current and stripped.startswith("- 來源："):
            current["source"] = stripped.replace("- 來源：", "", 1).strip()
    if current and len(articles) < limit:
        articles.append(current)
    return articles[:limit]


def extract_calendar_events(text: str) -> list[str]:
    events: list[str] = []
    title = ""
    details: list[str] = []
    for raw in text.splitlines():
        line = raw.rstrip()
        if line.startswith("### "):
            if title:
                events.append(format_calendar_event(title, details))
            title = line[4:].strip()
            details = []
            continue
        if title and line.startswith("- "):
            details.append(line[2:].strip())
    if title:
        events.append(format_calendar_event(title, details))
    return events


def format_calendar_event(title: str, details: list[str]) -> str:
    start = ""
    end = ""
    location = ""
    for item in details:
        if item.startswith("開始 | start:"):
            start = item.split(":", 1)[1].strip()
        elif item.startswith("結束 | end:"):
            end = item.split(":", 1)[1].strip()
        elif item.startswith("地點 | location:"):
            location = item.split(":", 1)[1].strip()
    when = format_time_range(start, end)
    suffix = ""
    if location and location != "(none)" and not location.startswith("http"):
        suffix = f" · {clip_item(location, 22)}"
    return f"{clip_item(title, 34)}｜{when}{suffix}".strip("｜")


def format_time_range(start: str, end: str) -> str:
    start_time = short_time(start)
    end_time = short_time(end)
    if start_time and end_time and start_time != end_time:
        return f"{start_time}-{end_time}"
    return start_time or end_time


def short_time(value: str) -> str:
    if not value:
        return ""
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return value[:10] if len(value) >= 10 else value
    if parsed.hour == 0 and parsed.minute == 0:
        return parsed.strftime("%m/%d")
    return parsed.strftime("%H:%M")


def extract_tasks(text: str) -> list[str]:
    tasks: list[str] = []
    for raw in text.splitlines():
        line = raw.rstrip()
        if line.startswith("- [ ] "):
            tasks.append(line[6:].strip())
    return tasks


def extract_gmail_messages(text: str) -> list[dict[str, str]]:
    messages: list[dict[str, str]] = []
    current: dict[str, str] | None = None
    for raw in text.splitlines():
        line = raw.rstrip()
        stripped = line.strip()
        if stripped.startswith("### "):
            if current:
                messages.append(current)
            current = {"subject": stripped[4:].strip(), "from": "", "snippet": ""}
            continue
        if not current:
            continue
        if stripped.startswith("- 寄件者 | from:"):
            sender = stripped.split(":", 1)[1].strip()
            current["from"] = clip_sender(sender)
        elif stripped.startswith("- 摘要 | snippet:"):
            current["snippet"] = stripped.split(":", 1)[1].strip()
    if current:
        messages.append(current)
    return messages


def clip_sender(sender: str) -> str:
    value = sender
    if "<" in value:
        value = value.split("<", 1)[0].strip().strip('"')
    return clip_item(value, 22)


def clean_task(item: str) -> str:
    value = item
    value = value.replace("（no due date）", "")
    value = value.replace(" · 游欣樺's list", "")
    return clip_item(value.strip(), 80)


def short_alert(item: str) -> str:
    if "受影響來源" in item:
        left = item.split("|", 1)[0].strip()
        if ":" in left:
            return left
        return "受影響來源：Gmail, Research Digest"
    if "來源未取得" in item:
        return item.split("|", 1)[0].strip()
    return clip_item(item, 58)


def useful_suggestions(items: list[str]) -> list[str]:
    cleaned: list[str] = []
    for item in items:
        value = item.replace("Suggestion:", "").strip()
        if (
            "GitHub" in value
            or "Linear" in value
            or "Resources" in value
            or "Inspect `" in value
            or "Confirm `" in value
        ):
            continue
        cleaned.append(clip_item(value, 50))
    return cleaned[:3]


def build_daily_line_brief(config: dict[str, str]) -> tuple[str, str]:
    today, generated_at = today_stamp()
    vault = _vault(config)
    feed = vault / "Resources" / "Operating Feed"
    briefs = vault / "Resources" / "AI Briefs"

    today_hub = read_text(feed / "Today Hub.md")
    calendar = read_text(feed / f"{today} Calendar View.md")
    tasks = read_text(feed / f"{today} Google Tasks.md")
    market = read_text(feed / f"{today} Market News.md")
    daily_brief = read_text(briefs / f"{today} Daily Brief.md")
    gmail = read_text(feed / f"{today} Gmail Digest.md")

    alerts = extract_bullets_after_heading(today_hub, "## 今日來源警示 | Today's Source Alerts", 3)
    market_snapshot = extract_bullets_after_heading(market, "## Market Snapshot | 市場快照", 3)
    watchlist_lines = extract_bullets_after_heading(market, "## Watchlist | 追蹤股票", 9)
    news_articles = extract_news_articles(market, 3)
    priorities = extract_bullets_after_heading(daily_brief, "## 5. Suggested Priorities", 3)
    actions = extract_bullets_after_heading(daily_brief, "## 6. Suggested Actions", 3)
    calendar_events = extract_calendar_events(calendar)
    task_items = extract_tasks(tasks)
    gmail_messages = extract_gmail_messages(gmail)

    event_count = extract_meta_value(calendar, "事件數量 | events") or str(len(calendar_events))
    task_count = extract_meta_value(tasks, "任務數量 | tasks") or str(len(task_items))

    gmail_status = "尚未取得今日摘要"
    gmail_count = extract_count_value(gmail, "郵件數量 | messages")
    if gmail_count:
        gmail_status = f"{gmail_count} 封未讀"
    elif "今天沒有符合條件的郵件" in gmail or "No matching messages" in gmail:
        gmail_status = "今天沒有符合條件的郵件"
    elif "api-failed" in gmail or "Gmail API failed" in gmail:
        gmail_status = "抓取失敗，不代表沒有郵件"
    elif gmail:
        gmail_status = "已更新"

    all_tasks = [clean_task(item) for item in task_items]
    suggested_actions = useful_suggestions(actions)

    lines = [
        f"🌅 {today} Morning Page",
        f"更新 {generated_at[-5:]}",
        "",
        "📌 今日摘要",
        f"• 行程 {event_count} 件",
        f"• 待辦 {task_count} 件",
        f"• 信箱：{gmail_status}",
    ]

    if alerts:
        lines += ["", "⚠️ 來源狀態", *[f"• {short_alert(item)}" for item in alerts[:2]]]
    if market_snapshot:
        lines += ["", "📈 市場快照", *[f"• {clip_item(item, 68)}" for item in market_snapshot[:2]]]
    if watchlist_lines:
        lines += ["", "📊 追蹤股票", *[line.lstrip("- ").strip() for line in watchlist_lines[:8]]]
    if news_articles:
        lines += ["", "📰 每日新聞"]
        for idx, article in enumerate(news_articles, 1):
            title = article.get("title", "").split(".", 1)[-1].strip()
            point = article.get("point") or title
            summary = article.get("summary") or point
            source = article.get("source", "")
            lines += [
                f"{idx}. {clip_item(title, 72)}",
                f"   重點：{clip_item(point, 80)}",
                f"   摘要：{clip_item(summary, 110)}",
            ]
            if source:
                lines.append(f"   來源：{clip_item(source, 40)}")
    if gmail_messages:
        lines += ["", "📬 Gmail 要處理"]
        for idx, message in enumerate(gmail_messages, 1):
            subject = clip_item(message.get("subject", ""), 58)
            sender = message.get("from", "")
            if sender:
                lines.append(f"{idx}. {subject}｜{sender}")
            else:
                lines.append(f"{idx}. {subject}")
    if suggested_actions:
        lines += ["", "✅ 今日行動", *[f"☐ {item}" for item in suggested_actions]]
    else:
        lines += ["", "✅ 今日行動", "☐ 打開 Today Hub，先挑 1 件最重要的事", "☐ 晚上補一行今日回顧"]

    lines += ["", "🗓 今日行程"]
    if calendar_events:
        lines += [f"{idx}. {item}" for idx, item in enumerate(calendar_events, 1)]
    else:
        lines.append("• 今天沒有找到事件，或 Calendar 尚未同步")

    lines += ["", "☑️ 待辦提醒"]
    if all_tasks:
        lines += [f"☐ {item}" for item in all_tasks]
    else:
        lines.append("• 目前沒有找到未完成 Tasks，或尚未同步")

    lines += [
        "",
        "📝 晚間回顧",
        "今天完成了：",
        "今天卡住了：",
        "明天第一步：",
        "",
        "DoraOS → LINE",
    ]

    return today, "\n".join(lines).strip()


def push_line_message(token: str, to_id: str, text: str, timeout: int = 20) -> None:
    messages = [{"type": "text", "text": chunk} for chunk in split_line_messages(text)]
    payload = json.dumps(
        {"to": to_id, "messages": messages},
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


def main() -> int:
    parser = argparse.ArgumentParser(description="Send one daily DoraOS morning brief to LINE.")
    parser.add_argument("--env-file", default=str(DEFAULT_ENV_FILE))
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    logger = build_logger("doraos.line_daily_brief", LOG_DIR / "dora_line_daily_brief.log", verbose=args.verbose)
    config = load_env_file(Path(args.env_file))
    brief_date, body = build_daily_line_brief(config)

    if args.dry_run:
        print(body)
        return 0

    enabled = config.get("LINE_DAILY_BRIEF_ENABLED", "true").strip().lower() in {"1", "true", "yes"}
    token = config.get("LINE_CHANNEL_ACCESS_TOKEN", "").strip()
    to_id = config.get("LINE_TO_USER_ID", "").strip()
    if not enabled:
        logger.info("LINE daily brief disabled.")
        print(json.dumps({"sent": False, "reason": "disabled", "date": brief_date}, ensure_ascii=False))
        return 0
    if not token or not to_id:
        logger.warning("LINE daily brief missing credentials.")
        print(json.dumps({"sent": False, "reason": "missing_credentials", "date": brief_date}, ensure_ascii=False))
        return 0

    conn = init_state(_state_path(config))
    try:
        if already_sent(conn, brief_date) and not args.force:
            logger.info("LINE daily brief already sent for %s.", brief_date)
            print(json.dumps({"sent": False, "reason": "already_sent", "date": brief_date}, ensure_ascii=False))
            return 0
        push_line_message(token, to_id, body)
        mark_sent(conn, brief_date, body)
    finally:
        conn.close()

    logger.info("Pushed LINE daily brief for %s.", brief_date)
    print(json.dumps({"sent": True, "date": brief_date}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
