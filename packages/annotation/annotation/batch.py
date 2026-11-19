"""Resumable batch runner for the evidence-grounded annotator."""

import argparse
import json
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from annotation.config import load_environment
from annotation.extractor import annotate_text
from annotation.models import AnnotationResult


def _is_complete(path: Path) -> bool:
    if not path.is_file():
        return False
    try:
        AnnotationResult.model_validate_json(path.read_text(encoding="utf-8"))
    except Exception:
        return False
    return True


def annotate_file(article_path: Path, output_path: Path, model: str) -> dict:
    started = time.monotonic()
    text = article_path.read_text(encoding="utf-8")
    result = annotate_text(text, model=model)
    output_path.write_text(result.model_dump_json(indent=2) + "\n", encoding="utf-8")
    return {
        "pmcid": article_path.stem,
        "status": "completed",
        "elapsed_seconds": round(time.monotonic() - started, 2),
        "associations": len(result.associations),
        "variant_mentions": len(result.variant_mentions),
    }


def run_batch(
    article_dir: Path,
    benchmark_dir: Path,
    output_dir: Path,
    *,
    model: str,
    workers: int,
    force: bool = False,
) -> dict:
    load_environment()
    output_dir.mkdir(parents=True, exist_ok=True)
    article_paths = []
    for benchmark_path in sorted(benchmark_dir.glob("PMC*.json")):
        article_path = article_dir / f"{benchmark_path.stem}.md"
        output_path = output_dir / f"{benchmark_path.stem}.json"
        if article_path.is_file() and (force or not _is_complete(output_path)):
            article_paths.append(article_path)

    records = []
    started = time.monotonic()
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {
            executor.submit(
                annotate_file,
                article_path,
                output_dir / f"{article_path.stem}.json",
                model,
            ): article_path
            for article_path in article_paths
        }
        for future in as_completed(futures):
            article_path = futures[future]
            try:
                record = future.result()
                print(
                    f"completed {record['pmcid']}: "
                    f"{record['associations']} association(s)"
                )
            except Exception as error:
                record = {
                    "pmcid": article_path.stem,
                    "status": "failed",
                    "error": str(error),
                }
                print(f"failed {article_path.stem}: {error}")
            records.append(record)

    report = {
        "model": model,
        "workers": workers,
        "elapsed_seconds": round(time.monotonic() - started, 2),
        "submitted": len(article_paths),
        "completed": sum(record["status"] == "completed" for record in records),
        "failed": sum(record["status"] == "failed" for record in records),
        "records": sorted(records, key=lambda record: record["pmcid"]),
    }
    (output_dir / "batch_report.json").write_text(
        json.dumps(report, indent=2) + "\n", encoding="utf-8"
    )
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("article_dir", type=Path)
    parser.add_argument("benchmark_dir", type=Path)
    parser.add_argument("output_dir", type=Path)
    parser.add_argument("--model", default="gpt-6.2-sol")
    parser.add_argument("--workers", type=int, default=3)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    if args.workers < 1:
        parser.error("--workers must be at least 1")

    report = run_batch(
        args.article_dir,
        args.benchmark_dir,
        args.output_dir,
        model=args.model,
        workers=args.workers,
        force=args.force,
    )
    print(json.dumps({key: value for key, value in report.items() if key != "records"}, indent=2))


if __name__ == "__main__":
    main()
