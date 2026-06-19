#!/usr/bin/env python3
"""Shared helpers for DoraOS recovery infrastructure."""

from __future__ import annotations

import hashlib
import json
import logging
import re
import socket
import time
from datetime import date, datetime
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, TypeVar


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ENV_FILE = ROOT / ".env"
STATE_DIR = ROOT / "recovery" / "state"
LOG_DIR = ROOT / "logs"

GENERATED_START = "<!-- DORAOS_SYNC:GENERATED_START -->"
GENERATED_END = "<!-- DORAOS_SYNC:GENERATED_END -->"
T = TypeVar("T")

DEFAULT_RETRY_ATTEMPTS: int = 3
DEFAULT_RETRY_BACKOFF: float = 2.0
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


def read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: Any) -> None:
    ensure_dir(path.parent)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def slugify(text: str, fallback: str = "entry") -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9\u4e00-\u9fff]+", "-", text.strip().lower())
    cleaned = cleaned.strip("-")
    return cleaned or fallback


def normalize_space(value: str) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


def to_iso_date(value: Any) -> Optional[str]:
    if value is None or value == "":
        return None
    if isinstance(value, date) and not isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, datetime):
        return value.date().isoformat()
    text = str(value).strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).date().isoformat()
    except ValueError:
        return text[:10]


def unique_list(items: Iterable[str]) -> List[str]:
    seen = set()
    ordered: List[str] = []
    for item in items:
        value = normalize_space(item)
        if not value:
            continue
        key = value.casefold()
        if key in seen:
            continue
        seen.add(key)
        ordered.append(value)
    return ordered


def as_int(value: Any) -> Optional[int]:
    if value is None or value == "":
        return None
    try:
        return int(float(str(value).strip()))
    except ValueError:
        return None


def ordinal_score(value: str, mapping: Dict[str, int]) -> Optional[int]:
    if not value:
        return None
    return mapping.get(value.strip().casefold())


def hash_signature(payload: Any) -> str:
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def split_generated_and_manual(body: str) -> tuple[str, str]:
    if GENERATED_START not in body or GENERATED_END not in body:
        return "", body.strip()
    start = body.index(GENERATED_START)
    end = body.index(GENERATED_END) + len(GENERATED_END)
    generated = body[start:end].strip()
    manual = (body[:start] + "\n" + body[end:]).strip()
    return generated, manual


def wrap_generated(markdown: str) -> str:
    return f"{GENERATED_START}\n{markdown.rstrip()}\n{GENERATED_END}"


def wait_for_network(
    timeout: float = 120.0,
    check_interval: float = 4.0,
    logger=None,
) -> bool:
    """Block until network is reachable, up to *timeout* seconds."""
    deadline = time.monotonic() + timeout
    attempt = 0
    while time.monotonic() < deadline:
        for host, port in _NETWORK_PROBE_HOSTS:
            try:
                sock = socket.create_connection((host, port), timeout=3.0)
                sock.close()
                if attempt > 0 and logger:
                    logger.info(
                        "Network ready after %.1fs (probe: %s:%s)",
                        time.monotonic() - (deadline - timeout),
                        host,
                        port,
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
            logger.info("Network not ready (attempt %s); retrying in %.0fs…", attempt, wait)
        time.sleep(wait)
    if logger:
        logger.warning("Network unavailable after %.0fs — will try anyway; fallback may be used.", timeout)
    return False


def retry_call(
    fn: Callable[[], T],
    *,
    retries: int = DEFAULT_RETRY_ATTEMPTS,
    base_delay: float = DEFAULT_RETRY_BACKOFF,
    should_retry: Callable[[Exception], bool] | None = None,
    on_retry: Callable[[Exception, int, float], None] | None = None,
) -> T:
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
