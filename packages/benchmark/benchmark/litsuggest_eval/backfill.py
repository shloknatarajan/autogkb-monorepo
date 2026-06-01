"""Backfill triage sessions for all un-processed LitSuggest weekly digest jobs."""

from __future__ import annotations

import argparse
import asyncio
import os
import re
import subprocess
from datetime import datetime

from src.database import create_triage_session, find_triage_session_by_week
from src.jobs import run_triage_job
from src.litsuggest_client import list_weekly_jobs

_DEFAULT_PROJECTS = [
    ("68f6813df2b49b9358c64421", "General PGx"),
    ("68f682f7da47ae09aeaa9182", "Pediatric PGx"),
]


def _parse_week_date(job_name: str) -> str | None:
    """Extract ISO start-date from 'Automatic Weekly Digest (Apr 19 2026 to Apr 25 2026)'."""
    m = re.search(r"\((\w+ \d+ \d+) to", job_name)
    if not m:
        return None
    try:
        return datetime.strptime(m.group(1), "%b %d %Y").date().isoformat()
    except ValueError:
        return None


def _backfill_project(project_id: str, project_name: str) -> int:
    jobs = list_weekly_jobs(project_id)
    print(f"\n[{project_name}] {len(jobs)} weekly jobs found.")
    new_count = 0
    for job in reversed(jobs):  # oldest-first
        week_date = _parse_week_date(job["name"])
        if not week_date:
            print(f"  SKIP (no date): {job['name']!r}")
            continue
        if find_triage_session_by_week(project_id, week_date):
            print(f"  SKIP (exists):  {week_date}")
            continue
        print(f"  TRIAGE: {week_date}  job={job['id'][:10]}…", end=" ", flush=True)
        session_id = create_triage_session(project_id, project_name, week_date)
        asyncio.run(run_triage_job(session_id, project_id, job["id"]))
        print("done.")
        new_count += 1
    return new_count


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Backfill LitSuggest triage sessions then run eval"
    )
    parser.add_argument(
        "--database-url",
        default=os.getenv("DATABASE_URL"),
        help="PostgreSQL DSN (default: DATABASE_URL env var)",
    )
    parser.add_argument(
        "--projects",
        nargs="+",
        metavar="ID:NAME",
        default=[f"{pid}:{name}" for pid, name in _DEFAULT_PROJECTS],
        help="Space-separated project_id:project_name pairs",
    )
    parser.add_argument("--skip-eval", action="store_true", help="Don't run eval after backfill")
    args = parser.parse_args()

    if not args.database_url:
        raise SystemExit("Error: DATABASE_URL must be set (--database-url or env var)")

    os.environ["DATABASE_URL"] = args.database_url

    total_new = 0
    for spec in args.projects:
        project_id, _, project_name = spec.partition(":")
        total_new += _backfill_project(project_id, project_name)

    print(f"\nBackfill complete. {total_new} new session(s) created.")

    if not args.skip_eval:
        print("\n--- Running eval ---")
        subprocess.run(
            ["uv", "run", "litsuggest-eval"],
            env={**os.environ, "DATABASE_URL": args.database_url},
            check=True,
        )


if __name__ == "__main__":
    main()
