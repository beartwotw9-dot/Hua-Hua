#!/usr/bin/env python3
"""Generate weekly recovery reviews from synced healing journal markdown files."""

from __future__ import annotations

import argparse
import subprocess
import sys
from collections import Counter
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

import frontmatter

from common import (
    DEFAULT_ENV_FILE,
    GENERATED_START,
    LOG_DIR,
    ROOT,
    as_int,
    build_logger,
    ensure_dir,
    load_env_file,
    require_env,
    to_iso_date,
)


JOURNAL_DIR = Path("Areas") / "Mental Health" / "Healing Journal"
WEEKLY_DIR = Path("Areas") / "Mental Health" / "Weekly Reviews"

SLEEP_SCORES = {"poor": 1, "low": 1, "fair": 2, "medium": 2, "good": 3, "great": 4}
RISK_SCORES = {"low": 1, "medium": 2, "high": 3, "critical": 4}
FOG_SCORES = {"none": 0, "low": 1, "mild": 1, "medium": 2, "high": 3, "severe": 4}
MOOD_SCORES = {
    "very low": 1,
    "low": 2,
    "foggy": 2,
    "heavy": 2,
    "mixed": 3,
    "stable": 3,
    "calm": 4,
    "hopeful": 4,
    "good": 4,
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate a weekly recovery review from synced journal files.")
    parser.add_argument("--env-file", default=str(DEFAULT_ENV_FILE))
    parser.add_argument("--vault-path", default="")
    parser.add_argument("--days", type=int, default=7)
    parser.add_argument("--end-date", default="")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--no-notion-sync", action="store_true")
    parser.add_argument("--verbose", action="store_true")
    return parser.parse_args()


def score_series(values: List[Optional[int]]) -> Optional[str]:
    series = [value for value in values if value is not None]
    if len(series) < 2:
        return None
    midpoint = max(1, len(series) // 2)
    first = sum(series[:midpoint]) / len(series[:midpoint])
    second = sum(series[midpoint:]) / len(series[midpoint:])
    delta = second - first
    if abs(delta) < 0.2:
        return "→"
    return "↑" if delta > 0 else "↓"


def frontmatter_score(value: Any, mapping: Dict[str, int]) -> Optional[int]:
    if value is None:
        return None
    return mapping.get(str(value).strip().casefold())


def collect_files(journal_dir: Path, start_date: date, end_date: date) -> List[Path]:
    matched: List[Path] = []
    for path in sorted(journal_dir.glob("*.md")):
        try:
            entry_date = datetime.strptime(path.stem[:10], "%Y-%m-%d").date()
        except ValueError:
            continue
        if start_date <= entry_date <= end_date:
            matched.append(path)
    return matched


def normalize_list_field(value: Any) -> List[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return []


def build_review(entries: List[frontmatter.Post], start_date: date, end_date: date) -> str:
    anxiety_arrow = score_series([as_int(post.metadata.get("anxiety")) for post in entries]) or "→"
    sleep_arrow = score_series([frontmatter_score(post.metadata.get("sleep"), SLEEP_SCORES) for post in entries]) or "→"
    fog_arrow = score_series([frontmatter_score(post.metadata.get("brain_fog"), FOG_SCORES) for post in entries]) or "→"
    risk_arrow = score_series([frontmatter_score(post.metadata.get("risk"), RISK_SCORES) for post in entries]) or "→"
    mood_arrow = score_series([frontmatter_score(post.metadata.get("mood"), MOOD_SCORES) for post in entries]) or "→"

    stressors = Counter(
        item
        for post in entries
        for item in normalize_list_field(post.metadata.get("stressors"))
    )
    wins = Counter(
        item
        for post in entries
        for item in normalize_list_field(post.metadata.get("wins"))
    )

    repeated_stressors = [item for item, _ in stressors.most_common(5)]
    positive_signals = [item for item, _ in wins.most_common(5)]

    suggested_focus: List[str] = []
    if anxiety_arrow == "↑" or risk_arrow == "↑":
        suggested_focus.append("lower background anxiety")
    if sleep_arrow == "↓":
        suggested_focus.append("stabilize sleep")
    if fog_arrow == "↑":
        suggested_focus.append("reduce overload")
    if not suggested_focus and mood_arrow in {"→", "↑"}:
        suggested_focus.append("protect stable routines")
    if not suggested_focus:
        suggested_focus.append("review stressors and reduce pressure spikes")

    lines = [
        "# Weekly Recovery Review",
        "",
        f"_Window: {start_date.isoformat()} to {end_date.isoformat()}_",
        "",
        "## Trends",
        f"- Mood {mood_arrow}",
        f"- Anxiety {anxiety_arrow}",
        f"- Sleep {sleep_arrow}",
        f"- Brain Fog {fog_arrow}",
        f"- Risk {risk_arrow}",
        "",
        "## Repeated Stressors",
    ]
    if repeated_stressors:
        lines.extend(f"- {item}" for item in repeated_stressors)
    else:
        lines.append("- No repeated stressors detected from synced metadata")

    lines.extend(["", "## Positive Signals"])
    if positive_signals:
        lines.extend(f"- {item}" for item in positive_signals)
    else:
        lines.append("- No repeated positive signals detected yet")

    lines.extend(["", "## Suggested Focus"])
    lines.extend(f"- {item}" for item in suggested_focus[:3])

    lines.extend(
        [
            "",
            "## Source Log",
            f"- Journal files reviewed: {len(entries)}",
        ]
    )
    lines.extend(f"- {Path(post.metadata.get('_file_path', '')).name}" for post in entries)
    return "\n".join(lines).strip() + "\n"


def upsert_review(path: Path, markdown: str, dry_run: bool) -> None:
    manual_section = "\n## Manual Notes\n\n"
    if path.exists():
        existing = frontmatter.load(path)
        content = existing.content
        if GENERATED_START in content and "<!-- DORAOS_SYNC:GENERATED_END -->" in content:
            start = content.index(GENERATED_START)
            end = content.index("<!-- DORAOS_SYNC:GENERATED_END -->") + len("<!-- DORAOS_SYNC:GENERATED_END -->")
            manual = (content[:start] + "\n" + content[end:]).strip()
        else:
            manual = content.strip()
        if "## Manual Notes" not in manual:
            manual = (manual_section + manual).strip() if manual else manual_section.strip()
        final = f"{GENERATED_START}\n{markdown.rstrip()}\n<!-- DORAOS_SYNC:GENERATED_END -->\n\n{manual.strip()}\n"
        post = frontmatter.Post(final, **existing.metadata)
    else:
        final = f"{GENERATED_START}\n{markdown.rstrip()}\n<!-- DORAOS_SYNC:GENERATED_END -->{manual_section}\n"
        post = frontmatter.Post(final)

    if not dry_run:
        ensure_dir(path.parent)
        path.write_text(frontmatter.dumps(post), encoding="utf-8")


def sync_review_to_notion(path: Path, start_date: date, end_date: date, dry_run: bool, logger) -> None:
    sync_script = ROOT / "scripts" / "notion_review_sync.py"
    title = f"Weekly Recovery Review — {end_date.isocalendar().year}-{end_date.isocalendar().week:02d}"
    cmd = [
        sys.executable,
        str(sync_script),
        "--env-file",
        str(DEFAULT_ENV_FILE),
        "--markdown-file",
        str(path),
        "--title",
        title,
        "--review-type",
        "Recovery Weekly",
        "--period-start",
        start_date.isoformat(),
        "--period-end",
        end_date.isoformat(),
        "--period-label",
        f"{start_date.isoformat()} to {end_date.isoformat()}",
        "--obsidian-path",
        str(path),
    ]
    if dry_run:
        cmd.append("--dry-run")
    result = subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True, check=False)
    if result.returncode != 0:
        logger.warning("Notion recovery review sync failed: %s", result.stderr.strip() or result.stdout.strip())
    elif result.stdout.strip():
        logger.info(result.stdout.strip())


def main() -> int:
    args = parse_args()
    env = load_env_file(Path(args.env_file).expanduser())
    vault_path = Path(args.vault_path or require_env(env, "OBSIDIAN_VAULT_PATH")).expanduser()
    logger = build_logger("weekly_recovery_review", LOG_DIR / "weekly_recovery_review.log", args.verbose)

    end_date = datetime.strptime(args.end_date, "%Y-%m-%d").date() if args.end_date else date.today()
    start_date = end_date - timedelta(days=max(1, args.days) - 1)

    journal_dir = vault_path / JOURNAL_DIR
    review_dir = vault_path / WEEKLY_DIR
    ensure_dir(review_dir)

    files = collect_files(journal_dir, start_date, end_date)
    logger.info("Found %s journal files between %s and %s", len(files), start_date, end_date)
    entries: List[frontmatter.Post] = []
    for path in files:
        post = frontmatter.load(path)
        post.metadata["_file_path"] = str(path)
        entries.append(post)

    markdown = build_review(entries, start_date, end_date)
    year, week, _ = end_date.isocalendar()
    target = review_dir / f"{year}-{week:02d} Weekly Recovery Review.md"
    upsert_review(target, markdown, args.dry_run)
    if args.dry_run:
        logger.info("Dry run complete. Weekly recovery review would be written to: %s", target)
    else:
        logger.info("Wrote weekly recovery review: %s", target)
        if not args.no_notion_sync:
            sync_review_to_notion(target, start_date, end_date, False, logger)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
