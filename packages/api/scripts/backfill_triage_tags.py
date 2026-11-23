"""Inventory or backfill drug, gene, and phenotype tags on triage sessions."""

import argparse
import asyncio
import os

from src.backfill.triage_tags import backfill_triage_tags


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Persist missing tags. Without this flag, only report the inventory.",
    )
    parser.add_argument(
        "--model",
        default=os.environ.get("TRIAGE_MODEL", "gpt-6-astra"),
        help="LLM used by the existing tag extractors (default: TRIAGE_MODEL)",
    )
    parser.add_argument(
        "--concurrency",
        type=int,
        default=4,
        help="Maximum tag extractions running concurrently (default: 4)",
    )
    parser.add_argument(
        "--session-batch-size",
        type=int,
        default=5,
        help="Sessions processed before each checkpoint (default: 5)",
    )
    args = parser.parse_args()
    stats = asyncio.run(
        backfill_triage_tags(
            model=args.model,
            apply=args.apply,
            concurrency=args.concurrency,
            session_batch_size=args.session_batch_size,
        )
    )
    print("Triage tag backfill inventory:" if not args.apply else "Backfill complete:")
    for key, value in stats.items():
        print(f"  {key}: {value}")


if __name__ == "__main__":
    main()
