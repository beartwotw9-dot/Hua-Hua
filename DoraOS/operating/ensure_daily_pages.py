#!/usr/bin/env python3
"""Create missing DoraOS daily page placeholders without copying stale data."""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta
from pathlib import Path
from typing import Iterable

from common import DEFAULT_ENV_FILE, LOG_DIR, build_logger, ensure_dir, load_env_file, require_env


PAGE_SPECS = [
    ("Resources/Operating Feed", "Gmail Digest.md", "gmail"),
    ("Resources/Operating Feed", "Calendar View.md", "calendar"),
    ("Resources/Operating Feed", "Google Tasks.md", "google-tasks"),
    ("Resources/Operating Feed", "Operating Feed.md", "operating-feed"),
    ("Resources/Research Sources/Daily Digests", "Research Digest.md", "research-digest"),
    ("Resources/AI Briefs", "Daily Brief.md", "daily-brief"),
]


def iter_dates(days: int) -> Iterable[str]:
    today = datetime.now().date()
    for offset in range(days - 1, -1, -1):
        yield (today - timedelta(days=offset)).strftime("%Y-%m-%d")


def nearest_available(base_dir: Path, suffix: str, target_date: str) -> Path | None:
    candidates = []
    for path in base_dir.glob(f"* {suffix}"):
        if not path.is_file():
            continue
        name = path.name
        if len(name) < 10:
            continue
        date_prefix = name[:10]
        try:
            delta = abs((datetime.strptime(date_prefix, "%Y-%m-%d").date() - datetime.strptime(target_date, "%Y-%m-%d").date()).days)
        except ValueError:
            continue
        candidates.append((delta, path.stat().st_mtime, path))
    if not candidates:
        return None
    candidates.sort(key=lambda item: (item[0], -item[1]))
    return candidates[0][2]


def placeholder_page(page_type: str, target_date: str) -> str:
    titles = {
        "gmail": "# Gmail 摘要 | Gmail Digest",
        "calendar": "# 今日日曆 | Calendar Today View",
        "google-tasks": "# Google 提醒清單 | Google Tasks",
        "operating-feed": "# 今日作業頁 | Operating Feed",
        "research-digest": "# 研究摘要 | Research Digest",
        "daily-brief": f"# Daily Brief — {target_date}",
    }
    title = titles.get(page_type, f"# {page_type}")
    return "\n".join(
        [
            title,
            "",
            f"- 生成日期 | generated for: {target_date}",
            "- 狀態 | status: missing-live-data",
            "- 說明 | note: 這一天缺少即時資料；系統只建立空白占位頁，沒有複製舊內容。",
            "",
            "## 待同步 | Pending Sync",
            "",
            "- Live API sync has not produced data for this date yet.",
            "- Do not treat this page as a successful backup until a sync script replaces it.",
            "",
        ]
    ).rstrip() + "\n"


def refresh_today_html(vault_path: Path, today: str, logger, dry_run: bool) -> int:
    feed_dir = vault_path / "Resources" / "Operating Feed"
    root_today = vault_path / "Today.html"
    dated_today = feed_dir / f"{today} Today Hub.html"

    if dated_today.exists():
        latest_html = dated_today
    else:
        candidates = sorted(
            [path for path in feed_dir.glob("* Today Hub.html") if path.is_file()],
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        latest_html = candidates[0] if candidates else None

    if not latest_html:
        logger.warning("No Today Hub HTML source found for %s", today)
        return 0

    content = latest_html.read_text(encoding="utf-8", errors="ignore")
    if latest_html != dated_today:
        content = content.replace(latest_html.stem[:10], today)

    changed = 0
    for target in (dated_today, root_today):
        if target.exists():
            existing = target.read_text(encoding="utf-8", errors="ignore")
            if existing == content:
                continue
        if dry_run:
            logger.info("Would refresh %s from %s", target, latest_html)
            changed += 1
            continue
        target.write_text(content, encoding="utf-8")
        logger.info("Refreshed %s from %s", target, latest_html)
        changed += 1
    return changed


def main() -> int:
    parser = argparse.ArgumentParser(description="Create missing DoraOS daily page placeholders without stale fallback copies.")
    parser.add_argument("--env-file", default=str(DEFAULT_ENV_FILE))
    parser.add_argument("--days", type=int, default=7)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    logger = build_logger("doraos.ensure_daily_pages", LOG_DIR / "dora_ensure_daily_pages.log", verbose=args.verbose)
    config = load_env_file(Path(args.env_file))
    vault_path = Path(require_env(config, "OBSIDIAN_VAULT_PATH")).expanduser()

    created = 0
    for date_str in iter_dates(args.days):
        for rel_dir, suffix, page_type in PAGE_SPECS:
            base_dir = vault_path / rel_dir
            ensure_dir(base_dir)
            target = base_dir / f"{date_str} {suffix}"
            if target.exists():
                continue
            content = placeholder_page(page_type, date_str)
            if args.dry_run:
                logger.info("Would create placeholder %s", target)
                continue
            target.write_text(content, encoding="utf-8")
            created += 1
            logger.info("Created missing-live-data placeholder %s", target)

    created += refresh_today_html(vault_path, datetime.now().date().strftime("%Y-%m-%d"), logger, args.dry_run)

    print(f"backfilled={created}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
