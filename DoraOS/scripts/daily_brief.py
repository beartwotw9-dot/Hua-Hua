#!/usr/bin/env python3
"""DoraOS v0.4 daily brief pipeline.

Generates an Obsidian-safe daily brief from scoped local and adapter-backed
sources. The first version is deliberately conservative:

- reads only from configured sources
- writes only one markdown brief file
- supports dry-run and local-only mode
- never mutates GitHub, Linear, Gmail, or durable memory
- degrades gracefully to mock data if real connectors are unavailable
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import sqlite3
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence


SAFE_OUTPUT_DIR = Path("Resources") / "AI Briefs"
MEMORY_REVIEW_DIR = Path("Resources") / "AI Memory Review"
APPROVED_MEMORY_FILE = Path("Resources") / "AI Memory" / "approved-memory.md"
QUEUE_STATE_FILE = Path("memory") / "memory_review_queue.json"
DEFAULT_FACT_BULLET_LIMIT = 5
DEFAULT_SOURCE_SNIPPET_LIMIT = 220
DEFAULT_SECTION_ITEM_LIMIT = 8


def load_dotenv(dotenv_path: Path) -> None:
    if not dotenv_path.exists():
        return
    for raw_line in dotenv_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip())


def setup_logging(log_path: Path, verbose: bool = False) -> logging.Logger:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("doraos.daily_brief")
    logger.setLevel(logging.DEBUG if verbose else logging.INFO)
    logger.handlers.clear()

    file_handler = logging.FileHandler(log_path, encoding="utf-8")
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s")
    )
    logger.addHandler(file_handler)

    stream_handler = logging.StreamHandler(sys.stderr)
    stream_handler.setLevel(logging.DEBUG if verbose else logging.INFO)
    stream_handler.setFormatter(logging.Formatter("%(levelname)s: %(message)s"))
    logger.addHandler(stream_handler)

    return logger


def safe_read_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def clip_text(text: str, limit: int = DEFAULT_SOURCE_SNIPPET_LIMIT) -> str:
    compact = " ".join(text.split())
    if len(compact) <= limit:
        return compact
    return compact[: limit - 1].rstrip() + "…"


def sanitize_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (list, tuple)):
        return ", ".join(sanitize_value(item) for item in value if item)
    return clip_text(str(value))


def strip_markdown_noise(text: str) -> str:
    cleaned = text.replace("#", " ").replace("`", " ").replace("*", " ")
    return clip_text(" ".join(cleaned.split()))


def parse_iso_date(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    for fmt in ("%Y-%m-%d", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%SZ"):
        try:
            return datetime.strptime(value, fmt)
        except ValueError:
            continue
    return None


@dataclass
class BriefItem:
    source_type: str
    title: str
    summary: str
    project: str = ""
    status: str = ""
    url: str = ""
    updated_at: str = ""
    labels: List[str] = field(default_factory=list)
    action_hint: str = ""
    confidence: str = "fact"
    sensitivity: str = "normal"
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class SourceResult:
    name: str
    status: str
    items: List[BriefItem] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)


@dataclass
class MemoryCandidate:
    candidate_id: str
    date: str
    source: str
    type: str
    proposed_memory: str
    reason: str
    confidence: str
    sensitivity_level: str
    suggested_lifespan: str
    status: str = "pending"
    fingerprint: str = ""
    review_file: str = ""
    decided_at: str = ""
    approved_at: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "candidate_id": self.candidate_id,
            "date": self.date,
            "source": self.source,
            "type": self.type,
            "proposed_memory": self.proposed_memory,
            "reason": self.reason,
            "confidence": self.confidence,
            "sensitivity_level": self.sensitivity_level,
            "suggested_lifespan": self.suggested_lifespan,
            "status": self.status,
            "fingerprint": self.fingerprint,
            "review_file": self.review_file,
            "decided_at": self.decided_at,
            "approved_at": self.approved_at,
        }

    @classmethod
    def from_dict(cls, payload: Dict[str, Any]) -> "MemoryCandidate":
        return cls(**payload)


class BaseSource:
    def __init__(self, name: str, source_type: str, enabled: bool = True) -> None:
        self.name = name
        self.source_type = source_type
        self.enabled = enabled

    def collect(self) -> SourceResult:
        raise NotImplementedError


class JsonListSource(BaseSource):
    def __init__(
        self,
        name: str,
        source_type: str,
        path: Optional[Path],
        enabled: bool = True,
    ) -> None:
        super().__init__(name=name, source_type=source_type, enabled=enabled)
        self.path = path

    def collect(self) -> SourceResult:
        result = SourceResult(name=self.name, status="disabled")
        if not self.enabled:
            return result
        if not self.path:
            result.status = "not_configured"
            result.notes.append("No path configured.")
            return result
        if not self.path.exists():
            result.status = "missing"
            result.errors.append(f"Configured path not found: {self.path}")
            return result
        try:
            payload = safe_read_json(self.path)
            raw_items = payload.get("items", payload if isinstance(payload, list) else [])
            result.items = [self._to_item(entry) for entry in raw_items]
            result.status = "ok"
            result.notes.append(f"Loaded {len(result.items)} item(s) from {self.path.name}.")
            return result
        except Exception as exc:  # pragma: no cover - safe fallback
            result.status = "error"
            result.errors.append(f"Failed to read {self.path}: {exc}")
            return result

    def _to_item(self, entry: Dict[str, Any]) -> BriefItem:
        return BriefItem(
            source_type=self.source_type,
            title=sanitize_value(entry.get("title") or entry.get("subject") or "Untitled"),
            summary=sanitize_value(
                entry.get("summary") or entry.get("snippet") or entry.get("description") or ""
            ),
            project=sanitize_value(entry.get("project") or entry.get("repo") or entry.get("team") or ""),
            status=sanitize_value(entry.get("status") or entry.get("state") or ""),
            url=sanitize_value(entry.get("url") or entry.get("thread_url") or ""),
            updated_at=sanitize_value(entry.get("updated_at") or entry.get("date") or ""),
            labels=[sanitize_value(v) for v in entry.get("labels", []) if v],
            action_hint=sanitize_value(entry.get("action_hint") or ""),
            confidence="fact",
            sensitivity=sanitize_value(entry.get("sensitivity") or "normal"),
            metadata={
                "id": sanitize_value(entry.get("id") or ""),
                "owner": sanitize_value(entry.get("owner") or ""),
                "from": sanitize_value(entry.get("from") or ""),
            },
        )


class LocalOnlyPlaceholderSource(BaseSource):
    def __init__(self, name: str, source_type: str, note: str) -> None:
        super().__init__(name=name, source_type=source_type, enabled=True)
        self.note = note

    def collect(self) -> SourceResult:
        return SourceResult(
            name=self.name,
            status="local_only",
            notes=[self.note],
        )


class LinearReadOnlySource(BaseSource):
    GRAPHQL_ENDPOINT = "https://api.linear.app/graphql"
    PRIORITY_MAP = {
        0: "",
        1: "urgent",
        2: "high",
        3: "normal",
        4: "low",
    }

    def __init__(
        self,
        api_key: Optional[str],
        config: Dict[str, Any],
        mock_path: Optional[Path],
        enabled: bool = True,
    ) -> None:
        super().__init__(name="linear", source_type="linear", enabled=enabled)
        self.api_key = api_key
        self.config = config
        self.mock_path = mock_path

    def collect(self) -> SourceResult:
        result = SourceResult(name=self.name, status="disabled")
        if not self.enabled:
            return result

        use_real_api = bool(self.config.get("use_real_api", True))
        if not use_real_api:
            fallback = self._collect_from_mock("real API disabled in config")
            fallback.name = self.name
            fallback.status = "fallback_mock"
            return fallback

        if not self.api_key:
            fallback = self._collect_from_mock("LINEAR_API_KEY missing")
            fallback.name = self.name
            fallback.status = "fallback_mock"
            fallback.notes.append("Using mock fallback because no API key is configured.")
            return fallback

        try:
            items = self._fetch_real_items()
            result.items = items
            result.status = "ok"
            result.notes.append(
                f"Fetched {len(items)} Linear issue(s) via read-only API within configured scope."
            )
            result.notes.append(self._describe_scope())
            return result
        except Exception as exc:  # pragma: no cover - external I/O
            fallback = self._collect_from_mock(f"API error: {exc}")
            fallback.name = self.name
            fallback.status = "fallback_mock"
            fallback.notes.append("Used mock fallback after Linear API read failure.")
            return fallback

    def _collect_from_mock(self, reason: str) -> SourceResult:
        mock_source = JsonListSource(
            name="linear",
            source_type="linear",
            path=self.mock_path,
            enabled=True,
        )
        result = mock_source.collect()
        result.notes.append(f"Fallback reason: {reason}.")
        return result

    def _fetch_real_items(self) -> List[BriefItem]:
        query = """
        query DoraOSDailyBriefLinear($first: Int!) {
          issues(first: $first) {
            nodes {
              id
              identifier
              title
              description
              priority
              updatedAt
              url
              state {
                name
              }
              team {
                id
                key
                name
              }
              project {
                id
                name
              }
              assignee {
                id
                name
                displayName
                email
              }
            }
          }
        }
        """
        max_items = int(self.config.get("max_items", DEFAULT_SECTION_ITEM_LIMIT))
        fetch_limit = max(max_items * 3, max_items, DEFAULT_SECTION_ITEM_LIMIT)
        payload = {
            "query": query,
            "variables": {"first": fetch_limit},
        }
        request = urllib.request.Request(
            self.GRAPHQL_ENDPOINT,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": self.api_key,
            },
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=20) as response:
            body = json.loads(response.read().decode("utf-8"))
        if body.get("errors"):
            messages = "; ".join(
                clip_text(error.get("message", "Unknown Linear API error"), 120)
                for error in body["errors"]
            )
            raise RuntimeError(messages)

        raw_items = body.get("data", {}).get("issues", {}).get("nodes", [])
        filtered = self._filter_items(raw_items)
        normalized = [self._normalize_item(entry) for entry in filtered[:max_items]]
        return normalized

    def _filter_items(self, raw_items: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
        team_scope = {value.lower() for value in self.config.get("team_scope", []) if value}
        workspace_scope = {value.lower() for value in self.config.get("workspace_scope", []) if value}
        project_scope = {value.lower() for value in self.config.get("project_names", []) if value}
        project_ids = {value for value in self.config.get("project_ids", []) if value}
        state_scope = {value.lower() for value in self.config.get("issue_states", []) if value}
        assignee_scope = {value.lower() for value in self.config.get("assignee_filter", []) if value}
        updated_since_days = self.config.get("updated_since_days")
        updated_cutoff = None
        if isinstance(updated_since_days, int) and updated_since_days >= 0:
            updated_cutoff = datetime.utcnow() - timedelta(days=updated_since_days)

        filtered: List[Dict[str, Any]] = []
        for entry in raw_items:
            team = entry.get("team") or {}
            project = entry.get("project") or {}
            state = entry.get("state") or {}
            assignee = entry.get("assignee") or {}

            team_values = {
                sanitize_value(team.get("key")).lower(),
                sanitize_value(team.get("name")).lower(),
                sanitize_value(team.get("id")).lower(),
            }
            if team_scope and not team_scope.intersection(team_values):
                continue
            if workspace_scope and not workspace_scope.intersection(team_values):
                continue

            project_name = sanitize_value(project.get("name")).lower()
            project_id = sanitize_value(project.get("id"))
            if project_scope and project_name not in project_scope:
                continue
            if project_ids and project_id not in project_ids:
                continue

            state_name = sanitize_value(state.get("name")).lower()
            if state_scope and state_name not in state_scope:
                continue

            assignee_values = {
                sanitize_value(assignee.get("name")).lower(),
                sanitize_value(assignee.get("displayName")).lower(),
                sanitize_value(assignee.get("email")).lower(),
                sanitize_value(assignee.get("id")).lower(),
            }
            if assignee_scope and not assignee_scope.intersection(assignee_values):
                continue

            if updated_cutoff:
                updated_at = parse_iso_date(entry.get("updatedAt"))
                if updated_at and updated_at < updated_cutoff:
                    continue

            filtered.append(entry)
        filtered.sort(
            key=lambda item: parse_iso_date(item.get("updatedAt")) or datetime.min,
            reverse=True,
        )
        return filtered

    def _normalize_item(self, entry: Dict[str, Any]) -> BriefItem:
        state = entry.get("state") or {}
        project = entry.get("project") or {}
        team = entry.get("team") or {}
        identifier = sanitize_value(entry.get("identifier"))
        priority_label = self.PRIORITY_MAP.get(entry.get("priority") or 0, "")
        title = sanitize_value(entry.get("title") or "Untitled issue")
        if identifier:
            title = f"{identifier} — {title}"
        summary = sanitize_value(entry.get("description") or "")
        summary = clip_text(summary, 140)
        if not summary:
            summary = "No safe summary available."
        status_parts = [sanitize_value(state.get("name"))]
        if priority_label:
            status_parts.append(f"priority: {priority_label}")
        return BriefItem(
            source_type="linear",
            title=title,
            summary=summary,
            project=sanitize_value(project.get("name") or team.get("key") or team.get("name") or ""),
            status=" | ".join(part for part in status_parts if part),
            url=sanitize_value(entry.get("url") or ""),
            updated_at=sanitize_value(entry.get("updatedAt") or ""),
            labels=[],
            action_hint="Review issue state and confirm whether it should affect today’s priorities.",
            confidence="fact",
            metadata={
                "id": sanitize_value(entry.get("id") or ""),
                "identifier": identifier,
                "priority": priority_label,
                "team": sanitize_value(team.get("key") or team.get("name") or ""),
                "updated_at": sanitize_value(entry.get("updatedAt") or ""),
            },
        )

    def _describe_scope(self) -> str:
        fragments: List[str] = []
        for key in (
            "workspace_scope",
            "team_scope",
            "project_names",
            "project_ids",
            "issue_states",
            "assignee_filter",
        ):
            values = self.config.get(key, [])
            if values:
                fragments.append(f"{key}={','.join(str(v) for v in values)}")
        if self.config.get("updated_since_days") is not None:
            fragments.append(f"updated_since_days={self.config.get('updated_since_days')}")
        if self.config.get("max_items") is not None:
            fragments.append(f"max_items={self.config.get('max_items')}")
        return "Scope: " + ("; ".join(fragments) if fragments else "none")


class ObsidianVaultSource(BaseSource):
    def __init__(
        self,
        vault_path: Path,
        include_paths: Sequence[str],
        exclude_paths: Sequence[str],
        enabled: bool = True,
    ) -> None:
        super().__init__(name="obsidian", source_type="obsidian", enabled=enabled)
        self.vault_path = vault_path
        self.include_paths = list(include_paths)
        self.exclude_paths = list(exclude_paths)

    def collect(self) -> SourceResult:
        result = SourceResult(name=self.name, status="disabled")
        if not self.enabled:
            return result
        if not self.vault_path.exists():
            result.status = "missing"
            result.errors.append(f"Vault path not found: {self.vault_path}")
            return result

        result.status = "ok"
        files: List[Path] = []
        for include_path in self.include_paths:
            target = self.vault_path / include_path
            if target.is_file() and target.suffix == ".md":
                files.append(target)
            elif target.is_dir():
                files.extend(target.rglob("*.md"))

        filtered: List[Path] = []
        for file_path in files:
            rel = file_path.relative_to(self.vault_path).as_posix()
            if any(rel.startswith(prefix) for prefix in self.exclude_paths):
                continue
            filtered.append(file_path)

        filtered = sorted(set(filtered), key=lambda p: p.stat().st_mtime, reverse=True)

        for file_path in filtered[:DEFAULT_SECTION_ITEM_LIMIT]:
            rel = file_path.relative_to(self.vault_path).as_posix()
            text = file_path.read_text(encoding="utf-8", errors="ignore")
            lines = [line.strip() for line in text.splitlines() if line.strip()]
            summary = clip_text(" ".join(lines[:6]))
            result.items.append(
                BriefItem(
                    source_type="obsidian",
                    title=file_path.stem,
                    summary=summary,
                    project=rel.split("/", 1)[0] if "/" in rel else rel,
                    status="note",
                    url=rel,
                    updated_at=datetime.fromtimestamp(file_path.stat().st_mtime).strftime("%Y-%m-%d"),
                    labels=[],
                    action_hint="Review for context carryover.",
                    confidence="fact",
                )
            )
        result.notes.append(f"Loaded {len(result.items)} recent note(s) from scoped vault paths.")
        return result


class SQLiteMemorySource(BaseSource):
    def __init__(self, db_path: Optional[Path], enabled: bool = True) -> None:
        super().__init__(name="memory", source_type="memory", enabled=enabled)
        self.db_path = db_path

    def collect(self) -> SourceResult:
        result = SourceResult(name=self.name, status="disabled")
        if not self.enabled:
            return result
        if not self.db_path:
            result.status = "not_configured"
            result.notes.append("No memory DB path configured.")
            return result
        if not self.db_path.exists():
            result.status = "missing"
            result.notes.append(f"Memory DB not found: {self.db_path}")
            return result

        try:
            connection = sqlite3.connect(self.db_path)
            connection.row_factory = sqlite3.Row
            rows: List[sqlite3.Row] = []
            table_name = self._detect_table(connection)
            if table_name:
                rows = list(
                    connection.execute(
                        f"""
                        SELECT *
                        FROM {table_name}
                        ORDER BY COALESCE(updated_at, created_at, date('now')) DESC
                        LIMIT ?
                        """,
                        (DEFAULT_SECTION_ITEM_LIMIT,),
                    )
                )
            connection.close()
            result.items = [self._row_to_item(row) for row in rows]
            result.status = "ok"
            result.notes.append(f"Loaded {len(result.items)} memory item(s).")
            return result
        except Exception as exc:  # pragma: no cover - safe fallback
            result.status = "error"
            result.errors.append(f"Could not read memory DB safely: {exc}")
            return result

    def _detect_table(self, connection: sqlite3.Connection) -> Optional[str]:
        candidates = {"memory_items", "memories", "notes", "entries"}
        rows = connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        ).fetchall()
        for row in rows:
            name = row[0]
            if name in candidates:
                return name
        return rows[0][0] if rows else None

    def _row_to_item(self, row: sqlite3.Row) -> BriefItem:
        fields = {key: row[key] for key in row.keys()}
        return BriefItem(
            source_type="memory",
            title=sanitize_value(fields.get("title") or fields.get("key") or "Memory item"),
            summary=sanitize_value(fields.get("summary") or fields.get("content") or fields.get("value") or ""),
            project=sanitize_value(fields.get("project") or fields.get("scope") or ""),
            status=sanitize_value(fields.get("status") or "memory"),
            url=sanitize_value(fields.get("source") or ""),
            updated_at=sanitize_value(fields.get("updated_at") or fields.get("created_at") or ""),
            labels=[],
            action_hint="Consider whether this remains durable and relevant.",
            confidence="fact",
        )


def group_by_project(items: Iterable[BriefItem]) -> Dict[str, List[BriefItem]]:
    grouped: Dict[str, List[BriefItem]] = {}
    for item in items:
        key = item.project or "General"
        grouped.setdefault(key, []).append(item)
    return grouped


def derive_snapshot(all_items: Sequence[BriefItem]) -> str:
    if not all_items:
        return "Limited context was available today. The brief is based on safe local notes and configured mock inputs."
    source_types = sorted({item.source_type for item in all_items})
    projects = sorted({item.project for item in all_items if item.project})
    return (
        f"This brief combines {len(all_items)} scoped item(s) from {', '.join(source_types)}. "
        f"Current signal clusters around {', '.join(projects[:3]) if projects else 'general operations'}."
    )


def select_message_items(items: Sequence[BriefItem]) -> List[BriefItem]:
    return [item for item in items if item.source_type == "gmail"][:DEFAULT_SECTION_ITEM_LIMIT]


def select_task_items(items: Sequence[BriefItem]) -> List[BriefItem]:
    return [
        item for item in items if item.source_type in {"github", "linear"}
    ][: DEFAULT_SECTION_ITEM_LIMIT]


def derive_priorities(all_items: Sequence[BriefItem]) -> List[str]:
    priorities: List[str] = []
    task_items = select_task_items(all_items)
    message_items = select_message_items(all_items)

    if task_items:
        priorities.append(
            "Suggestion: review the highest-signal GitHub and Linear items first, then confirm what actually matters today."
        )
    if message_items:
        priorities.append(
            "Suggestion: triage communication items into reply, defer, or reference before they spill into project work."
        )
    project_names = [item.project for item in all_items if item.project]
    if project_names:
        top_project = max(set(project_names), key=project_names.count)
        priorities.append(
            f"Suggestion: protect one focused block for {top_project}, since it appears repeatedly across the scoped sources."
        )
    if not priorities:
        priorities.append(
            "Suggestion: use today to clean the AI inbox, refresh project briefs, and tighten source scopes before expanding automation."
        )
    return priorities[:3]


def candidate_contains_secret(text: str) -> bool:
    text = text.lower()
    return any(keyword in text for keyword in ("password", "secret", "api key", "token", "private key"))


def derive_candidate_type(item: BriefItem) -> str:
    text = f"{item.title} {item.summary}".lower()
    if any(keyword in text for keyword in ("decision", "policy", "approved", "rejected")):
        return "decision"
    if any(keyword in text for keyword in ("workflow", "process", "review", "meeting", "brief", "triage")):
        return "workflow"
    if any(keyword in text for keyword in ("preference", "prefer", "style")):
        return "preference"
    return "project-context"


def derive_candidate_reason(item: BriefItem, candidate_type: str) -> str:
    if candidate_type == "decision":
        return f"Source appears to capture a reusable decision worth preserving from {item.source_type}."
    if candidate_type == "workflow":
        return f"Source appears to describe an operational workflow or review pattern from {item.source_type}."
    if candidate_type == "preference":
        return f"Source appears to encode a recurring preference that may matter in future collaboration."
    return f"Source may contain project context that could remain useful beyond today."


def derive_candidate_confidence(item: BriefItem, candidate_type: str) -> str:
    if item.source_type == "obsidian" and candidate_type in {"decision", "workflow"}:
        return "high"
    if item.source_type in {"linear", "github"}:
        return "medium"
    return "low"


def derive_candidate_sensitivity(item: BriefItem) -> str:
    if item.source_type == "gmail":
        return "medium"
    if candidate_contains_secret(f"{item.title} {item.summary}"):
        return "high"
    return "low"


def derive_candidate_lifespan(item: BriefItem, candidate_type: str) -> str:
    if candidate_type in {"workflow", "preference"}:
        return "90d"
    if candidate_type == "decision":
        return "project-lifetime"
    if item.project:
        return "30d"
    return "14d"


def should_candidate_be_queued(item: BriefItem) -> bool:
    text = f"{item.title} {item.summary}".lower()
    if candidate_contains_secret(text):
        return False
    if item.source_type == "gmail" and item.sensitivity == "high":
        return False
    return any(
        keyword in text
        for keyword in (
            "decision",
            "workflow",
            "preference",
            "process",
            "policy",
            "review",
            "meeting",
            "brief",
            "triage",
        )
    )


def build_candidate_fingerprint(item: BriefItem, candidate_type: str, proposed_memory: str) -> str:
    raw = "|".join(
        [
            item.source_type,
            candidate_type,
            item.project.lower(),
            proposed_memory.lower(),
        ]
    )
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]


def derive_memory_candidate_records(items: Sequence[BriefItem], brief_date: str) -> List[MemoryCandidate]:
    candidates: List[MemoryCandidate] = []
    seen: set[str] = set()
    for item in items:
        if not should_candidate_be_queued(item):
            continue
        candidate_type = derive_candidate_type(item)
        proposed_memory = clip_text(
            " ".join(
                part
                for part in [
                    item.project,
                    item.title,
                    item.summary,
                ]
                if part
            ),
            180,
        )
        proposed_memory = strip_markdown_noise(proposed_memory)
        if not proposed_memory:
            continue
        fingerprint = build_candidate_fingerprint(item, candidate_type, proposed_memory)
        if fingerprint in seen:
            continue
        seen.add(fingerprint)
        candidate_id = f"mem-{brief_date.replace('-', '')}-{fingerprint[:6]}"
        review_file = f"{brief_date} Memory Review.md"
        candidates.append(
            MemoryCandidate(
                candidate_id=candidate_id,
                date=brief_date,
                source=item.source_type,
                type=candidate_type,
                proposed_memory=proposed_memory,
                reason=derive_candidate_reason(item, candidate_type),
                confidence=derive_candidate_confidence(item, candidate_type),
                sensitivity_level=derive_candidate_sensitivity(item),
                suggested_lifespan=derive_candidate_lifespan(item, candidate_type),
                status="pending",
                fingerprint=fingerprint,
                review_file=review_file,
            )
        )
    return candidates[:DEFAULT_FACT_BULLET_LIMIT]


def format_memory_candidate_for_brief(candidate: MemoryCandidate) -> str:
    return (
        f"Candidate: {candidate.proposed_memory} "
        f"[source: {candidate.source}, type: {candidate.type}, confidence: {candidate.confidence}, status: {candidate.status}]. "
        "Approval required before promotion to approved memory."
    )


def load_queue_state(queue_path: Path) -> List[MemoryCandidate]:
    if not queue_path.exists():
        return []
    payload = safe_read_json(queue_path)
    return [MemoryCandidate.from_dict(entry) for entry in payload]


def save_queue_state(queue_path: Path, candidates: Sequence[MemoryCandidate]) -> None:
    queue_path.parent.mkdir(parents=True, exist_ok=True)
    queue_path.write_text(
        json.dumps([candidate.to_dict() for candidate in candidates], indent=2, ensure_ascii=True) + "\n",
        encoding="utf-8",
    )


def upsert_memory_candidates(existing: Sequence[MemoryCandidate], new_candidates: Sequence[MemoryCandidate]) -> tuple[List[MemoryCandidate], int]:
    indexed: Dict[str, MemoryCandidate] = {candidate.fingerprint: candidate for candidate in existing}
    added = 0
    for candidate in new_candidates:
        if candidate.fingerprint in indexed:
            continue
        indexed[candidate.fingerprint] = candidate
        added += 1
    merged = sorted(indexed.values(), key=lambda candidate: (candidate.date, candidate.candidate_id))
    return merged, added


def build_memory_review_markdown(review_date: str, candidates: Sequence[MemoryCandidate], output_path: Path) -> str:
    lines: List[str] = []
    lines.append(f"# Memory Review — {review_date}")
    lines.append("")
    lines.append("Human approval is required before any candidate becomes approved memory.")
    lines.append("")
    if not candidates:
        lines.append("- No memory candidates for this date.")
        return "\n".join(lines) + "\n"

    for candidate in candidates:
        lines.append(f"## {candidate.candidate_id}")
        lines.append(f"- Date: {candidate.date}")
        lines.append(f"- Source: {candidate.source}")
        lines.append(f"- Type: {candidate.type}")
        lines.append(f"- Proposed memory: {candidate.proposed_memory}")
        lines.append(f"- Reason: {candidate.reason}")
        lines.append(f"- Confidence: {candidate.confidence}")
        lines.append(f"- Sensitivity level: {candidate.sensitivity_level}")
        lines.append(f"- Suggested lifespan: {candidate.suggested_lifespan}")
        lines.append(f"- Status: {candidate.status}")
        if candidate.decided_at:
            lines.append(f"- Decided at: {candidate.decided_at}")
        lines.append("- Approval notes: ")
        lines.append("")
    lines.append(f"_Output path: `{output_path.as_posix()}`_")
    return "\n".join(lines) + "\n"


def render_all_review_files(vault_path: Path, candidates: Sequence[MemoryCandidate]) -> Dict[str, str]:
    review_dir = (vault_path / MEMORY_REVIEW_DIR).resolve()
    review_dir.mkdir(parents=True, exist_ok=True)
    grouped: Dict[str, List[MemoryCandidate]] = {}
    for candidate in candidates:
        grouped.setdefault(candidate.date, []).append(candidate)
    rendered: Dict[str, str] = {}
    for review_date, group in grouped.items():
        output_path = review_dir / f"{review_date} Memory Review.md"
        rendered[str(output_path)] = build_memory_review_markdown(review_date, group, output_path)
    return rendered


def write_review_files(rendered_files: Dict[str, str]) -> None:
    for path_str, content in rendered_files.items():
        path = Path(path_str)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")


def build_approved_memory_markdown(candidates: Sequence[MemoryCandidate], output_path: Path) -> str:
    approved = [candidate for candidate in candidates if candidate.status == "approved"]
    lines: List[str] = []
    lines.append("# Approved Memory")
    lines.append("")
    lines.append("Only locally approved memory is stored here. No external memory systems are updated automatically.")
    lines.append("")
    if not approved:
        lines.append("- No approved memory entries yet.")
    else:
        for candidate in approved:
            lines.append(f"## {candidate.candidate_id}")
            lines.append(f"- Approved at: {candidate.approved_at or candidate.decided_at or candidate.date}")
            lines.append(f"- Source: {candidate.source}")
            lines.append(f"- Type: {candidate.type}")
            lines.append(f"- Proposed memory: {candidate.proposed_memory}")
            lines.append(f"- Reason: {candidate.reason}")
            lines.append(f"- Confidence: {candidate.confidence}")
            lines.append(f"- Sensitivity level: {candidate.sensitivity_level}")
            lines.append(f"- Suggested lifespan: {candidate.suggested_lifespan}")
            lines.append("")
    lines.append(f"_Output path: `{output_path.as_posix()}`_")
    return "\n".join(lines) + "\n"


def update_candidate_status(
    candidates: Sequence[MemoryCandidate],
    candidate_id: str,
    new_status: str,
    now_value: str,
) -> tuple[List[MemoryCandidate], Optional[MemoryCandidate]]:
    updated: List[MemoryCandidate] = []
    target: Optional[MemoryCandidate] = None
    for candidate in candidates:
        if candidate.candidate_id == candidate_id:
            candidate.status = new_status
            candidate.decided_at = now_value
            if new_status == "approved":
                candidate.approved_at = now_value
            target = candidate
        updated.append(candidate)
    return updated, target


def format_pending_candidates(candidates: Sequence[MemoryCandidate]) -> str:
    pending = [candidate for candidate in candidates if candidate.status == "pending"]
    if not pending:
        return "No pending memory candidates.\n"
    lines = ["Pending memory candidates:", ""]
    for candidate in pending:
        lines.append(
            f"- {candidate.candidate_id} | {candidate.date} | {candidate.source} | {candidate.type} | {candidate.confidence} | {candidate.proposed_memory}"
        )
    return "\n".join(lines) + "\n"


def derive_risks(items: Sequence[BriefItem], source_results: Sequence[SourceResult]) -> List[str]:
    risks: List[str] = []
    failed = [result.name for result in source_results if result.status in {"error", "missing"}]
    if failed:
        risks.append(
            f"Some sources were unavailable or incomplete: {', '.join(failed)}. Today’s brief may underrepresent those workstreams."
        )
    if any(item.source_type == "gmail" for item in items):
        risks.append(
            "Communication summaries are intentionally compressed. Open the original thread before responding to anything sensitive or ambiguous."
        )
    if any(item.source_type == "memory" for item in items):
        risks.append(
            "Memory items are advisory only. Re-validate them against current project notes before treating them as durable truth."
        )
    if not risks:
        risks.append("No major blockers detected from the configured scopes, but this prototype does not yet validate live external APIs.")
    return risks[:DEFAULT_FACT_BULLET_LIMIT]


def derive_suggested_actions(items: Sequence[BriefItem]) -> List[str]:
    actions: List[str] = []
    for item in select_task_items(items)[:3]:
        verb = "Review"
        if item.source_type == "linear":
            verb = "Confirm"
        elif item.source_type == "github":
            verb = "Inspect"
        actions.append(
            f"Suggestion: {verb} `{item.title}` and decide whether it belongs in today’s top three."
        )
    if not actions:
        actions.append("Suggestion: review the brief, approve any useful follow-ups, and leave the rest as reference only.")
    return actions


def format_item_bullet(item: BriefItem, include_project: bool = True) -> str:
    parts = [item.title]
    if include_project and item.project:
        parts.append(f"[{item.project}]")
    if item.status:
        parts.append(f"({item.status})")
    body = " ".join(parts)
    if item.summary:
        body += f": {item.summary}"
    return f"- {body}"


def format_source_log(source_results: Sequence[SourceResult]) -> List[str]:
    lines: List[str] = []
    for result in source_results:
        note = "; ".join(result.notes) if result.notes else "No additional notes."
        lines.append(f"- {result.name}: {result.status}. {note}")
    return lines


def build_markdown(
    brief_date: str,
    source_results: Sequence[SourceResult],
    output_path: Path,
) -> str:
    all_items = [item for result in source_results for item in result.items]
    grouped_projects = group_by_project(
        [item for item in all_items if item.source_type in {"obsidian", "github", "linear", "memory"}]
    )
    message_items = select_message_items(all_items)
    task_items = select_task_items(all_items)
    priorities = derive_priorities(all_items)
    suggested_actions = derive_suggested_actions(all_items)
    memory_candidates = derive_memory_candidate_records(all_items, brief_date)
    risks = derive_risks(all_items, source_results)
    source_log = format_source_log(source_results)

    lines: List[str] = []
    lines.append(f"# Daily Brief — {brief_date}")
    lines.append("")
    lines.append("## 1. Today’s Snapshot")
    lines.append(derive_snapshot(all_items))
    lines.append("")
    lines.append("## 2. Active Projects")
    if grouped_projects:
        for project, items in list(grouped_projects.items())[:DEFAULT_SECTION_ITEM_LIMIT]:
            lines.append(f"- {project}: {clip_text(' | '.join(item.title for item in items[:3]), 180)}")
    else:
        lines.append("- No scoped project context was available.")
    lines.append("")
    lines.append("## 3. Messages / Follow-ups")
    if message_items:
        for item in message_items:
            lines.append(format_item_bullet(item))
    else:
        lines.append("- No scoped communication items were included.")
    lines.append("")
    lines.append("## 4. Tasks / Decisions")
    if task_items:
        for item in task_items:
            lines.append(format_item_bullet(item))
    else:
        lines.append("- No scoped GitHub or Linear task items were included.")
    lines.append("")
    lines.append("## 5. Suggested Priorities")
    for priority in priorities:
        lines.append(f"- {priority}")
    lines.append("")
    lines.append("## 6. Suggested Actions")
    for action in suggested_actions:
        lines.append(f"- {action}")
    lines.append("")
    lines.append("## 7. Memory Candidates")
    if memory_candidates:
        for candidate in memory_candidates:
            lines.append(f"- {format_memory_candidate_for_brief(candidate)}")
    else:
        lines.append("- No memory candidates were surfaced from the current scope.")
    lines.append("")
    lines.append("## 8. Risks / Blockers")
    for risk in risks:
        lines.append(f"- {risk}")
    lines.append("")
    lines.append("## 9. Approval Boundaries")
    lines.append("- No durable memory writes were made.")
    lines.append("- No emails were sent.")
    lines.append("- No GitHub or Linear tasks were changed.")
    lines.append("- This pipeline only writes the daily brief markdown file.")
    lines.append("")
    lines.append("## 10. Source Log")
    lines.extend(source_log)
    lines.append("")
    lines.append(f"_Output path: `{output_path.as_posix()}`_")
    return "\n".join(lines) + "\n"


def resolve_path(root: Path, value: Optional[str]) -> Optional[Path]:
    if not value:
        return None
    candidate = Path(os.path.expandvars(value))
    if not candidate.is_absolute():
        candidate = (root / candidate).resolve()
    return candidate


def load_config(config_path: Path) -> Dict[str, Any]:
    return safe_read_json(config_path)


def ensure_output_dir(vault_path: Path, configured_output_dir: Optional[str]) -> Path:
    configured = Path(configured_output_dir) if configured_output_dir else SAFE_OUTPUT_DIR
    if configured.is_absolute():
        output_dir = configured
    else:
        output_dir = (vault_path / configured).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    return output_dir


def build_sources(root: Path, config: Dict[str, Any], local_only: bool) -> List[BaseSource]:
    vault_path = resolve_path(root, os.environ.get("OBSIDIAN_VAULT_PATH")) or root / "obsidian" / "DoraOS"
    memory_db = resolve_path(root, os.environ.get("MEMORY_DB_PATH"))
    sources_cfg = config.get("sources", {})

    obsidian_cfg = sources_cfg.get("obsidian", {})
    github_cfg = sources_cfg.get("github", {})
    linear_cfg = sources_cfg.get("linear", {})
    gmail_cfg = sources_cfg.get("gmail", {})
    memory_cfg = sources_cfg.get("memory", {})

    sources: List[BaseSource] = [
        ObsidianVaultSource(
            vault_path=vault_path,
            include_paths=obsidian_cfg.get("include_paths", ["Projects", "Resources"]),
            exclude_paths=obsidian_cfg.get(
                "exclude_paths",
                [
                    "Archive",
                    "Areas",
                    "Areas/Health/Journals",
                    "Resources/AI Briefs",
                    "Resources/OpenHuman",
                    "Resources/AI Memory Review",
                    "Resources/AI Memory",
                    "Resources/AI Weekly Reviews",
                    "Resources/Research Sources",
                    "Resources/Notion Sync",
                ],
            ),
            enabled=obsidian_cfg.get("enabled", True),
        ),
        SQLiteMemorySource(
            db_path=memory_db or resolve_path(root, memory_cfg.get("db_path")),
            enabled=memory_cfg.get("enabled", True),
        ),
    ]

    if local_only:
        sources.extend(
            [
                LocalOnlyPlaceholderSource(
                    name="github",
                    source_type="github",
                    note="Skipped because --local-only was set.",
                ),
                LocalOnlyPlaceholderSource(
                    name="linear",
                    source_type="linear",
                    note="Skipped because --local-only was set. Mock fallback is disabled in local-only mode.",
                ),
                LocalOnlyPlaceholderSource(
                    name="gmail",
                    source_type="gmail",
                    note="Skipped because --local-only was set.",
                ),
            ]
        )
    else:
        sources.extend(
            [
                JsonListSource(
                    name="github",
                    source_type="github",
                    path=resolve_path(root, github_cfg.get("mock_path")),
                    enabled=github_cfg.get("enabled", True),
                ),
                LinearReadOnlySource(
                    api_key=os.environ.get("LINEAR_API_KEY", "").strip() or None,
                    config=linear_cfg,
                    mock_path=resolve_path(root, linear_cfg.get("mock_path")),
                    enabled=linear_cfg.get("enabled", True),
                ),
                JsonListSource(
                    name="gmail",
                    source_type="gmail",
                    path=resolve_path(root, gmail_cfg.get("mock_path")),
                    enabled=gmail_cfg.get("enabled", True),
                ),
            ]
        )
    return sources


def write_output(output_path: Path, content: str) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(content, encoding="utf-8")


def collect_source_results(
    root: Path,
    config: Dict[str, Any],
    local_only: bool,
    selected_sources: set[str],
    logger: logging.Logger,
) -> List[SourceResult]:
    source_objects = build_sources(root, config, local_only=local_only)
    source_results: List[SourceResult] = []
    for source in source_objects:
        if selected_sources and source.name not in selected_sources:
            continue
        result = source.collect()
        source_results.append(result)
        for error in result.errors:
            logger.warning("%s: %s", source.name, error)
    return source_results


def handle_memory_review(
    root: Path,
    vault_path: Path,
    config: Dict[str, Any],
    logger: logging.Logger,
    args: argparse.Namespace,
    selected_sources: set[str],
) -> int:
    queue_path = root / QUEUE_STATE_FILE
    approved_memory_path = (vault_path / APPROVED_MEMORY_FILE).resolve()
    now_value = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    candidates = load_queue_state(queue_path)

    if args.list_pending:
        print(format_pending_candidates(candidates))
        logger.info("Listed %s pending memory candidate(s)", len([c for c in candidates if c.status == "pending"]))
        return 0

    if args.approve or args.reject or args.archive:
        target_id = args.approve or args.reject or args.archive
        new_status = "approved" if args.approve else "rejected" if args.reject else "archived"
        updated_candidates, target = update_candidate_status(candidates, target_id, new_status, now_value)
        if not target:
            print(f"Memory candidate not found: {target_id}", file=sys.stderr)
            return 1
        rendered_files = render_all_review_files(vault_path, updated_candidates)
        approved_memory_markdown = build_approved_memory_markdown(updated_candidates, approved_memory_path)
        if args.dry_run:
            print(f"Dry run: would mark {target_id} as {new_status}.")
            print(approved_memory_markdown if new_status == "approved" and args.stdout else "")
        else:
            save_queue_state(queue_path, updated_candidates)
            write_review_files(rendered_files)
            approved_memory_path.parent.mkdir(parents=True, exist_ok=True)
            approved_memory_path.write_text(approved_memory_markdown, encoding="utf-8")
            print(f"Updated {target_id} to {new_status}.")
        logger.info("Memory candidate %s set to %s", target_id, new_status)
        return 0

    source_results = collect_source_results(root, config, args.local_only, selected_sources, logger)
    all_items = [item for result in source_results for item in result.items]
    new_candidates = derive_memory_candidate_records(all_items, args.date)
    merged_candidates, added_count = upsert_memory_candidates(candidates, new_candidates)
    rendered_files = render_all_review_files(vault_path, merged_candidates)
    review_output = (vault_path / MEMORY_REVIEW_DIR / f"{args.date} Memory Review.md").resolve()
    review_content = rendered_files.get(str(review_output), build_memory_review_markdown(args.date, [], review_output))
    rendered_files.setdefault(str(review_output), review_content)
    approved_memory_markdown = build_approved_memory_markdown(merged_candidates, approved_memory_path)

    if args.stdout or args.dry_run:
        print(review_content)
    if not args.dry_run:
        save_queue_state(queue_path, merged_candidates)
        write_review_files(rendered_files)
        approved_memory_path.parent.mkdir(parents=True, exist_ok=True)
        if not approved_memory_path.exists():
            approved_memory_path.write_text(approved_memory_markdown, encoding="utf-8")
        elif any(candidate.status == "approved" for candidate in merged_candidates):
            approved_memory_path.write_text(approved_memory_markdown, encoding="utf-8")
        print(f"Wrote memory review file to {review_output}")
    else:
        print(f"Dry run complete. Output would be written to {review_output}")

    logger.info(
        "Memory review processed for %s with %s new candidate(s), %s total candidate(s)",
        args.date,
        added_count,
        len(merged_candidates),
    )
    return 0


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Generate a DoraOS daily brief.")
    parser.add_argument(
        "--config",
        default="config/daily_brief.config.example.json",
        help="Path to JSON config file.",
    )
    parser.add_argument("--date", default=date.today().isoformat(), help="Brief date in YYYY-MM-DD format.")
    parser.add_argument("--dry-run", action="store_true", help="Render the brief without writing the markdown file.")
    parser.add_argument("--local-only", action="store_true", help="Use only local Obsidian and local memory sources.")
    parser.add_argument("--memory-review", action="store_true", help="Run the memory review queue workflow.")
    parser.add_argument("--list-pending", action="store_true", help="List pending memory review candidates.")
    parser.add_argument("--approve", default="", help="Approve a pending memory candidate by candidate ID.")
    parser.add_argument("--reject", default="", help="Reject a pending memory candidate by candidate ID.")
    parser.add_argument("--archive", default="", help="Archive a memory candidate by candidate ID.")
    parser.add_argument(
        "--sources",
        default="",
        help="Comma-separated source names to include. Defaults to config-enabled sources.",
    )
    parser.add_argument("--stdout", action="store_true", help="Print the brief markdown to stdout.")
    parser.add_argument("--verbose", action="store_true", help="Enable verbose logging.")
    args = parser.parse_args(argv)

    root = Path(__file__).resolve().parents[1]
    load_dotenv(root / ".env")
    config_path = resolve_path(root, args.config)
    if not config_path or not config_path.exists():
        print(f"Config file not found: {args.config}", file=sys.stderr)
        return 1

    config = load_config(config_path)
    log_path = resolve_path(root, config.get("logging", {}).get("file", "logs/daily_brief.log")) or root / "logs" / "daily_brief.log"
    logger = setup_logging(log_path, verbose=args.verbose)

    selected_sources = {
        name.strip()
        for name in args.sources.split(",")
        if name.strip()
    }

    try:
        vault_path = resolve_path(root, os.environ.get("OBSIDIAN_VAULT_PATH")) or root / "obsidian" / "DoraOS"
        if args.memory_review or args.list_pending or args.approve or args.reject or args.archive:
            return handle_memory_review(root, vault_path, config, logger, args, selected_sources)

        relative_output_dir = config.get("brief", {}).get("vault_output_dir")
        output_dir = ensure_output_dir(vault_path, relative_output_dir)
        output_path = output_dir / f"{args.date} Daily Brief.md"

        source_results = collect_source_results(root, config, args.local_only, selected_sources, logger)

        markdown = build_markdown(args.date, source_results, output_path)

        if args.stdout or args.dry_run:
            print(markdown)
        if not args.dry_run:
            write_output(output_path, markdown)
            logger.info("Wrote daily brief to %s", output_path)
            print(f"Wrote daily brief to {output_path}")
        else:
            logger.info("Dry run complete for %s", args.date)
            print(f"Dry run complete. Output would be written to {output_path}")
        return 0
    except Exception as exc:  # pragma: no cover - top-level safety
        logger.exception("Daily brief pipeline failed: %s", exc)
        print("Daily brief generation failed. Check the log for safe diagnostic details.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
