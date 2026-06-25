#!/usr/bin/env python3
"""Read-only Google Calendar today view sync for DoraOS Operating Feed."""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta
from pathlib import Path
import re
from typing import Dict, List

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

from common import DEFAULT_ENV_FILE, GOOGLE_READONLY_SCOPES, LOG_DIR, build_logger, ensure_dir, load_env_file, require_env, retry_call, today_stamp, wait_for_network


SCOPES = GOOGLE_READONLY_SCOPES


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


def _extract_latest_snapshot(text: str) -> str:
    markers = ["# 今日日曆 | Calendar Today View", "# Calendar Today View"]
    last_position = -1
    last_marker = ""
    for marker in markers:
        position = text.rfind(marker)
        if position > last_position:
            last_position = position
            last_marker = marker
    if last_position == -1:
        return text.strip()
    return text[last_position :].strip() if last_marker else text.strip()


def _calendar_snapshot_score(note_path: Path) -> tuple[int, int, int, str]:
    text = _extract_latest_snapshot(note_path.read_text(encoding="utf-8", errors="ignore"))
    match = re.search(r"events:\s*(\d+)", text)
    events = int(match.group(1)) if match else 0
    has_event_blocks = 1 if "\n### " in f"\n{text}" else 0
    is_live = 0 if "status: fallback" in text or "status: backfilled-fallback" in text else 1
    return (has_event_blocks, events, is_live, note_path.stem)


def latest_existing_calendar(feed_dir: Path, current_output: Path | None = None) -> Path | None:
    candidates = [
        path
        for path in feed_dir.glob("* Calendar View.md")
        if path.is_file() and (current_output is None or path.resolve() != current_output.resolve())
    ]
    if not candidates:
        return None
    candidates.sort(key=_calendar_snapshot_score, reverse=True)
    return candidates[0]


def _archive_token(token_path: Path) -> None:
    if not token_path.exists():
        return
    suffix = today_stamp()[1].replace(":", "").replace(" ", "-")
    token_path.rename(token_path.with_name(f"{token_path.name}.invalid-{suffix}"))


def get_credentials(config: Dict[str, str], logger, *, force_reauth: bool = False) -> Credentials:
    credentials_path = Path(require_env(config, "GOOGLE_OAUTH_CREDENTIALS_PATH")).expanduser()
    token_path = Path(require_env(config, "GOOGLE_TOKEN_PATH")).expanduser()
    retries, delay = _retry_settings(config)

    creds = None
    if force_reauth:
        _archive_token(token_path)
        logger.warning("Archived existing Google token and starting OAuth re-auth flow.")

    if token_path.exists():
        creds = Credentials.from_authorized_user_file(str(token_path), SCOPES)

    if creds and creds.expired and creds.refresh_token:
        retry_call(
            lambda: creds.refresh(Request()),
            retries=retries,
            base_delay=delay,
            should_retry=_is_retryable_google_error,
            on_retry=lambda exc, attempt, wait: logger.warning("Calendar token refresh retry %s after error: %s", attempt, exc),
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


def build_calendar_view(events: List[Dict[str, str]], generated_at: str, timezone_str: str) -> str:
    lines = [
        "# 今日日曆 | Calendar Today View",
        "",
        f"- 生成時間 | generated: {generated_at}",
        f"- 時區 | timezone: `{timezone_str}`",
        f"- 事件數量 | events: {len(events)}",
        "",
        "## 規則 | Rule",
        "",
        "- 這是日程上下文，不是你的待辦清單 | this is calendar context, not your task list",
        "- 用它來看時間壓力，不要自動生成承諾事項 | use it to see time pressure, not to auto-generate commitments",
        "",
        "## 今天 | Today",
        "",
    ]
    if not events:
        lines.append("- 今天沒有找到事件 | No events found for the selected window.")
    else:
        for event in events:
            lines += [
                f"### {event['title']}",
                f"- 開始 | start: {event['start']}",
                f"- 結束 | end: {event['end']}",
                f"- 地點 | location: {event['location'] or '(none)'}",
                "",
            ]
    return "\n".join(lines).rstrip() + "\n"


def build_fallback_calendar(previous_text: str, generated_at: str, timezone_str: str, source_name: str) -> str:
    latest_snapshot = _extract_latest_snapshot(previous_text)
    has_verified_events = "\n### " in f"\n{latest_snapshot}"
    fallback_note = (
        "- 說明 | note: 今日無法連線到 Google Calendar API，已改用最近可用版本補上今天頁面 | Google Calendar API was unavailable today, so the latest available calendar view was copied forward."
        if has_verified_events
        else "- 說明 | note: 今日無法連線到 Google Calendar API，而且最近可用日曆也沒有已驗證事件；今天是否有行程目前無法確認 | Google Calendar API was unavailable, and the latest available calendar snapshot also had no verified events; today's schedule could not be confirmed."
    )
    return "\n".join(
        [
            "# 今日日曆 | Calendar Today View",
            "",
            f"- 生成時間 | generated: {generated_at}",
            f"- 時區 | timezone: `{timezone_str}`",
            "- 狀態 | status: fallback",
            f"- 來源 | source: {source_name}",
            fallback_note,
            "",
            "## 目前可用內容 | Latest Available Content",
            "",
            latest_snapshot,
            "",
        ]
    ).rstrip() + "\n"


def build_failure_calendar(generated_at: str, timezone_str: str, error: Exception) -> str:
    error_text = str(error)
    status = "auth-required" if "invalid_grant" in error_text.lower() else "api-failed"
    note = (
        "Google OAuth token 已失效或被撤銷；需要重新授權，否則本地 Calendar 自動化無法抓取即時行程。"
        if status == "auth-required"
        else "Google Calendar API 沒有成功取得今天資料；此頁不含舊行程備援內容，避免把過期資訊誤認為今天行程。"
    )
    return "\n".join(
        [
            "# 今日日曆 | Calendar Today View",
            "",
            f"- 生成時間 | generated: {generated_at}",
            f"- 時區 | timezone: `{timezone_str}`",
            f"- 狀態 | status: {status}",
            f"- 錯誤 | error: `{str(error_text)[:220]}`",
            f"- 說明 | note: {note}",
            "",
            "## 今天 | Today",
            "",
            "- Google Calendar API failed. No live events were written.",
            "",
        ]
    ).rstrip() + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="Sync today's Google Calendar view into DoraOS Operating Feed.")
    parser.add_argument("--env-file", default=str(DEFAULT_ENV_FILE))
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument("--calendar-id", default="primary")
    parser.add_argument("--reauth", action="store_true", help="Archive the existing token and run the local OAuth flow.")
    args = parser.parse_args()

    logger = build_logger("doraos.calendar_sync", LOG_DIR / "dora_calendar_today_sync.log", verbose=args.verbose)
    config = load_env_file(Path(args.env_file))
    vault_path = Path(require_env(config, "OBSIDIAN_VAULT_PATH")).expanduser()
    timezone_str = config.get("GOOGLE_CALENDAR_TIMEZONE", "Asia/Taipei")

    network_timeout = float(config.get("NETWORK_WARMUP_TIMEOUT", "120") or "120")
    wait_for_network(timeout=network_timeout, logger=logger)

    today, generated_at = today_stamp()
    output_path = vault_path / "Resources" / "Operating Feed" / f"{today} Calendar View.md"
    feed_dir = output_path.parent

    try:
        retries, delay = _retry_settings(config)
        creds = get_credentials(config, logger, force_reauth=args.reauth)
        service = build("calendar", "v3", credentials=creds, cache_discovery=False)

        now = datetime.now().astimezone()
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        end = start + timedelta(days=1)
        response = retry_call(
            lambda: service.events().list(
                calendarId=args.calendar_id,
                timeMin=start.isoformat(),
                timeMax=end.isoformat(),
                singleEvents=True,
                orderBy="startTime",
            ).execute(),
            retries=retries,
            base_delay=delay,
            should_retry=_is_retryable_google_error,
            on_retry=lambda exc, attempt, wait: logger.warning("Calendar list retry %s after error: %s", attempt, exc),
        )

        events: List[Dict[str, str]] = []
        seen: set[tuple[str, str, str, str]] = set()
        for item in response.get("items", []) or []:
            start_value = item.get("start", {}).get("dateTime") or item.get("start", {}).get("date") or ""
            end_value = item.get("end", {}).get("dateTime") or item.get("end", {}).get("date") or ""
            event = {
                "title": item.get("summary", "(untitled)"),
                "start": start_value,
                "end": end_value,
                "location": item.get("location", ""),
            }
            key = (event["title"], event["start"], event["end"], event["location"])
            if key in seen:
                continue
            seen.add(key)
            events.append(event)
        content = build_calendar_view(events, generated_at, timezone_str)
    except Exception as exc:
        allow_stale_fallback = config.get("CALENDAR_ALLOW_STALE_FALLBACK", "").strip().lower() in {"1", "true", "yes"}
        if allow_stale_fallback:
            fallback_path = latest_existing_calendar(feed_dir, current_output=output_path)
            if not fallback_path:
                raise
            previous_text = fallback_path.read_text(encoding="utf-8")
            content = build_fallback_calendar(previous_text, generated_at, timezone_str, fallback_path.stem)
            logger.warning("Calendar sync failed; wrote stale fallback view from %s: %s", fallback_path, exc)
        else:
            content = build_failure_calendar(generated_at, timezone_str, exc)
            logger.error("Calendar sync failed; wrote explicit failure view without stale fallback: %s", exc)

    if args.dry_run:
        print(content)
        logger.info("Dry run complete for %s", output_path)
        return 0

    ensure_dir(output_path.parent)
    output_path.write_text(content, encoding="utf-8")
    logger.info("Wrote calendar view to %s", output_path)
    print(output_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
