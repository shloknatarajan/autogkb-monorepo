"""Rough field-level evaluation against existing benchmark annotations."""

import argparse
import json
import re
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_BENCHMARK_DIR = (
    REPO_ROOT / "data" / "benchmark" / "benchmark_annotations" / "by_pmcid"
)


def _norm(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip().casefold())


def _ground_truth_direction(annotation: dict) -> str | None:
    direction = _norm(annotation.get("Direction of effect"))
    if direction in {"increased", "decreased"}:
        return direction
    association = _norm(annotation.get("Is/Is Not associated"))
    sentence = _norm(annotation.get("Sentence"))
    significance = _norm(annotation.get("Significance"))
    if "not associated" in association or "not associated" in sentence:
        return "no_association"
    if significance == "no":
        return "no_association"
    return None


def load_ground_truth(path: Path) -> list[dict[str, str | None]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    records = []
    for key in ("var_drug_ann", "var_pheno_ann", "var_fa_ann"):
        for annotation in data.get(key) or []:
            variant = _norm(annotation.get("Variant/Haplotypes"))
            if not variant:
                continue
            records.append(
                {
                    "variant": variant,
                    "drug": _norm(annotation.get("Drug(s)")) or None,
                    "direction": _ground_truth_direction(annotation),
                }
            )
    return records


def load_generated(path: Path) -> list[dict]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data.get("associations"), list):
        return data["associations"]
    annotation_data = data.get("annotation_data") or {}
    if isinstance(annotation_data.get("structured_associations"), list):
        return annotation_data["structured_associations"]
    result = data.get("result") or {}
    return result.get("associations") or []


def association_matches(expected: dict, generated: dict) -> bool:
    generated_variants = {
        _norm(value)
        for value in [
            generated.get("variant"),
            *(generated.get("variant_aliases") or []),
        ]
        if value
    }
    if expected["variant"] not in generated_variants:
        return False
    expected_drug = expected.get("drug")
    generated_drug = _norm(generated.get("drug"))
    if expected_drug and expected_drug not in generated_drug and generated_drug not in expected_drug:
        return False
    expected_direction = expected.get("direction")
    if expected_direction and expected_direction != _norm(generated.get("direction")):
        return False
    return True


def evaluate_article(expected: list[dict], generated: list[dict]) -> dict:
    unmatched = set(range(len(generated)))
    matches = 0
    for record in expected:
        match = next(
            (
                index
                for index in unmatched
                if association_matches(record, generated[index])
            ),
            None,
        )
        if match is not None:
            unmatched.remove(match)
            matches += 1

    precision = matches / len(generated) if generated else 0.0
    recall = matches / len(expected) if expected else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    expected_variants = {record["variant"] for record in expected}
    generated_variants = {
        _norm(value)
        for record in generated
        for value in [record.get("variant"), *(record.get("variant_aliases") or [])]
        if value
    }
    variant_recall = (
        len(expected_variants & generated_variants) / len(expected_variants)
        if expected_variants
        else 0.0
    )
    return {
        "expected_associations": len(expected),
        "generated_associations": len(generated),
        "matched_associations": matches,
        "precision": round(precision, 4),
        "recall": round(recall, 4),
        "f1": round(f1, 4),
        "variant_recall": round(variant_recall, 4),
    }


def evaluate_directory(generated_dir: Path, benchmark_dir: Path) -> dict:
    articles = {}
    totals = {"expected": 0, "generated": 0, "matched": 0}
    for generated_path in sorted(generated_dir.glob("*.json")):
        benchmark_path = benchmark_dir / generated_path.name
        if not benchmark_path.exists():
            continue
        score = evaluate_article(
            load_ground_truth(benchmark_path), load_generated(generated_path)
        )
        articles[generated_path.stem] = score
        totals["expected"] += score["expected_associations"]
        totals["generated"] += score["generated_associations"]
        totals["matched"] += score["matched_associations"]

    precision = totals["matched"] / totals["generated"] if totals["generated"] else 0.0
    recall = totals["matched"] / totals["expected"] if totals["expected"] else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {
        "aggregate": {
            **totals,
            "precision": round(precision, 4),
            "recall": round(recall, 4),
            "f1": round(f1, 4),
        },
        "articles": articles,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("generated_dir", type=Path)
    parser.add_argument("--benchmark-dir", type=Path, default=DEFAULT_BENCHMARK_DIR)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    report = evaluate_directory(args.generated_dir, args.benchmark_dir)
    content = json.dumps(report, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(content + "\n", encoding="utf-8")
    else:
        print(content)


if __name__ == "__main__":
    main()
