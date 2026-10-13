"""Re-score articles from existing DB sessions using a prompt and model.

Reads articles (pmid + title + abstract) from triage_sessions read-only,
re-scores with the specified prompt variant, and writes results to a JSONL
file. Does NOT write to the database.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

import psycopg2
import psycopg2.extras

from shared.triage_scoring import _VA_TRIAGE_SYSTEM_V2, score_for_va_with_system

from .fetcher import fetch_abstracts
from .loader import load_ground_truth

OUTPUT_PATH = (
    Path(__file__).resolve().parents[4] / "data" / "litsuggest" / "v2_scores.jsonl"
)

PROMPT_VARIANTS = {
    "v2": _VA_TRIAGE_SYSTEM_V2,
}


def _load_articles_from_db(database_url: str, project_id: str) -> list[dict]:
    """Return deduplicated articles (most recent session per PMID) from DB."""
    conn = psycopg2.connect(database_url)
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT articles FROM triage_sessions"
                " WHERE status = 'completed' AND project_id = %s"
                " ORDER BY created_at ASC",
                (project_id,),
            )
            rows = cur.fetchall()
    finally:
        conn.close()

    seen: dict[str, dict] = {}
    for row in rows:
        for art in row["articles"] or []:
            pmid = art.get("pmid")
            if pmid:
                seen[pmid] = dict(art)
    return list(seen.values())


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Re-score DB articles with a new prompt variant (no DB writes)"
    )
    parser.add_argument("--database-url", default=os.getenv("DATABASE_URL"))
    parser.add_argument(
        "--ground-truth",
        action="store_true",
        help="Score the labeled TSV directly, fetching/caching abstracts from PubMed",
    )
    parser.add_argument(
        "--ncbi-email",
        default=os.getenv("NCBI_EMAIL", ""),
        help="Email sent to NCBI E-utilities (default: NCBI_EMAIL)",
    )
    parser.add_argument("--project-id", default="68f6813df2b49b9358c64421",
                        help="LitSuggest project ID to read articles from")
    parser.add_argument("--model", default=os.getenv("PIPELINE_MODEL", "gpt-5.2"))
    parser.add_argument(
        "--prompt-variant",
        default="v2",
        choices=list(PROMPT_VARIANTS),
        help="Built-in prompt to use (default: v2); ignored when --prompt-file is set",
    )
    parser.add_argument(
        "--prompt-file",
        type=Path,
        help="Path to a custom system-prompt text file",
    )
    parser.add_argument("--output", default=str(OUTPUT_PATH),
                        help="Path for output JSONL file")
    args = parser.parse_args()

    if not args.ground_truth and not args.database_url:
        raise SystemExit("Error: DATABASE_URL must be set (--database-url or env var)")

    if args.ground_truth:
        records = load_ground_truth()
        abstracts = fetch_abstracts([record.pmid for record in records], args.ncbi_email)
        articles = [abstracts[record.pmid] for record in records]
        print(f"Loaded {len(articles)} labeled articles from PubMed/cache.")
    else:
        articles = _load_articles_from_db(args.database_url, args.project_id)
        print(f"Loaded {len(articles)} articles from DB.")

    if args.prompt_file:
        system_prompt = args.prompt_file.read_text(encoding="utf-8").strip()
        if not system_prompt:
            raise SystemExit("Error: --prompt-file must not be empty")
        print(f"Using custom prompt from {args.prompt_file} with model {args.model}.")
    else:
        system_prompt = PROMPT_VARIANTS[args.prompt_variant]
        print(f"Using prompt variant {args.prompt_variant!r} with model {args.model}.")
    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    with open(out_path, "w", encoding="utf-8") as fh:
        for i, art in enumerate(articles, 1):
            pmid = art["pmid"]
            title = art.get("title") or ""
            abstract = art.get("abstract") or ""
            result = score_for_va_with_system(pmid, title, abstract, args.model, system_prompt)
            record = {
                "pmid": pmid,
                "triage_label": result["label"],
                "score": result["score"],
                "reasoning": result["reasoning"],
            }
            fh.write(json.dumps(record) + "\n")
            if i % 50 == 0:
                print(f"  {i}/{len(articles)} scored…")

    print(f"Done. {len(articles)} articles scored. Results saved to {out_path}")


if __name__ == "__main__":
    main()
