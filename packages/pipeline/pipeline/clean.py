"""Clean incomplete entries from generations.jsonl.

Removes:
  1. Lines that are not valid JSON
  2. Lines that fail GenerationRecord validation (missing required fields)
  3. Records with empty annotations (incomplete pipeline runs)

Usage:
  clean-generations              # dry-run by default
  clean-generations --apply      # actually rewrite the file
"""

from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path

from loguru import logger

from generation.models import GenerationRecord
from generation.pipeline import GENERATIONS_JSONL


def clean_generations(path: Path = GENERATIONS_JSONL, apply: bool = False) -> None:
    if not path.exists():
        logger.error(f"File not found: {path}")
        return

    lines = path.read_text(encoding="utf-8").splitlines()
    logger.info(f"Read {len(lines)} line(s) from {path}")

    kept: list[GenerationRecord] = []
    removed_invalid = 0
    removed_incomplete = 0

    for i, line in enumerate(lines):
        line = line.strip()
        if not line:
            removed_invalid += 1
            continue

        # Check valid JSON
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            logger.warning(f"  Line {i}: invalid JSON — removing")
            removed_invalid += 1
            continue

        # Check valid GenerationRecord
        try:
            record = GenerationRecord.model_validate(data)
        except Exception as e:
            logger.warning(f"  Line {i}: validation failed ({e}) — removing")
            removed_invalid += 1
            continue

        # Check completeness: must have annotations
        if not record.annotations:
            logger.warning(
                f"  Line {i}: empty annotations for {record.pmcid} — removing"
            )
            removed_incomplete += 1
            continue

        kept.append(record)

    # Report
    logger.info(f"  Invalid/empty lines removed: {removed_invalid}")
    logger.info(f"  Incomplete records removed: {removed_incomplete}")
    logger.info(f"  Records kept: {len(kept)} (from {len(lines)} lines)")

    if not apply:
        logger.info("Dry run — no changes written. Pass --apply to rewrite the file.")
        return

    # Backup and rewrite
    backup = path.with_suffix(".jsonl.bak")
    shutil.copy2(path, backup)
    logger.info(f"  Backup saved to {backup}")

    with open(path, "w", encoding="utf-8") as f:
        for record in kept:
            f.write(record.model_dump_json() + "\n")

    logger.success(f"Rewrote {path} with {len(kept)} record(s)")


def main():
    parser = argparse.ArgumentParser(
        description="Clean incomplete entries from generations.jsonl"
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Actually rewrite the file (default is dry-run)",
    )
    parser.add_argument(
        "--path",
        type=Path,
        default=GENERATIONS_JSONL,
        help=f"Path to generations.jsonl (default: {GENERATIONS_JSONL})",
    )
    args = parser.parse_args()
    clean_generations(path=args.path, apply=args.apply)


if __name__ == "__main__":
    main()
