"""Inventory or backfill missing PubMed publication dates on triage sessions."""

import argparse
import os

from src.backfill.triage_publication_dates import backfill_triage_publication_dates


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Persist dates. Without this flag, only report the inventory.",
    )
    parser.add_argument(
        "--email",
        default=os.environ.get("NCBI_EMAIL"),
        help="Email sent to NCBI (default: NCBI_EMAIL)",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=200,
        help="PMIDs fetched in each PubMed request (default: 200)",
    )
    args = parser.parse_args()
    stats = backfill_triage_publication_dates(
        ncbi_email=args.email,
        apply=args.apply,
        batch_size=args.batch_size,
    )
    print(
        "Publication-date backfill inventory:"
        if not args.apply
        else "Publication-date backfill complete:"
    )
    for key, value in stats.items():
        print(f"  {key}: {value}")


if __name__ == "__main__":
    main()
