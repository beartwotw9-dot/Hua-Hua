#!/usr/bin/env python3
"""Read-only Google Tasks / reminder list sync for DoraOS Operating Feed."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict, List

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

from common import (
    DEFAULT_ENV_FILE,
    LOG_DIR,
    build_logger,
    clip_text,
    ensure_dir,
    load_env_file,
    require_env,
    retry_call,
    today_stamp,
    wait_for_network,
)


SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/calendar.readonly",
    "https://www.googleapis.com/auth/tasks.readonly",
]


def _retry_settings(config: Dict[str, str]) -> tuple[int, float]:
    retries = int(config.get("SYNC_RETRY_ATTEMPTS", "") or "3")
    delay = float(config.get("SYNC_RETRY_BACKOFF_SECONDS", "") or "2.0")
    return retries, delay


def _is_retryable_google_error(exc: Exception) -> bool:
    lowered = str(exc).lower()
    markers = [
        "failed to resolve",
        "nodename nor servname provided",
        "temporarily unavailable",
        "connection aborted",
        "remote end closed connection without response",
        "remotedisconnected",
        "max retries exceeded",
        "name resolution",
        "timed out",
        "timeout",
        "connection reset",
    ]
    return any(marker in lowered for marker in markers)


def _archive_token(token_path: Path) -> None:
    if not token_path.exists():
        return
    suffix = today_stamp()[1].replace(":", "").replace(" ", "-")
    token_path.rename(token_path.with_name(f"{token_path.name}.invalid-{suffix}"))


def _token_file_has_required_scopes(token_path: Path) -> bool:
    if not token_path.exists():
        return False
    try:
        token_payload = json.loads(token_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return False
    granted = set(token_payload.get("scopes") or [])
    return set(SCOPES).issubset(granted)


def get_credentials(config: Dict[str, str], logger, *, force_reauth: bool = False) -> Credentials:
    credentials_path = Path(require_env(config, "GOOGLE_OAUTH_CREDENTIALS_PATH")).expanduser()
    token_path = Path(require_env(config, "GOOGLE_TOKEN_PATH")).expanduser()
    retries, delay = _retry_settings(config)

    creds = None
    if force_reauth:
        _archive_token(token_path)
        logger.warning("Archived existing Google token and starting OAuth re-auth flow.")

    if token_path.exists():
        if not _token_file_has_required_scopes(token_path):
            _archive_token(token_path)
            logger.warning("Archived Google token because it does not include Google Tasks readonly scope.")
        else:
            creds = Credentials.from_authorized_user_file(str(token_path), SCOPES)

    if creds and creds.expired and creds.refresh_token:
        retry_call(
            lambda: creds.refresh(Request()),
            retries=retries,
            base_delay=delay,
            should_retry=_is_retryable_google_error,
            on_retry=lambda exc, attempt, wait: logger.warning("Google Tasks token refresh retry %s after error: %s", attempt, exc),
        )
        token_path.write_text(creds.to_json(), encoding="utf-8")
        return creds

    if creds and creds.valid:
        return creds

    flow = InstalledAppFlow.from_client_secrets_file(str(credentials_path), SCOPES)
    creds = flow.run_local_server(port=0)
    token_path.parent.mkdir(parents=True, exist_ok=True)
    token_path.write_text(creds.to_json(), encoding="utf-8")
    return creds


def _fetch_all_pages(request_fn) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    page_token = None
    while True:
        response = request_fn(page_token).execute()
        items.extend(response.get("items", []) or [])
        page_token = response.get("nextPageToken")
        if not page_token:
            return items


def fetch_tasks(service, retries: int, delay: float, logger, max_tasks: int) -> list[dict[str, str]]:
    tasklists = retry_call(
        lambda: _fetch_all_pages(lambda page_token: service.tasklists().list(maxResults=100, pageToken=page_token)),
        retries=retries,
        base_delay=delay,
        should_retry=_is_retryable_google_error,
        on_retry=lambda exc, attempt, wait: logger.warning("Google Tasks list retry %s after error: %s", attempt, exc),
    )

    rows: list[dict[str, str]] = []
    for tasklist in tasklists:
        list_id = tasklist.get("id")
        list_title = tasklist.get("title", "Tasks")
        if not list_id:
            continue
        tasks = retry_call(
            lambda: _fetch_all_pages(
                lambda page_token: service.tasks().list(
                    tasklist=list_id,
                    maxResults=100,
                    pageToken=page_token,
                    showCompleted=False,
                    showDeleted=False,
                    showHidden=True,
                )
            ),
            retries=retries,
            base_delay=delay,
            should_retry=_is_retryable_google_error,
            on_retry=lambda exc, attempt, wait: logger.warning("Google Tasks item retry %s after error: %s", attempt, exc),
        )
        for task in tasks:
            if task.get("status") == "completed" or task.get("deleted"):
                continue
            rows.append(
                {
                    "title": clip_text(task.get("title", "(untitled task)"), 180),
                    "list": list_title,
                    "due": (task.get("due") or "")[:10],
                    "notes": clip_text(task.get("notes", ""), 220),
                    "updated": task.get("updated", ""),
                }
            )

    rows.sort(key=lambda row: (row["due"] or "9999-12-31", row["list"], row["title"]))
    return rows[:max_tasks]


def build_tasks_view(tasks: List[dict[str, str]], generated_at: str, max_tasks: int) -> str:
    lines = [
        "# Google 提醒清單 | Google Tasks",
        "",
        f"- 生成時間 | generated: {generated_at}",
        f"- 任務數量 | tasks: {len(tasks)}",
        f"- 顯示上限 | max: {max_tasks}",
        "",
        "## 規則 | Rule",
        "",
        "- 這是 Google Calendar 側邊欄 Tasks / 提醒清單，不是一般日曆事件。",
        "- 它是今天晨報的輸入資料；真正要承諾的事再挑進 Today Manual Tasks。",
        "",
        "## 提醒清單 | Reminder List",
        "",
    ]
    if not tasks:
        lines.append("- 目前沒有找到未完成 Google Tasks。")
    else:
        for task in tasks:
            due = f"（due: {task['due']}）" if task.get("due") else "（no due date）"
            lines.append(f"- [ ] {task['title']} {due} · {task['list']}")
            if task.get("notes"):
                lines.append(f"  - notes: {task['notes']}")
    return "\n".join(lines).rstrip() + "\n"


def build_failure_tasks(generated_at: str, error: Exception) -> str:
    error_text = str(error)
    lowered = error_text.lower()
    if "accessnotconfigured" in lowered or "api has not been used" in lowered or "it is disabled" in lowered:
        status = "api-disabled"
    elif "invalid_grant" in lowered or "insufficient" in lowered:
        status = "auth-required"
    else:
        status = "api-failed"
    return "\n".join(
        [
            "# Google 提醒清單 | Google Tasks",
            "",
            f"- 生成時間 | generated: {generated_at}",
            f"- 狀態 | status: {status}",
            f"- 錯誤 | error: `{error_text[:220]}`",
            "- 說明 | note: Google Tasks API 沒有成功取得提醒清單；此頁不複製舊資料，避免把過期待辦當成今天。",
            "",
            "## 提醒清單 | Reminder List",
            "",
            "- Google Tasks API failed. No live reminders were written.",
            "",
        ]
    ).rstrip() + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="Sync Google Tasks reminder list into DoraOS Operating Feed.")
    parser.add_argument("--env-file", default=str(DEFAULT_ENV_FILE))
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument("--max-tasks", type=int, default=50)
    parser.add_argument("--reauth", action="store_true", help="Archive the existing token and run the local OAuth flow.")
    args = parser.parse_args()

    logger = build_logger("doraos.google_tasks_sync", LOG_DIR / "dora_google_tasks_sync.log", verbose=args.verbose)
    config = load_env_file(Path(args.env_file))
    vault_path = Path(require_env(config, "OBSIDIAN_VAULT_PATH")).expanduser()

    network_timeout = float(config.get("NETWORK_WARMUP_TIMEOUT", "60") or "60")
    wait_for_network(timeout=network_timeout, logger=logger)

    today, generated_at = today_stamp()
    output_path = vault_path / "Resources" / "Operating Feed" / f"{today} Google Tasks.md"

    try:
        retries, delay = _retry_settings(config)
        creds = get_credentials(config, logger, force_reauth=args.reauth)
        service = build("tasks", "v1", credentials=creds, cache_discovery=False)
        tasks = fetch_tasks(service, retries, delay, logger, args.max_tasks)
        content = build_tasks_view(tasks, generated_at, args.max_tasks)
    except Exception as exc:
        content = build_failure_tasks(generated_at, exc)
        logger.error("Google Tasks sync failed; wrote explicit failure view without stale fallback: %s", exc)

    if args.dry_run:
        print(content)
        logger.info("Dry run complete for %s", output_path)
        return 0

    ensure_dir(output_path.parent)
    output_path.write_text(content, encoding="utf-8")
    logger.info("Wrote Google Tasks view to %s", output_path)
    print(output_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
