"""One-off CLI for adding paper-type and PGx-information labels to old sessions."""

import argparse
import asyncio
import os

from src.triage_label_backfill import backfill_triage_labels


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--model",
        default=os.environ.get("TRIAGE_MODEL", "gpt-6-astra"),
        help="LLM used by the existing label classifiers (default: TRIAGE_MODEL)",
    )
    parser.add_argument(
        "--concurrency",
        type=int,
        default=4,
        help="Maximum papers classified concurrently (default: 4)",
    )
    parser.add_argument(
        "--session-batch-size",
        type=int,
        default=5,
        help="Sessions scheduled together before checkpointing (default: 5)",
    )
    args = parser.parse_args()
    stats = asyncio.run(
        backfill_triage_labels(
            model=args.model,
            concurrency=args.concurrency,
            session_batch_size=args.session_batch_size,
        )
    )
    print("Triage label backfill complete:")
    for key, value in stats.items():
        print(f"  {key}: {value}")


if __name__ == "__main__":
    main()
