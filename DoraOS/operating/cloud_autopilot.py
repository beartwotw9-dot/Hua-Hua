#!/usr/bin/env python3
"""Cloud-first DoraOS autopilot orchestrator.

Runs the safe, off-machine-friendly daily pipeline so Codex automations do not
need to reason about script ordering each time.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable

from common import DEFAULT_ENV_FILE, LOG_DIR, build_logger, ensure_dir, load_env_file, require_env, wait_for_network


ROOT = Path(__file__).resolve().parents[1]
OPERATING_DIR = ROOT / "operating"
RECOVERY_DIR = ROOT / "recovery"
AUTOMATION_STATUS = ROOT / "obsidian" / "DoraOS" / "Resources" / "Operating Feed" / "Automation Status.md"
API_HEALTH = ROOT / "obsidian" / "DoraOS" / "Resources" / "Operating Feed" / "API Health.md"


@dataclass
class JobResult:
    name: str
    success: bool
    detail: str


@dataclass
class SourceHealth:
    name: str
    status: str
    note: str


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run DoraOS Cloud Autopilot.")
    parser.add_argument("--env-file", default=str(DEFAULT_ENV_FILE))
    parser.add_argument("--mode", choices=["daily", "midday", "weekly", "monthly"], default="daily")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument(
        "--catchup",
        action="store_true",
        help="Skip all jobs if today pages already appear fresh.",
    )
    return parser.parse_args()


def pick_python(script_path: Path) -> str:
    if script_path.is_relative_to(OPERATING_DIR):
        candidate = OPERATING_DIR / ".venv" / "bin" / "python"
        if candidate.exists():
            return str(candidate)
    if script_path.is_relative_to(RECOVERY_DIR):
        candidate = RECOVERY_DIR / ".venv" / "bin" / "python"
        if candidate.exists():
            return str(candidate)
    return sys.executable


def run_job(name: str, script_path: Path, script_args: list[str], logger) -> JobResult:
    python_bin = pick_python(script_path)
    cmd = [python_bin, str(script_path), *script_args]
    try:
        result = subprocess.run(
            cmd,
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            check=False,
        )
    except Exception as exc:  # pragma: no cover
        logger.warning("Job %s crashed before completion: %s", name, exc)
        return JobResult(name=name, success=False, detail=str(exc))

    output = (result.stdout or "").strip()
    error = (result.stderr or "").strip()
    if result.returncode == 0:
        detail = output.splitlines()[-1] if output else "ok"
        logger.info("Job %s succeeded: %s", name, detail)
        return JobResult(name=name, success=True, detail=detail)

    detail = error or output or f"exit {result.returncode}"
    logger.warning("Job %s failed: %s", name, detail)
    return JobResult(name=name, success=False, detail=detail)


def detect_fallback_markers(vault_path: Path, today: str) -> list[str]:
    checks = [
        vault_path / "Resources" / "Operating Feed" / f"{today} Gmail Digest.md",
        vault_path / "Resources" / "Operating Feed" / f"{today} Calendar View.md",
        vault_path / "Resources" / "Operating Feed" / f"{today} Google Tasks.md",
        vault_path / "Resources" / "Operating Feed" / f"{today} Operating Feed.md",
        vault_path / "Resources" / "Research Sources" / "Daily Digests" / f"{today} Research Digest.md",
    ]
    markers: list[str] = []
    for path in checks:
        if not path.exists():
            markers.append(f"missing: {path.name}")
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        if (
            "status: fallback" in text
            or "status: backfilled-fallback" in text
            or "status: low-pressure fallback" in text
            or "status: api-failed" in text
            or "status: api-disabled" in text
            or "status: auth-required" in text
            or "status: missing-live-data" in text
        ):
            markers.append(f"fallback: {path.name}")
    return markers


def collect_source_health(vault_path: Path, today: str, results: Iterable[JobResult]) -> list[SourceHealth]:
    result_map = {job.name: job for job in results}
    notion_status_path = vault_path / "Resources" / "Notion Sync" / "Notion Backup Status.md"
    gmail_path = vault_path / "Resources" / "Operating Feed" / f"{today} Gmail Digest.md"
    calendar_path = vault_path / "Resources" / "Operating Feed" / f"{today} Calendar View.md"
    google_tasks_path = vault_path / "Resources" / "Operating Feed" / f"{today} Google Tasks.md"
    research_path = vault_path / "Resources" / "Research Sources" / "Daily Digests" / f"{today} Research Digest.md"

    def note_status(path: Path) -> str:
        if not path.exists():
            return "error"
        text = path.read_text(encoding="utf-8", errors="ignore")
        if (
            "status: fallback" in text
            or "status: backfilled-fallback" in text
            or "status: low-pressure fallback" in text
            or "status: api-failed" in text
            or "status: api-disabled" in text
            or "status: auth-required" in text
            or "status: missing-live-data" in text
        ):
            return "fallback"
        return "healthy"

    health: list[SourceHealth] = []
    for name, path in [
        ("Gmail", gmail_path),
        ("Calendar", calendar_path),
        ("Google Tasks", google_tasks_path),
        ("Research Digest", research_path),
    ]:
        status = note_status(path)
        msg = {
            "healthy": "即時資料正常 | live data available",
            "fallback": "使用備援資料 | using fallback data",
            "error": "今日頁面不存在 | missing today's note",
        }[status]
        health.append(SourceHealth(name, status, msg))

    notion_job = result_map.get("notion_backup")

    def _parse_notion_counts(detail: str) -> dict:
        out: dict = {}
        for token in (detail or "").split():
            if "=" in token:
                k, _, v = token.partition("=")
                try:
                    out[k] = int(v)
                except ValueError:
                    pass
        return out

    if notion_status_path.exists():
        notion_text = notion_status_path.read_text(encoding="utf-8", errors="ignore")
        nc = _parse_notion_counts(notion_job.detail if notion_job else "")
        live_n = nc.get("live", -1)
        fallback_n = nc.get("fallback_used", 0)
        backed_n = nc.get("backed_up", -1)
        failed_n = nc.get("failed", 0)

        if "## Limitations" in notion_text and "no sources backed up" in notion_text:
            notion_status = "fallback"
            notion_note = "未抓到 Notion 新資料 | no fresh Notion backup today"
        elif live_n == 0 and backed_n > 0:
            # ALL backups were restored from previous snapshots — none are live
            notion_status = "fallback"
            notion_note = (
                f"所有 {backed_n} 個備份均為快照回復版（無即時資料）"
                f" | all {backed_n} backups used previous snapshots (no live data)"
            )
        elif fallback_n > 0:
            notion_status = "fallback"
            notion_note = (
                f"部分備份使用快照回復（{fallback_n} 個 fallback，{max(live_n,0)} 個即時）"
                f" | {fallback_n} fallback snapshot(s), {max(live_n,0)} live"
            )
        elif failed_n > 0 and live_n <= 0:
            notion_status = "error"
            notion_note = f"Notion 備份失敗 {failed_n} 個，無可用資料 | {failed_n} source(s) failed with no fallback"
        elif "## Limitations" in notion_text:
            notion_status = "fallback"
            notion_note = "部分 Notion 來源有限制 | some Notion sources had limitations"
        else:
            notion_status = "healthy"
            notion_note = f"Notion 備份正常（{max(live_n, backed_n, 0)} 個即時）| Notion backup healthy"
    else:
        notion_status = "error"
        notion_note = "找不到 Notion Backup Status | missing Notion backup status"
    health.append(SourceHealth("Notion", notion_status, notion_note))
    morning_brief_job = result_map.get("morning_brief_notion")
    if morning_brief_job:
        if morning_brief_job.success:
            health.append(SourceHealth("Morning Brief Notion Row", "healthy", "今日 Notion 晨報 row 已確認或建立 | today's Notion morning row exists"))
        else:
            health.append(SourceHealth("Morning Brief Notion Row", "error", f"今日 Notion 晨報 row 未確認 | morning row not verified: {morning_brief_job.detail[:160]}"))
    return health


def render_api_health(generated_at: str, today: str, health_items: list[SourceHealth]) -> str:
    healthy = sum(1 for item in health_items if item.status == "healthy")
    fallback = sum(1 for item in health_items if item.status == "fallback")
    error = sum(1 for item in health_items if item.status == "error")
    lines = [
        "# API Health",
        "",
        f"- 更新時間 | updated: {generated_at}",
        f"- 日期 | date: {today}",
        f"- 正常 | healthy: {healthy}",
        f"- 備援 | fallback: {fallback}",
        f"- 錯誤 | error: {error}",
        "",
        "## Today",
        "",
    ]
    for item in health_items:
        badge = {
            "healthy": "🟢 healthy",
            "fallback": "🟡 fallback",
            "error": "🔴 error",
        }[item.status]
        lines.append(f"- **{item.name}**: {badge} — {item.note}")
    lines += [
        "",
        "## Rule",
        "",
        "- `healthy` 代表今天拿到即時資料 | `healthy` means today's live data was fetched",
        "- `fallback` 代表今天有頁面，但內容是最近可用版本 | `fallback` means today's page exists but uses the latest available content",
        "- `error` 代表今天連頁面都沒補出來 | `error` means today's page could not be produced",
        "",
    ]
    return "\n".join(lines).rstrip() + "\n"


def render_status(mode: str, generated_at: str, results: Iterable[JobResult], fallback_markers: list[str], today: str) -> str:
    ok_results = [job for job in results if job.success]
    failed_results = [job for job in results if not job.success]
    degraded_count = len(fallback_markers)
    fully_fresh_count = max(len(ok_results) - degraded_count, 0)

    lines = [
        "# Automation Status",
        "",
        f"- 更新時間 | updated: {generated_at}",
        f"- 模式 | mode: {mode}",
        f"- 日期 | date: {today}",
        "",
        "## 今天先看 | Start Here",
        "",
        "- [[Resources/Operating Feed/Today Hub]]",
        f"- [[Resources/Operating Feed/{today} Operating Feed]]",
        f"- [[Resources/Research Sources/Daily Digests/{today} Research Digest]]",
        "- [[Resources/Operating Feed/Today Manual Tasks]]",
        "",
        "## 自動化目標 | Automation Goals",
        "",
        "- 早上自動整理信箱、日曆、研究雷達 | build the morning context automatically",
        "- 補跑漏掉的步驟與缺日頁面 | repair missed steps and backfill missing days",
        "- 優先保住 Today Hub 單一主畫面 | preserve Today Hub as the single daily screen",
        "",
        "## 本次執行結果 | Run Results",
        "",
        f"- 成功（含備援） | succeeded (including fallback): {len(ok_results)}",
        f"- 完整即時成功 | fully fresh: {fully_fresh_count}",
        f"- 降級備援 | degraded fallback: {degraded_count}",
        f"- 失敗 | failed: {len(failed_results)}",
        "",
        "### 成功項目 | Successful Jobs",
        "",
    ]
    if not ok_results:
        lines.append("- none")
    else:
        for job in ok_results:
            lines.append(f"- `{job.name}`: {job.detail}")

    lines += [
        "",
        "### 失敗項目 | Failed Jobs",
        "",
    ]
    if not failed_results:
        lines.append("- none")
    else:
        for job in failed_results:
            lines.append(f"- `{job.name}`: {job.detail}")

    lines += [
        "",
        "## 備援 / 補洞訊號 | Fallback / Backfill Signals",
        "",
    ]
    if not fallback_markers:
        lines.append("- none")
    else:
        for marker in fallback_markers:
            lines.append(f"- {marker}")

    lines += [
        "",
        "## 規則 | Rule",
        "",
        "- 這頁是狀態頁，不是待辦清單 | this page is status, not a task list",
        "- 如果來源失敗，Today Hub 仍應保持可用 | Today Hub should remain usable even when a source fails",
        "- 真正承諾事項仍寫在 [[Resources/Operating Feed/Today Manual Tasks]] | real commitments still belong in Today Manual Tasks",
        "",
    ]
    return "\n".join(lines).rstrip() + "\n"


def today_pages_are_fresh(vault_path: Path, today: str) -> bool:
    """Return True if today's Operating Feed already exists and was written today.

    Used by --catchup mode to avoid double-running when jobs already fired.
    A backfilled-fallback page (written by ensure_daily_pages) is NOT fresh.
    """
    op_feed = vault_path / "Resources" / "Operating Feed" / f"{today} Operating Feed.md"
    if not op_feed.exists():
        return False
    try:
        mtime_day = datetime.fromtimestamp(op_feed.stat().st_mtime).strftime("%Y-%m-%d")
    except Exception:
        return False
    if mtime_day != today:
        return False
    content = op_feed.read_text(encoding="utf-8", errors="ignore")
    stale_markers = (
        "status: backfilled-fallback",
        "status: fallback",
        "status: api-failed",
        "status: api-disabled",
        "status: auth-required",
        "status: missing-live-data",
    )
    if any(marker in content for marker in stale_markers):
        return False
    api_health = vault_path / "Resources" / "Operating Feed" / "API Health.md"
    automation_status = vault_path / "Resources" / "Operating Feed" / "Automation Status.md"
    if not api_health.exists() or f"- 日期 | date: {today}" not in api_health.read_text(encoding="utf-8", errors="ignore"):
        return False
    if not automation_status.exists():
        return False
    status_text = automation_status.read_text(encoding="utf-8", errors="ignore")
    if f"- 日期 | date: {today}" not in status_text:
        return False
    if "`morning_brief_notion`" not in status_text or "morning_brief_page=" not in status_text:
        return False
    if "- 降級備援 | degraded fallback: 0" not in status_text:
        return False
    if "- 失敗 | failed: 0" not in status_text:
        return False
    if "## 備援 / 補洞訊號 | Fallback / Backfill Signals\n\n- none" not in status_text:
        return False
    return True


def main() -> int:
    args = parse_args()
    logger = build_logger("doraos.cloud_autopilot", LOG_DIR / "dora_cloud_autopilot.log", verbose=args.verbose)
    env = load_env_file(Path(args.env_file))
    vault_path = Path(require_env(env, "OBSIDIAN_VAULT_PATH")).expanduser()
    today = datetime.now().strftime("%Y-%m-%d")

    # Network warmup — critical for boot-time catch-up runs where DNS may not be ready
    network_timeout = float(env.get("NETWORK_WARMUP_TIMEOUT", "120") or "120")
    wait_for_network(timeout=network_timeout, logger=logger)

    # --catchup mode: skip full run if today pages are already fresh (avoids double-run at login)
    if args.catchup and today_pages_are_fresh(vault_path, today):
        logger.info("Catchup mode: pages for %s already fresh — skipping pipeline.", today)
        print("catchup=skipped reason=already_fresh")
        return 0
    if args.catchup:
        logger.info("Catchup mode: pages missing or stale for %s — running full pipeline.", today)

    env_args = ["--env-file", str(Path(args.env_file).expanduser())]
    common_flags: list[str] = []
    if args.verbose:
        common_flags.append("--verbose")
    if args.dry_run:
        common_flags.append("--dry-run")

    jobs: list[tuple[str, Path, list[str]]] = [
        ("ensure_daily_pages", OPERATING_DIR / "ensure_daily_pages.py", [*env_args, *common_flags, "--days", "7"]),
        ("research_digest", OPERATING_DIR / "research_digest_sync.py", [*env_args, *common_flags]),
        ("gmail_digest", OPERATING_DIR / "gmail_digest_sync.py", [*env_args, *common_flags]),
        ("calendar_today", OPERATING_DIR / "calendar_today_sync.py", [*env_args, *common_flags]),
        ("google_tasks", OPERATING_DIR / "google_tasks_sync.py", [*env_args, *common_flags]),
        ("weather_market", OPERATING_DIR / "weather_market_sync.py", [*env_args, *common_flags]),
        ("operating_feed", OPERATING_DIR / "operating_feed_compose.py", [*env_args, *common_flags]),
        ("morning_brief_notion", RECOVERY_DIR / "morning_brief_notion_sync.py", [*env_args, *common_flags]),
        ("daily_brief", ROOT / "scripts" / "daily_brief.py", ["--config", "config/daily_brief.config.example.json", *common_flags]),
    ]
    if args.mode in {"daily", "midday"}:
        jobs.insert(1, ("notion_backup", RECOVERY_DIR / "notion_backup_sync.py", [*env_args, *common_flags, "--config", str(ROOT / "config" / "notion_backup.config.example.json")]))
    if args.mode == "weekly":
        jobs.extend(
            [
                ("weekly_recovery_review", RECOVERY_DIR / "weekly_review_generator.py", [*env_args, *common_flags]),
                ("weekly_review", ROOT / "scripts" / "weekly_review.py", ["--config", "config/weekly_review.config.example.json", *common_flags]),
            ]
        )
    if args.mode == "monthly":
        jobs.extend(
            [
                ("monthly_recovery_review", RECOVERY_DIR / "monthly_review_generator.py", [*env_args, *common_flags]),
                ("monthly_review", ROOT / "scripts" / "monthly_review.py", ["--config", "config/weekly_review.config.example.json", *common_flags]),
            ]
        )

    results = [run_job(name, path, job_args, logger) for name, path, job_args in jobs]
    fallback_markers = detect_fallback_markers(vault_path, today)
    source_health = collect_source_health(vault_path, today, results)
    generated_at = datetime.now().strftime("%Y-%m-%d %H:%M")
    status_text = render_status(args.mode, generated_at, results, fallback_markers, today)
    api_health_text = render_api_health(generated_at, today, source_health)

    if args.dry_run:
        print(status_text)
        return 0

    ensure_dir(AUTOMATION_STATUS.parent)
    AUTOMATION_STATUS.write_text(status_text, encoding="utf-8")
    API_HEALTH.write_text(api_health_text, encoding="utf-8")
    operating_feed_script = OPERATING_DIR / "operating_feed_compose.py"
    rerender_args = [pick_python(operating_feed_script), str(operating_feed_script), "--env-file", str(Path(args.env_file).expanduser())]
    if args.verbose:
        rerender_args.append("--verbose")
    subprocess.run(rerender_args, cwd=str(ROOT), capture_output=True, text=True, check=False)
    logger.info("Updated automation status at %s", AUTOMATION_STATUS)

    if (
        args.mode == "daily"
        and env.get("CLOUD_AUTOPILOT_SEND_LINE_DAILY", "false").strip().lower() in {"1", "true", "yes", "on"}
    ):
        line_result = run_job(
            "line_daily_brief",
            OPERATING_DIR / "line_daily_brief.py",
            ["--env-file", str(Path(args.env_file).expanduser())],
            logger,
        )
        if not line_result.success:
            logger.warning("Cloud LINE daily brief failed: %s", line_result.detail)

    print(AUTOMATION_STATUS)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
