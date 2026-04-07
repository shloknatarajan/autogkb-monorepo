"""
Check for benchmark annotation files where there are grouped variants

This is used for data exploration, not for scoring at the moment.
"""

import json
import re
import sys
from pathlib import Path
from dataclasses import dataclass, field
from collections import defaultdict
from datetime import datetime


@dataclass
class GroupedAnnotation:
    """Stores an annotation that has grouped terms (commas in key fields)"""

    pmcid: str
    pmid: str
    article_title: str
    annotation_type: str  # 'var_drug_ann', 'var_pheno_ann', 'var_fa_ann'
    annotation: dict
    grouped_fields: list = field(default_factory=list)  # Which fields have commas

    def __hash__(self):
        # Use Variant Annotation ID for deduplication
        return hash((self.pmcid, self.annotation.get("Variant Annotation ID")))

    def __eq__(self, other):
        return self.pmcid == other.pmcid and self.annotation.get(
            "Variant Annotation ID"
        ) == other.annotation.get("Variant Annotation ID")


def _has_comma(v) -> bool:
    return "," in (v or "")


def _extract_outcome_from_sentence(sentence: str) -> str:
    """Extract the outcome/effect from the sentence.

    Returns a string like "decreased response" or "not increased risk"
    """
    if not sentence:
        return "effect"

    sentence_lower = sentence.lower()

    # Check for "not associated" vs "associated"
    is_not = (
        "not associated" in sentence_lower or "are not associated" in sentence_lower
    )

    # Patterns to extract: "associated with {direction} {outcome}"
    # direction: increased, decreased
    # outcome: response, risk, likelihood, severity, clearance, concentrations, dose, etc.
    patterns = [
        r"associated with (increased|decreased) (response|risk|likelihood|severity|clearance|concentrations?|dose|metabolism|activity)",
        r"associated with (increased|decreased) (\w+)",
    ]

    direction = None
    outcome = None

    for pattern in patterns:
        match = re.search(pattern, sentence_lower)
        if match:
            direction = match.group(1)
            outcome = match.group(2)
            break

    if direction and outcome:
        if is_not:
            return f"not {direction} {outcome}"
        return f"{direction} {outcome}"

    # Fallback: look for simple patterns
    if "decreased" in sentence_lower:
        if is_not:
            return "not decreased effect"
        return "decreased effect"
    if "increased" in sentence_lower:
        if is_not:
            return "not increased effect"
        return "increased effect"

    if is_not:
        return "no association"
    return "associated"


def _generate_summary_line(ann: "GroupedAnnotation") -> str:
    """Generate a summary line for an annotation.

    Old Format: (variant) + (drug OR drug) -> outcome
    New Format: (variant) + (drug, drug...) + (phenotype, ...) -> (outcome)
    only include the field if it exists in the annotation

    """
    variant = ann.annotation.get("Variant/Haplotypes", "Unknown variant")
    drugs_str = ann.annotation.get("Drug(s)", "")
    phenotype_str = ann.annotation.get("Phenotype", "")
    sentence = ann.annotation.get("Sentence", "")

    type_prefix_map = {
        "var_drug_ann": "drug",
        "var_pheno_ann": "pheno",
        "var_fa_ann": "fa",
    }
    type_prefix = type_prefix_map.get(ann.annotation_type, ann.annotation_type)

    components = [f"({variant})"]

    if drugs_str:
        drugs = [d.strip() for d in drugs_str.split(",") if d.strip()]
        if drugs:
            components.append(f"({', '.join(drugs)})")

    if phenotype_str:
        phenotypes = [p.strip() for p in phenotype_str.split(",") if p.strip()]
        if phenotypes:
            components.append(f"({', '.join(phenotypes)})")

    outcome = _extract_outcome_from_sentence(sentence)
    return f"{type_prefix}: {' + '.join(components)} -> {outcome}"


def _check_annotation_for_groups(annotation: dict, fields_to_check: list) -> list:
    """Check which fields in an annotation have commas"""
    grouped_fields = []
    for field_name in fields_to_check:
        if _has_comma(annotation.get(field_name)):
            grouped_fields.append(field_name)
    return grouped_fields


def find_grouped_annotations(file_path: str) -> list[GroupedAnnotation]:
    """Find all annotations with grouped terms in a file"""
    with open(file_path, "r") as f:
        data = json.load(f)

    pmcid = data.get("pmcid", "Unknown")
    pmid = data.get("pmid", "Unknown")
    title = data.get("title", "Unknown")

    grouped_annotations = []
    seen_ids = set()

    # Fields to check for each annotation type
    common_fields = ["Drug(s)", "Variant/Haplotypes"]

    # Check var_drug_ann
    for item in data.get("var_drug_ann", []) or []:
        grouped_fields = _check_annotation_for_groups(item, common_fields)
        if grouped_fields:
            ann_id = item.get("Variant Annotation ID")
            if ann_id not in seen_ids:
                seen_ids.add(ann_id)
                grouped_annotations.append(
                    GroupedAnnotation(
                        pmcid=pmcid,
                        pmid=pmid,
                        article_title=title,
                        annotation_type="var_drug_ann",
                        annotation=item,
                        grouped_fields=grouped_fields,
                    )
                )

    # Check var_pheno_ann - also check 'Phenotype' field
    pheno_fields = common_fields + ["Phenotype"]
    for item in data.get("var_pheno_ann", []) or []:
        grouped_fields = _check_annotation_for_groups(item, pheno_fields)
        if grouped_fields:
            ann_id = item.get("Variant Annotation ID")
            if ann_id not in seen_ids:
                seen_ids.add(ann_id)
                grouped_annotations.append(
                    GroupedAnnotation(
                        pmcid=pmcid,
                        pmid=pmid,
                        article_title=title,
                        annotation_type="var_pheno_ann",
                        annotation=item,
                        grouped_fields=grouped_fields,
                    )
                )

    # Check var_fa_ann
    for item in data.get("var_fa_ann", []) or []:
        grouped_fields = _check_annotation_for_groups(item, common_fields)
        if grouped_fields:
            ann_id = item.get("Variant Annotation ID")
            if ann_id not in seen_ids:
                seen_ids.add(ann_id)
                grouped_annotations.append(
                    GroupedAnnotation(
                        pmcid=pmcid,
                        pmid=pmid,
                        article_title=title,
                        annotation_type="var_fa_ann",
                        annotation=item,
                        grouped_fields=grouped_fields,
                    )
                )

    return grouped_annotations


def print_grouped_annotations(all_annotations: list[GroupedAnnotation]):
    """Print annotations grouped by PMCID in a nice format"""
    # Group by PMCID
    by_pmcid = defaultdict(list)
    for ann in all_annotations:
        by_pmcid[ann.pmcid].append(ann)

    print(f"\n{'=' * 80}")
    print(f"FOUND {len(all_annotations)} ANNOTATIONS WITH GROUPED TERMS")
    print(f"ACROSS {len(by_pmcid)} ARTICLES")
    print(f"{'=' * 80}\n")

    for pmcid in sorted(by_pmcid.keys()):
        annotations = by_pmcid[pmcid]
        first = annotations[0]

        print(f"\n{'─' * 80}")
        print(f"PMCID: {pmcid}")
        print(f"PMID:  {first.pmid}")
        print(f"Title: {first.article_title}")
        print(f"{'─' * 80}")

        # Generate summary section for this article
        print("Summary:")
        for ann in annotations:
            summary_line = _generate_summary_line(ann)
            print(summary_line)
        print("\nBreakdown:")

        for i, ann in enumerate(annotations, 1):
            print(f"\n[{i}] Type: {ann.annotation_type}")
            print(f"Grouped fields: {', '.join(ann.grouped_fields)}")
            print(f"Annotation ID: {ann.annotation.get('Variant Annotation ID')}")
            print("---")

            # Print key fields from the annotation
            key_fields = [
                "Variant/Haplotypes",
                "Gene",
                "Drug(s)",
                "Phenotype",
                "Sentence",
                "Significance",
                "Phenotype Category",
            ]
            for field in key_fields:
                if field in ann.annotation and ann.annotation[field]:
                    value = ann.annotation[field]
                    print(f"{field}: {value}")


def main():
    benchmark_dir = Path("data/benchmark_annotations")

    # Generate timestamped output filename
    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    output_file = Path("data/groupings") / f"group_summary_{timestamp}.txt"

    all_grouped_annotations = []

    for file_path in benchmark_dir.glob("*.json"):
        annotations = find_grouped_annotations(str(file_path))
        all_grouped_annotations.extend(annotations)

    # Redirect stdout to both file and console
    original_stdout = sys.stdout
    with open(output_file, "w") as f:
        # Write to file
        sys.stdout = f
        print_grouped_annotations(all_grouped_annotations)

        # Summary statistics
        print(f"\n\n{'=' * 80}")
        print("SUMMARY BY GROUPED FIELD")
        print(f"{'=' * 80}")

        field_counts = defaultdict(int)
        for ann in all_grouped_annotations:
            for field in ann.grouped_fields:
                field_counts[field] += 1

        for field, count in sorted(field_counts.items(), key=lambda x: -x[1]):
            print(f"  {field}: {count} annotations")

    # Restore stdout and print confirmation
    sys.stdout = original_stdout
    print(f"Output written to: {output_file}")


if __name__ == "__main__":
    main()
