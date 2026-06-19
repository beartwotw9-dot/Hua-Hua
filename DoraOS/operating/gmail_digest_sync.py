#!/usr/bin/env python3
"""Read-only Gmail digest sync for DoraOS Operating Feed."""

from __future__ import annotations

import argparse
import base64
from pathlib import Path
from typing import Any, Dict, List

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

from common import DEFAULT_ENV_FILE, LOG_DIR, ROOT, build_logger, clip_text, ensure_dir, load_env_file, require_env, retry_call, today_stamp, wait_for_network


SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/calendar.readonly",
]
DEFAULT_QUERY = "in:inbox is:unread newer_than:3d"


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
    markers = ["# Gmail 摘要 | Gmail Digest", "# Gmail Digest"]
    last_position = -1
    last_marker = ""
    for marker in markers:
        position = text.rfind(marker)
        if position > last_position:
            last_position = position
            last_marker = marker
    if last_position == -1:
        return text.strip()
    return text[last_position:].strip() if last_marker else text.strip()


def latest_existing_digest(feed_dir: Path, current_output: Path | None = None) -> Path | None:
    candidates = [
        path
        for path in feed_dir.glob("* Gmail Digest.md")
        if path.is_file() and (current_output is None or path.resolve() != current_output.resolve())
    ]
    if not candidates:
        return None
    candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)
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
        logger.warning("Archived existing Gmail token and starting OAuth re-auth flow.")

    if token_path.exists():
        creds = Credentials.from_authorized_user_file(str(token_path), SCOPES)

    if creds and creds.expired and creds.refresh_token:
        retry_call(
            lambda: creds.refresh(Request()),
            retries=retries,
            base_delay=delay,
            should_retry=_is_retryable_google_error,
            on_retry=lambda exc, attempt, wait: logger.warning("Gmail token refresh retry %s after error: %s", attempt, exc),
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


def _header(headers: List[Dict[str, str]], name: str) -> str:
    for header in headers:
        if header.get("name", "").lower() == name.lower():
            return header.get("value", "")
    return ""


def _extract_text(payload: Dict[str, Any]) -> str:
    body = payload.get("body", {}) or {}
    data = body.get("data")
    if data:
        try:
            return base64.urlsafe_b64decode(data.encode("utf-8")).decode("utf-8", errors="replace")
        except Exception:
            return ""
    for part in payload.get("parts", []) or []:
        mime = part.get("mimeType", "")
        if mime == "text/plain":
            part_body = part.get("body", {}) or {}
            pdata = part_body.get("data")
            if pdata:
                try:
                    return base64.urlsafe_b64decode(pdata.encode("utf-8")).decode("utf-8", errors="replace")
                except Exception:
                    continue
    return ""


def build_digest(messages: List[Dict[str, str]], generated_at: str, query: str) -> str:
    lines = [
        "# Gmail 摘要 | Gmail Digest",
        "",
        f"- 生成時間 | generated: {generated_at}",
        f"- 查詢條件 | query: `{query}`",
        f"- 郵件數量 | messages: {len(messages)}",
        "",
        "## 規則 | Rule",
        "",
        "- 這是上下文，不是你的待辦清單 | this is context, not your todo list",
        "- 不要自動把 email 變成承諾事項 | do not auto-convert email into commitments",
        "- 只把你真的要做的事手動抄到 Today Manual Tasks | copy only what you actually need into Today Manual Tasks",
        "",
        "## 郵件列表 | Messages",
        "",
    ]
    if not messages:
        lines.append("- 今天沒有符合條件的郵件 | No matching messages today.")
    else:
        for item in messages:
            lines += [
                f"### {item['subject'] or '(no subject)'}",
                f"- 寄件者 | from: {item['from'] or '(unknown)'}",
                f"- 日期 | date: {item['date'] or '(unknown)'}",
                f"- 標籤 | labels: {item['labels'] or '(none)'}",
                f"- 摘要 | snippet: {item['snippet'] or '(none)'}",
                "",
            ]
    return "\n".join(lines).rstrip() + "\n"


def build_fallback_digest(previous_text: str, generated_at: str, source_name: str) -> str:
    latest_snapshot = _extract_latest_snapshot(previous_text)
    return "\n".join(
        [
            "# Gmail 摘要 | Gmail Digest",
            "",
            f"- 生成時間 | generated: {generated_at}",
            "- 狀態 | status: fallback",
            f"- 來源 | source: {source_name}",
            "- 說明 | note: 今日無法連線到 Gmail API，已改用最近可用版本補上今天頁面 | Gmail API was unavailable today, so the latest available digest was copied forward.",
            "",
            "## 目前可用內容 | Latest Available Content",
            "",
            latest_snapshot,
            "",
        ]
    ).rstrip() + "\n"


def build_failure_digest(generated_at: str, query: str, error: Exception) -> str:
    error_text = str(error)
    status = "auth-required" if "invalid_grant" in error_text.lower() else "api-failed"
    note = (
        "Gmail OAuth token 已失效或被撤銷；需要手動重新授權 `gmail_digest_sync.py --reauth`。"
        if status == "auth-required"
        else "Gmail API 沒有成功取得今天資料；此頁不含舊郵件備援內容，避免把過期資訊誤認為今天信件。"
    )
    return "\n".join(
        [
            "# Gmail 摘要 | Gmail Digest",
            "",
            f"- 生成時間 | generated: {generated_at}",
            f"- 狀態 | status: {status}",
            f"- 查詢條件 | query: `{query}`",
            f"- 錯誤 | error: `{clip_text(error_text, 220)}`",
            f"- 說明 | note: {note}",
            "",
            "## 郵件列表 | Messages",
            "",
            "- Gmail API failed. No live messages were written.",
            "",
        ]
    ).rstrip() + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="Sync a read-only Gmail digest into DoraOS Operating Feed.")
    parser.add_argument("--env-file", default=str(DEFAULT_ENV_FILE))
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument("--query", default="")
    parser.add_argument("--max-results", type=int, default=12)
    parser.add_argument("--reauth", action="store_true", help="Archive the existing token and run the local OAuth flow.")
    args = parser.parse_args()

    logger = build_logger("doraos.gmail_sync", LOG_DIR / "dora_gmail_digest_sync.log", verbose=args.verbose)
    config = load_env_file(Path(args.env_file))
    vault_path = Path(require_env(config, "OBSIDIAN_VAULT_PATH")).expanduser()
    query = args.query.strip() or config.get("GMAIL_DIGEST_QUERY", DEFAULT_QUERY)

    network_timeout = float(config.get("NETWORK_WARMUP_TIMEOUT", "120") or "120")
    wait_for_network(timeout=network_timeout, logger=logger)

    today, generated_at = today_stamp()
    output_path = vault_path / "Resources" / "Operating Feed" / f"{today} Gmail Digest.md"
    feed_dir = output_path.parent

    try:
        retries, delay = _retry_settings(config)
        creds = get_credentials(config, logger, force_reauth=args.reauth)
        service = build("gmail", "v1", credentials=creds, cache_discovery=False)
        response = retry_call(
            lambda: service.users().messages().list(userId="me", q=query, maxResults=args.max_results).execute(),
            retries=retries,
            base_delay=delay,
            should_retry=_is_retryable_google_error,
            on_retry=lambda exc, attempt, wait: logger.warning("Gmail list retry %s after error: %s", attempt, exc),
        )
        message_refs = response.get("messages", []) or []

        messages: List[Dict[str, str]] = []
        for ref in message_refs:
            message = retry_call(
                lambda ref_id=ref["id"]: service.users().messages().get(userId="me", id=ref_id, format="full").execute(),
                retries=retries,
                base_delay=delay,
                should_retry=_is_retryable_google_error,
                on_retry=lambda exc, attempt, wait, ref_id=ref["id"]: logger.warning("Gmail message retry %s for %s after error: %s", attempt, ref_id, exc),
            )
            payload = message.get("payload", {}) or {}
            headers = payload.get("headers", []) or []
            text = _extract_text(payload)
            messages.append(
                {
                    "subject": _header(headers, "Subject"),
                    "from": _header(headers, "From"),
                    "date": _header(headers, "Date"),
                    "labels": ", ".join(message.get("labelIds", []) or []),
                    "snippet": clip_text(text or message.get("snippet", ""), 280),
                }
            )
        content = build_digest(messages, generated_at, query)
    except Exception as exc:
        allow_stale_fallback = config.get("GMAIL_ALLOW_STALE_FALLBACK", "").strip().lower() in {"1", "true", "yes"}
        if allow_stale_fallback:
            fallback_path = latest_existing_digest(feed_dir, current_output=output_path)
            if not fallback_path:
                raise
            previous_text = fallback_path.read_text(encoding="utf-8")
            content = build_fallback_digest(previous_text, generated_at, fallback_path.stem)
            logger.warning("Gmail sync failed; wrote stale fallback digest from %s: %s", fallback_path, exc)
        else:
            content = build_failure_digest(generated_at, query, exc)
            logger.error("Gmail sync failed; wrote explicit api-failed digest without stale fallback: %s", exc)

    if args.dry_run:
        print(content)
        logger.info("Dry run complete for %s", output_path)
        return 0

    ensure_dir(output_path.parent)
    output_path.write_text(content, encoding="utf-8")
    logger.info("Wrote Gmail digest to %s", output_path)
    print(output_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
