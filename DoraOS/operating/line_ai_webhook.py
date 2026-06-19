#!/usr/bin/env python3
"""Interactive LINE webhook for DoraOS.

The default path is read-only: it answers from DoraOS context without mutating
Gmail, Calendar, Tasks, Notion, or Obsidian. Notion writes are allowed only when
explicitly enabled and the LINE message uses a capture command.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import json
import sys
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, PlainTextResponse

from common import DEFAULT_ENV_FILE, LOG_DIR, build_logger, clip_text, load_env_file, require_env, today_stamp
from line_daily_brief import build_daily_line_brief, split_line_messages


LINE_REPLY_URL = "https://api.line.me/v2/bot/message/reply"
NOTION_VERSION = "2022-06-28"
MAX_CONTEXT_CHARS = 9000
MAX_REPLY_CHARS = 4200


CONFIG = load_env_file(DEFAULT_ENV_FILE)
LOGGER = build_logger("doraos.line_ai_webhook", LOG_DIR / "dora_line_ai_webhook.log")
app = FastAPI(title="DoraOS LINE AI Webhook")


def _reload_config(env_file: str | Path = DEFAULT_ENV_FILE) -> dict[str, str]:
    global CONFIG
    CONFIG = load_env_file(Path(env_file))
    return CONFIG


def _vault(config: dict[str, str]) -> Path:
    return Path(require_env(config, "OBSIDIAN_VAULT_PATH")).expanduser()


def _read(path: Path, limit: int = 2200) -> str:
    if not path.exists():
        return ""
    return clip_text(path.read_text(encoding="utf-8", errors="ignore"), limit)


def _clip_preserve_lines(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text.strip()
    return text[: max(0, limit - 1)].rstrip() + "…"


def _today_context(config: dict[str, str]) -> str:
    today, generated_at = today_stamp()
    vault = _vault(config)
    feed = vault / "Resources" / "Operating Feed"
    briefs = vault / "Resources" / "AI Briefs"
    parts = [
        f"# DoraOS Context {today} ({generated_at})",
        "",
        "## Today Hub",
        _read(feed / "Today Hub.md", 1800),
        "",
        "## Calendar",
        _read(feed / f"{today} Calendar View.md", 1500),
        "",
        "## Google Tasks",
        _read(feed / f"{today} Google Tasks.md", 1700),
        "",
        "## Gmail",
        _read(feed / f"{today} Gmail Digest.md", 2200),
        "",
        "## Market / News",
        _read(feed / f"{today} Market News.md", 1800),
        "",
        "## Daily Brief",
        _read(briefs / f"{today} Daily Brief.md", 1200),
    ]
    return clip_text("\n".join(parts), MAX_CONTEXT_CHARS)


def _system_prompt() -> str:
    return (
        "你是 DoraOS 內的 LINE AI 助理，語氣像 Codex：自然繁中、溫暖、直接、聰明但不官腔。"
        "你要根據 DoraOS 今日上下文回答，不要假裝知道不存在的資料。"
        "如果使用者問今天、信箱、待辦、行程、股票、新聞，就優先使用提供的上下文。"
        "回答要適合 LINE：短、分段、可直接行動。"
        "不要洩漏 token、email 地址、隱私原文；必要時只用壓縮摘要。"
        "你不能送 email、改 Calendar、改 Tasks。"
        "只有使用者明確要求「存到 Notion」時，系統才會另外處理 Notion 寫入；一般回答不要假裝已經寫入。"
    )


def _is_enabled(config: dict[str, str], key: str) -> bool:
    return config.get(key, "false").strip().lower() in {"1", "true", "yes", "on"}


def _notion_headers(config: dict[str, str]) -> dict[str, str]:
    token = require_env(config, "NOTION_API_KEY")
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Notion-Version": NOTION_VERSION,
    }


def _notion_request(config: dict[str, str], method: str, path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    data = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        f"https://api.notion.com/v1{path}",
        data=data,
        headers=_notion_headers(config),
        method=method,
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            body = response.read().decode("utf-8") or "{}"
            return json.loads(body)
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Notion HTTP {exc.code}: {body}") from exc


def _notion_database_schema(config: dict[str, str], database_id: str) -> dict[str, Any]:
    return _notion_request(config, "GET", f"/databases/{database_id}")


def _rich_text(text: str, limit: int = 1900) -> list[dict[str, Any]]:
    chunks: list[dict[str, Any]] = []
    remaining = text.strip()
    while remaining and len(chunks) < 8:
        chunk = remaining[:limit]
        remaining = remaining[limit:]
        chunks.append({"type": "text", "text": {"content": chunk}})
    return chunks or [{"type": "text", "text": {"content": ""}}]


def _pick_property(properties: dict[str, Any], names: list[str], allowed_types: set[str]) -> tuple[str, str] | None:
    for name in names:
        prop = properties.get(name)
        if prop and prop.get("type") in allowed_types:
            return name, prop["type"]
    for name, prop in properties.items():
        if prop.get("type") in allowed_types:
            return name, prop["type"]
    return None


def _set_optional_property(properties: dict[str, Any], db_props: dict[str, Any], names: list[str], value: str) -> None:
    picked = None
    for name in names:
        prop = db_props.get(name)
        if prop and prop.get("type") in {"rich_text", "select", "multi_select", "status", "url", "date"}:
            picked = (name, prop["type"], prop)
            break
    if not picked:
        return
    name, prop_type, prop = picked
    if prop_type == "rich_text":
        properties[name] = {"rich_text": _rich_text(value)}
    elif prop_type == "select":
        options = {option.get("name") for option in prop.get("select", {}).get("options", [])}
        if options and value not in options:
            return
        properties[name] = {"select": {"name": value}}
    elif prop_type == "multi_select":
        options = {option.get("name") for option in prop.get("multi_select", {}).get("options", [])}
        if options and value not in options:
            return
        properties[name] = {"multi_select": [{"name": value}]}
    elif prop_type == "status":
        groups = prop.get("status", {}).get("groups", [])
        options = {option.get("name") for group in groups for option in group.get("options", [])}
        if options and value not in options:
            return
        properties[name] = {"status": {"name": value}}
    elif prop_type == "url":
        properties[name] = {"url": value if value.startswith(("http://", "https://")) else None}
    elif prop_type == "date":
        properties[name] = {"date": {"start": value[:10]}}


def _notion_title_from_content(content: str) -> str:
    first_line = next((line.strip() for line in content.splitlines() if line.strip()), "")
    if not first_line:
        return f"LINE Capture {datetime.now().strftime('%Y-%m-%d %H:%M')}"
    return _clip_preserve_lines(first_line, 60)


def _parse_notion_capture_command(user_text: str) -> str | None:
    prefixes = [
        "存到 Notion：",
        "存到 notion：",
        "存到Notion：",
        "記到 Notion：",
        "記到 notion：",
        "notion:",
        "Notion:",
        "notion：",
        "Notion：",
    ]
    stripped = user_text.strip()
    for prefix in prefixes:
        if stripped.startswith(prefix):
            return stripped[len(prefix) :].strip()
    return None


def _looks_like_private_recovery(content: str) -> bool:
    keywords = ["日記", "療癒日記", "諮商", "惡夢", "焦慮", "憂鬱", "精神科", "回診", "burnout"]
    lowered = content.lower()
    return any(keyword.lower() in lowered for keyword in keywords)


def _create_notion_capture(config: dict[str, str], content: str) -> str:
    if not _is_enabled(config, "LINE_AI_NOTION_WRITES_ENABLED"):
        return "Notion 寫入還沒啟用。請在 DoraOS `.env` 設定 `LINE_AI_NOTION_WRITES_ENABLED=true` 和目標 Notion database / parent page。"
    database_id = config.get("LINE_AI_NOTION_NOTES_DB_ID", "").strip()
    parent_page_id = config.get("LINE_AI_NOTION_PARENT_PAGE_ID", "").strip()
    if not database_id and not parent_page_id:
        return "Notion 目標還沒設定。請先設定 `LINE_AI_NOTION_NOTES_DB_ID` 或 `LINE_AI_NOTION_PARENT_PAGE_ID`。"
    if _looks_like_private_recovery(content):
        return "這段看起來比較像日記 / recovery 內容，我先不寫一般 Notion。請改用 Healing Journal 流程，我會幫你放到正確位置。"

    if parent_page_id and not database_id:
        return _create_notion_child_page(config, parent_page_id, content)

    schema = _notion_database_schema(config, database_id)
    db_props = schema.get("properties", {}) or {}
    title_prop = _pick_property(db_props, ["標題", "名稱", "Name", "Title", "title"], {"title"})
    if not title_prop:
        return "Notion schema 沒找到 title 欄位，所以我先不寫入，避免 40060 格式錯誤。"

    now = datetime.now().isoformat(timespec="seconds")
    title_name, _title_type = title_prop
    properties: dict[str, Any] = {
        title_name: {"title": _rich_text(_notion_title_from_content(content), 120)}
    }
    _set_optional_property(properties, db_props, ["內容", "Content", "備註", "Notes", "摘要", "Summary"], content)
    _set_optional_property(properties, db_props, ["來源", "Source"], "LINE")
    _set_optional_property(properties, db_props, ["類型", "Type", "分類", "Category"], "LINE Capture")
    _set_optional_property(properties, db_props, ["狀態", "Status"], "Inbox")
    _set_optional_property(properties, db_props, ["日期", "Date", "Created", "建立日期"], now)

    payload = {
        "parent": {"database_id": database_id},
        "properties": properties,
        "children": [
            {
                "object": "block",
                "type": "paragraph",
                "paragraph": {"rich_text": _rich_text(content)},
            },
            {
                "object": "block",
                "type": "paragraph",
                "paragraph": {"rich_text": _rich_text(f"Source: LINE → DoraOS assistant\nCaptured: {now}")},
            },
        ],
    }
    created = _notion_request(config, "POST", "/pages", payload)
    url = created.get("url", "")
    return f"已存到 Notion。\n\n標題：{_notion_title_from_content(content)}" + (f"\n{url}" if url else "")


def _create_notion_child_page(config: dict[str, str], parent_page_id: str, content: str) -> str:
    now = datetime.now().isoformat(timespec="seconds")
    title = _notion_title_from_content(content)
    payload = {
        "parent": {"page_id": parent_page_id},
        "properties": {
            "title": {"title": _rich_text(title, 120)},
        },
        "children": [
            {
                "object": "block",
                "type": "paragraph",
                "paragraph": {"rich_text": _rich_text(content)},
            },
            {
                "object": "block",
                "type": "paragraph",
                "paragraph": {"rich_text": _rich_text(f"Source: LINE → DoraOS assistant\nCaptured: {now}")},
            },
        ],
    }
    created = _notion_request(config, "POST", "/pages", payload)
    url = created.get("url", "")
    return f"已存到 Notion。\n\n標題：{title}" + (f"\n{url}" if url else "")


def _handle_notion_command(user_text: str) -> str | None:
    content = _parse_notion_capture_command(user_text)
    if content is None:
        return None
    if not content:
        return "要存什麼？格式可以用：\n存到 Notion：今天想到的事"
    try:
        return _create_notion_capture(CONFIG, content)
    except Exception as exc:
        LOGGER.error("Notion capture failed: %s", exc)
        return "Notion 寫入失敗，我先沒有亂補資料。請讓 Codex 檢查 Notion database schema / integration access。"


def _openrouter_chat(config: dict[str, str], user_text: str, context: str) -> str:
    api_key = config.get("OPENROUTER_API_KEY", "").strip()
    if not api_key:
        return ""
    allow_context = config.get("LINE_AI_ALLOW_EXTERNAL_CONTEXT", "false").strip().lower() in {"1", "true", "yes"}
    if not allow_context:
        return ""
    base_url = config.get("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1").rstrip("/")
    model = config.get("LINE_AI_MODEL", "openai/gpt-4.1-mini").strip() or "openai/gpt-4.1-mini"
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": _system_prompt()},
            {"role": "user", "content": f"DoraOS 今日上下文：\n{context}\n\n使用者訊息：{user_text}"},
        ],
        "temperature": 0.4,
        "max_tokens": 900,
    }
    request = urllib.request.Request(
        f"{base_url}/chat/completions",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://doraos.local",
            "X-Title": "DoraOS LINE AI",
        },
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=45) as response:
        data = json.loads(response.read().decode("utf-8"))
    return data["choices"][0]["message"]["content"].strip()


def _fallback_reply(user_text: str, context: str) -> str:
    text = user_text.strip()
    if text in {"晨報", "早安", "morning"}:
        _today, body = build_daily_line_brief(CONFIG)
        return body
    if "信箱" in text or "gmail" in text.lower():
        return "我可以看到今天 Gmail 摘要，但 AI provider 尚未啟用。先看晨報的「Gmail 要處理」段落。"
    if "待辦" in text or "task" in text.lower():
        return "我可以讀取今天 Google Tasks；AI provider 尚未啟用時，請先輸入「晨報」看完整待辦。"
    return (
        "我已接到 LINE 訊息，但完整 AI 模式還沒開。\n\n"
        "原因：預設不會把 Gmail / Calendar / Tasks 送到外部 AI。\n"
        "若你同意，設定 `LINE_AI_ALLOW_EXTERNAL_CONTEXT=true` 後，我就能用 DoraOS 上下文回答。\n\n"
        "要存 Notion 可以傳：\n存到 Notion：今天想到的事"
    )


def generate_reply(user_text: str) -> str:
    notion_reply = _handle_notion_command(user_text)
    if notion_reply is not None:
        return _clip_preserve_lines(notion_reply, MAX_REPLY_CHARS)
    context = _today_context(CONFIG)
    try:
        reply = _openrouter_chat(CONFIG, user_text, context)
    except Exception as exc:
        LOGGER.error("OpenRouter chat failed: %s", exc)
        reply = ""
    if not reply:
        reply = _fallback_reply(user_text, context)
    return _clip_preserve_lines(reply, MAX_REPLY_CHARS)


def verify_signature(body: bytes, signature: str, channel_secret: str) -> bool:
    digest = hmac.new(channel_secret.encode("utf-8"), body, hashlib.sha256).digest()
    expected = base64.b64encode(digest).decode("utf-8")
    return hmac.compare_digest(expected, signature or "")


def reply_to_line(config: dict[str, str], reply_token: str, text: str) -> None:
    token = require_env(config, "LINE_CHANNEL_ACCESS_TOKEN")
    messages = [{"type": "text", "text": chunk} for chunk in split_line_messages(text)]
    payload = json.dumps({"replyToken": reply_token, "messages": messages}, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        LINE_REPLY_URL,
        data=payload,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}",
        },
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=20) as response:
        if response.status >= 300:
            raise RuntimeError(f"LINE reply failed with HTTP {response.status}")


@app.get("/health")
def health() -> dict[str, Any]:
    return {"ok": True, "service": "doraos-line-ai", "time": datetime.now().isoformat(timespec="seconds")}


@app.post("/line/webhook")
async def line_webhook(request: Request) -> JSONResponse:
    body = await request.body()
    signature = request.headers.get("x-line-signature", "")
    channel_secret = CONFIG.get("LINE_CHANNEL_SECRET", "").strip()
    if channel_secret and not verify_signature(body, signature, channel_secret):
        raise HTTPException(status_code=403, detail="invalid LINE signature")

    payload = json.loads(body.decode("utf-8") or "{}")
    for event in payload.get("events", []):
        if event.get("type") != "message":
            continue
        message = event.get("message", {}) or {}
        if message.get("type") != "text":
            continue
        reply_token = event.get("replyToken", "")
        if not reply_token:
            continue
        user_text = message.get("text", "").strip()
        if not user_text:
            continue
        reply = generate_reply(user_text)
        reply_to_line(CONFIG, reply_token, reply)
    return JSONResponse({"ok": True})


def main() -> int:
    parser = argparse.ArgumentParser(description="Run DoraOS interactive LINE AI webhook.")
    parser.add_argument("--env-file", default=str(DEFAULT_ENV_FILE))
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8789)
    args = parser.parse_args()
    _reload_config(args.env_file)

    import uvicorn

    uvicorn.run(app, host=args.host, port=args.port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
