"""Rough evaluation against existing benchmark annotations.

Reference and generated records are compared on canonical keys: variants and drugs
on both sides go through the same lookup, so ``Coumadin`` matches ``warfarin`` and
``VKORC1 -1639G>A`` matches ``rs9923231``. A reference annotation often lists several
alleles or drugs; a generated record matches when it shares at least one of each.
"""

import argparse
import json
import re
from pathlib import Path
from typing import Any

from annotation.methods.grounded_v2.entities import EntityResolver, drug_key, variant_key


REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_BENCHMARK_DIR = (
    REPO_ROOT / "data" / "benchmark" / "benchmark_annotations" / "by_pmcid"
)


def _norm(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip().casefold())


def _ground_truth_direction(annotation: dict) -> str | None:
    # "Is not associated with increased risk" carries a direction word, but the
    # finding is a null result.
    association = _norm(annotation.get("Is/Is Not associated"))
    if "not associated" in association:
        return "no_association"
    direction = _norm(annotation.get("Direction of effect"))
    if direction in {"increased", "decreased"}:
        return direction
    sentence = _norm(annotation.get("Sentence"))
    significance = _norm(annotation.get("Significance"))
    if "not associated" in sentence:
        return "no_association"
    if significance == "no":
        return "no_association"
    return None


def _split(value: Any) -> list[str]:
    return [part.strip() for part in str(value or "").split(",") if part.strip()]


def _variant_keys(terms: list[str], resolver: EntityResolver) -> set[str]:
    return {variant_key(resolver.variant(term)) for term in terms if term}


def _drug_keys(terms: list[str], resolver: EntityResolver) -> set[str]:
    return {drug_key(resolver.drug(term)) for term in terms if term}


def load_ground_truth(path: Path, resolver: EntityResolver) -> list[dict]:
    data = json.loads(path.read_text(encoding="utf-8"))
    records = []
    for key in ("var_drug_ann", "var_pheno_ann", "var_fa_ann"):
        for annotation in data.get(key) or []:
            variants = _variant_keys(
                _split(annotation.get("Variant/Haplotypes")), resolver
            )
            if not variants:
                continue
            records.append(
                {
                    "variants": variants,
                    "drugs": _drug_keys(_split(annotation.get("Drug(s)")), resolver),
                    "direction": _ground_truth_direction(annotation),
                }
            )
    return records


def _raw_associations(path: Path) -> list[dict]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data.get("associations"), list):
        return data["associations"]
    annotation_data = data.get("annotation_data") or {}
    if isinstance(annotation_data.get("structured_associations"), list):
        return annotation_data["structured_associations"]
    # A job response saved from the API nests the result one level down.
    result = data.get("result") or annotation_data.get("result") or {}
    if isinstance(result.get("structured_associations"), list):
        return result["structured_associations"]
    return result.get("associations") or []


def _entity_terms(entities: list[dict]) -> list[str]:
    return [
        term
        for entity in entities
        for term in [entity.get("name"), *(entity.get("mentions") or [])]
        if term
    ]


def load_generated(path: Path, resolver: EntityResolver) -> list[dict]:
    """Read current output and the earlier single-variant, single-drug layout."""
    records = []
    for association in _raw_associations(path):
        if "variants" in association:
            variant_terms = _entity_terms(association["variants"])
            drug_terms = _entity_terms(association.get("drugs") or [])
            ids = [e["id"] for e in association["variants"] if e.get("id")]
            drug_ids = [e["id"] for e in association.get("drugs") or [] if e.get("id")]
        else:
            variant_terms = [
                association.get("variant"),
                *(association.get("variant_aliases") or []),
            ]
            drug_terms = _split(association.get("drug"))
            ids = [association["variant_id"]] if association.get("variant_id") else []
            drug_ids = []
        records.append(
            {
                "variants": _variant_keys(variant_terms, resolver) | set(ids),
                "drugs": _drug_keys(drug_terms, resolver) | set(drug_ids),
                "direction": _norm(association.get("direction")),
            }
        )
    return records


def association_matches(expected: dict, generated: dict) -> bool:
    if not expected["variants"] & generated["variants"]:
        return False
    if expected["drugs"] and not expected["drugs"] & generated["drugs"]:
        return False
    direction = expected.get("direction")
    return not direction or direction == generated["direction"]


def evaluate_article(expected: list[dict], generated: list[dict]) -> dict:
    unmatched = set(range(len(generated)))
    matches = 0
    for record in expected:
        match = next(
            (
                index
                for index in sorted(unmatched)
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
    generated_variants = set().union(*(record["variants"] for record in generated))
    expected_variants = {frozenset(record["variants"]) for record in expected}
    variant_recall = (
        sum(bool(group & generated_variants) for group in expected_variants)
        / len(expected_variants)
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
    resolver = EntityResolver()
    articles = {}
    totals = {"expected": 0, "generated": 0, "matched": 0}
    for generated_path in sorted(generated_dir.glob("*.json")):
        benchmark_path = benchmark_dir / generated_path.name
        if not benchmark_path.exists():
            continue
        score = evaluate_article(
            load_ground_truth(benchmark_path, resolver),
            load_generated(generated_path, resolver),
        )
        articles[generated_path.stem] = score
        totals["expected"] += score["expected_associations"]
        totals["generated"] += score["generated_associations"]
        totals["matched"] += score["matched_associations"]

    precision = totals["matched"] / totals["generated"] if totals["generated"] else 0.0
    recall = totals["matched"] / totals["expected"] if totals["expected"] else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    count = len(articles) or 1
    return {
        "aggregate": {
            **totals,
            "precision": round(precision, 4),
            "recall": round(recall, 4),
            "f1": round(f1, 4),
            **{
                f"macro_{metric}": round(
                    sum(score[metric] for score in articles.values()) / count, 4
                )
                for metric in ("precision", "recall", "f1", "variant_recall")
            },
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
