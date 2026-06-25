#!/usr/bin/env python3
"""Shared helpers for DoraOS operating feed sync scripts."""

from __future__ import annotations

import json
import logging
import re
import socket
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, TypeVar


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ENV_FILE = ROOT / ".env"
LOG_DIR = ROOT / "logs"
T = TypeVar("T")

GOOGLE_READONLY_SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/calendar.readonly",
    "https://www.googleapis.com/auth/tasks.readonly",
]

# Default retry settings — can be overridden via .env
# SYNC_RETRY_ATTEMPTS=3  (up from 2; helps with slow DNS after wake)
# SYNC_RETRY_BACKOFF_SECONDS=2.0  (up from 1.5; gives more time between tries)
DEFAULT_RETRY_ATTEMPTS: int = 3
DEFAULT_RETRY_BACKOFF: float = 2.0

# DNS hosts to probe for network readiness (tried in order)
_NETWORK_PROBE_HOSTS = [
    ("api.notion.com", 443),
    ("www.googleapis.com", 443),
    ("oauth2.googleapis.com", 443),
    ("api.semanticscholar.org", 443),
    ("export.arxiv.org", 443),
    ("8.8.8.8", 53),
]


def load_env_file(path: Path) -> Dict[str, str]:
    values: Dict[str, str] = {}
    if not path.exists():
        return values
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def require_env(config: Dict[str, str], key: str) -> str:
    value = config.get(key, "").strip()
    if not value:
        raise ValueError(f"Missing required environment variable: {key}")
    return value


def ensure_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


def build_logger(name: str, log_file: Path, verbose: bool = False) -> logging.Logger:
    ensure_dir(log_file.parent)
    logger = logging.getLogger(name)
    logger.setLevel(logging.DEBUG if verbose else logging.INFO)
    logger.handlers.clear()
    logger.propagate = False

    file_handler = logging.FileHandler(log_file, encoding="utf-8")
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logger.addHandler(file_handler)

    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.DEBUG if verbose else logging.INFO)
    console_handler.setFormatter(logging.Formatter("%(levelname)s %(message)s"))
    logger.addHandler(console_handler)
    return logger


def write_json(path: Path, payload: Any) -> None:
    ensure_dir(path.parent)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def today_stamp() -> tuple[str, str]:
    now = datetime.now()
    return now.strftime("%Y-%m-%d"), now.strftime("%Y-%m-%d %H:%M")


def clip_text(text: str, limit: int = 240) -> str:
    compact = " ".join(str(text or "").split())
    if len(compact) <= limit:
        return compact
    return compact[: limit - 1].rstrip() + "…"


def normalize_space(value: str) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


def wait_for_network(
    timeout: float = 120.0,
    check_interval: float = 4.0,
    logger: logging.Logger | None = None,
) -> bool:
    """Block until TCP connectivity is confirmed, up to *timeout* seconds.

    Tries each host in ``_NETWORK_PROBE_HOSTS`` in turn.  Returns ``True`` as
    soon as any probe succeeds, ``False`` if the timeout expires first.

    This is called at the top of each sync script so that early-morning
    launchd jobs (07:25–08:20) do not fail with Errno 8 / DNS-not-ready
    errors when the machine just woke from sleep.
    """
    deadline = time.monotonic() + timeout
    probe_socket_timeout = 3.0
    attempt = 0
    while time.monotonic() < deadline:
        for host, port in _NETWORK_PROBE_HOSTS:
            try:
                sock = socket.create_connection((host, port), timeout=probe_socket_timeout)
                sock.close()
                if attempt > 0 and logger:
                    elapsed = time.monotonic() - (deadline - timeout)
                    logger.info(
                        "Network ready after %.1fs (probe: %s:%s)", elapsed, host, port
                    )
                return True
            except OSError:
                continue
        attempt += 1
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        wait = min(check_interval, remaining)
        if logger:
            logger.info(
                "Network not yet reachable (attempt %s); retrying in %.0fs (%.0fs remaining)…",
                attempt,
                wait,
                remaining,
            )
        time.sleep(wait)

    if logger:
        logger.warning(
            "Network unavailable after %.0fs — proceeding anyway; fallback will be used if fetch fails.",
            timeout,
        )
    return False


def retry_call(
    fn: Callable[[], T],
    *,
    retries: int = DEFAULT_RETRY_ATTEMPTS,
    base_delay: float = DEFAULT_RETRY_BACKOFF,
    should_retry: Callable[[Exception], bool] | None = None,
    on_retry: Callable[[Exception, int, float], None] | None = None,
) -> T:
    """Call *fn* up to *retries*+1 times with exponential-ish back-off.

    ``on_retry(exc, attempt_number, wait_seconds)`` is called before each
    sleep so callers can log retry context.  The final exception is re-raised
    if all attempts fail.
    """
    last_error: Exception | None = None
    for attempt in range(retries + 1):
        try:
            return fn()
        except Exception as exc:  # pragma: no cover - helper used by sync scripts
            last_error = exc
            if should_retry and not should_retry(exc):
                raise
            if attempt >= retries:
                break
            delay = base_delay * (attempt + 1)
            if on_retry:
                on_retry(exc, attempt + 1, delay)
            time.sleep(delay)
    assert last_error is not None
    raise last_error
