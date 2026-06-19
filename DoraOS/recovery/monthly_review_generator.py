#!/usr/bin/env python3
"""Generate monthly recovery reviews from synced healing journal markdown files."""

from __future__ import annotations

import argparse
import calendar
import subprocess
import sys
from collections import Counter
from datetime import date, datetime
from pathlib import Path
from typing import List, Optional

import frontmatter

from common import DEFAULT_ENV_FILE, GENERATED_END, GENERATED_START, LOG_DIR, ROOT, build_logger, ensure_dir, load_env_file, require_env
from weekly_review_generator import (
    FOG_SCORES,
    JOURNAL_DIR,
    MOOD_SCORES,
    RISK_SCORES,
    SLEEP_SCORES,
    collect_files,
    frontmatter_score,
    normalize_list_field,
    score_series,
)


MONTHLY_DIR = Path("Areas") / "Mental Health" / "Monthly Reviews"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate a monthly recovery review from synced journal files.")
    parser.add_argument("--env-file", default=str(DEFAULT_ENV_FILE))
    parser.add_argument("--vault-path", default="")
    parser.add_argument("--month", default="", help="Month in YYYY-MM format. Defaults to current month.")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--no-notion-sync", action="store_true")
    parser.add_argument("--verbose", action="store_true")
    return parser.parse_args()


def month_range(value: str) -> tuple[date, date]:
    if value:
        year, month = [int(part) for part in value.split("-", 1)]
    else:
        today = date.today()
        year, month = today.year, today.month
    return date(year, month, 1), date(year, month, calendar.monthrange(year, month)[1])


def average_int(values: List[Optional[int]]) -> Optional[float]:
    cleaned = [value for value in values if value is not None]
    if not cleaned:
        return None
    return round(sum(cleaned) / len(cleaned), 1)


def build_review(entries: List[frontmatter.Post], start_date: date, end_date: date) -> str:
    anxiety_values = [int(post.metadata["anxiety"]) for post in entries if post.metadata.get("anxiety") is not None]
    energy_values = [int(post.metadata["energy"]) for post in entries if post.metadata.get("energy") is not None]
    mood_arrow = score_series([frontmatter_score(post.metadata.get("mood"), MOOD_SCORES) for post in entries]) or "→"
    anxiety_arrow = score_series([int(post.metadata["anxiety"]) if post.metadata.get("anxiety") is not None else None for post in entries]) or "→"
    sleep_arrow = score_series([frontmatter_score(post.metadata.get("sleep"), SLEEP_SCORES) for post in entries]) or "→"
    fog_arrow = score_series([frontmatter_score(post.metadata.get("brain_fog"), FOG_SCORES) for post in entries]) or "→"
    risk_arrow = score_series([frontmatter_score(post.metadata.get("risk"), RISK_SCORES) for post in entries]) or "→"

    stressors = Counter(item for post in entries for item in normalize_list_field(post.metadata.get("stressors")))
    wins = Counter(item for post in entries for item in normalize_list_field(post.metadata.get("wins")))
    tomorrow = Counter(item for post in entries for item in normalize_list_field(post.metadata.get("tomorrow_top_3")))
    risk_days = Counter(str(post.metadata.get("risk", "")).strip() or "Unknown" for post in entries)

    lines = [
        "# Monthly Recovery Review",
        "",
        f"_Window: {start_date.isoformat()} to {end_date.isoformat()}_",
        "",
        "## 1. Recovery Snapshot",
        f"- Journal files reviewed: {len(entries)}",
        f"- Average energy: {average_int(energy_values) if energy_values else 'Unknown'}",
        f"- Average anxiety: {average_int(anxiety_values) if anxiety_values else 'Unknown'}",
        f"- Risk distribution: {', '.join(f'{key}: {count}' for key, count in risk_days.most_common()) if risk_days else 'Unknown'}",
        "",
        "## 2. Trends",
        f"- Mood {mood_arrow}",
        f"- Anxiety {anxiety_arrow}",
        f"- Sleep {sleep_arrow}",
        f"- Brain Fog {fog_arrow}",
        f"- Risk {risk_arrow}",
        "",
        "## 3. Repeated Stressors",
    ]
    lines.extend(f"- {item} | seen {count} time(s)" for item, count in stressors.most_common(8)) if stressors else lines.append("- No repeated stressors detected from metadata.")
    lines.extend(["", "## 4. Positive Signals"])
    lines.extend(f"- {item} | seen {count} time(s)" for item, count in wins.most_common(8)) if wins else lines.append("- No repeated positive signals detected yet.")
    lines.extend(["", "## 5. Carryover Needs"])
    lines.extend(f"- {item} | carried {count} time(s)" for item, count in tomorrow.most_common(8)) if tomorrow else lines.append("- No repeated tomorrow top-3 items detected.")
    lines.extend(["", "## 6. Suggested Focus"])
    if risk_arrow == "↑" or anxiety_arrow == "↑":
        lines.append("- Reduce background pressure before adding new commitments.")
    if sleep_arrow == "↓" or fog_arrow == "↑":
        lines.append("- Protect sleep and lower cognitive load.")
    if wins:
        lines.append("- Keep the routines that repeatedly showed up as positive signals.")
    if len(lines) and lines[-1] == "## 6. Suggested Focus":
        lines.append("- Maintain stable routines and review stressors gently.")
    lines.extend(["", "## 7. Source Log"])
    lines.extend(f"- {Path(post.metadata.get('_file_path', '')).name}" for post in entries)
    return "\n".join(lines).strip() + "\n"


def upsert_review(path: Path, markdown: str, dry_run: bool) -> None:
    manual_section = "\n## Manual Notes\n\n"
    if path.exists():
        existing = frontmatter.load(path)
        content = existing.content
        if GENERATED_START in content and GENERATED_END in content:
            start = content.index(GENERATED_START)
            end = content.index(GENERATED_END) + len(GENERATED_END)
            manual = (content[:start] + "\n" + content[end:]).strip()
        else:
            manual = content.strip()
        if "## Manual Notes" not in manual:
            manual = (manual_section + manual).strip() if manual else manual_section.strip()
        final = f"{GENERATED_START}\n{markdown.rstrip()}\n{GENERATED_END}\n\n{manual.strip()}\n"
        post = frontmatter.Post(final, **existing.metadata)
    else:
        final = f"{GENERATED_START}\n{markdown.rstrip()}\n{GENERATED_END}{manual_section}\n"
        post = frontmatter.Post(final)
    if not dry_run:
        ensure_dir(path.parent)
        path.write_text(frontmatter.dumps(post), encoding="utf-8")


def sync_review_to_notion(path: Path, start_date: date, end_date: date, dry_run: bool, logger) -> None:
    sync_script = ROOT / "scripts" / "notion_review_sync.py"
    title = f"Monthly Recovery Review — {start_date.strftime('%Y-%m')}"
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
        "Recovery Monthly",
        "--period-start",
        start_date.isoformat(),
        "--period-end",
        end_date.isoformat(),
        "--period-label",
        start_date.strftime("%Y-%m"),
        "--obsidian-path",
        str(path),
    ]
    if dry_run:
        cmd.append("--dry-run")
    result = subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True, check=False)
    if result.returncode != 0:
        logger.warning("Notion monthly recovery review sync failed: %s", result.stderr.strip() or result.stdout.strip())
    elif result.stdout.strip():
        logger.info(result.stdout.strip())


def main() -> int:
    args = parse_args()
    env = load_env_file(Path(args.env_file).expanduser())
    vault_path = Path(args.vault_path or require_env(env, "OBSIDIAN_VAULT_PATH")).expanduser()
    logger = build_logger("monthly_recovery_review", LOG_DIR / "dora_monthly_recovery_review.log", args.verbose)
    start_date, end_date = month_range(args.month)

    journal_dir = vault_path / JOURNAL_DIR
    review_dir = vault_path / MONTHLY_DIR
    ensure_dir(review_dir)
    files = collect_files(journal_dir, start_date, end_date)
    entries: List[frontmatter.Post] = []
    for path in files:
        post = frontmatter.load(path)
        post.metadata["_file_path"] = str(path)
        entries.append(post)

    markdown = build_review(entries, start_date, end_date)
    target = review_dir / f"{start_date.strftime('%Y-%m')} Monthly Recovery Review.md"
    upsert_review(target, markdown, args.dry_run)
    if args.dry_run:
        logger.info("Dry run complete. Monthly recovery review would be written to: %s", target)
    else:
        logger.info("Wrote monthly recovery review: %s", target)
        if not args.no_notion_sync:
            sync_review_to_notion(target, start_date, end_date, False, logger)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
