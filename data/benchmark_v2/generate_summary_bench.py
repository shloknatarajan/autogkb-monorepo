"""
Generate summary_bench.jsonl from benchmark annotations and sentence_bench.jsonl.

Extracts structured facts per article that a good summary should cover:
- All variant-drug/phenotype associations with structured fields
- Study parameters (p-values, sample sizes, study types)
- Linked to ground truth sentences from sentence_bench.jsonl

Usage:
    python -m data.benchmark_v2.generate_summary_bench
"""

import json
from pathlib import Path

ANNOTATIONS_DIR = Path(__file__).parent.parent / "benchmark_annotations"
SENTENCE_BENCH = Path(__file__).parent / "sentence_bench.jsonl"
OUTPUT_PATH = Path(__file__).parent / "summary_bench.jsonl"


def load_sentence_bench() -> dict[str, dict[str, list[str]]]:
    """Load sentence_bench.jsonl -> {pmcid: {variant: [sentences]}}."""
    data: dict[str, dict[str, list[str]]] = {}
    with open(SENTENCE_BENCH) as f:
        for line in f:
            rec = json.loads(line)
            pmcid = rec["pmcid"]
            if pmcid not in data:
                data[pmcid] = {}
            data[pmcid][rec["variant"]] = rec["sentences"]
    return data


def extract_study_params(annotation: dict, variant_ann_id: str) -> list[dict]:
    """Extract study parameters linked to a specific variant annotation."""
    params = []
    for sp in annotation.get("study_parameters", []):
        if str(sp.get("Variant Annotation ID_norm", "")) == str(variant_ann_id):
            params.append(
                {
                    "study_type": sp.get("Study Type"),
                    "study_cases": sp.get("Study Cases"),
                    "study_controls": sp.get("Study Controls"),
                    "characteristics": sp.get("Characteristics"),
                    "p_value": sp.get("P Value"),
                    "ratio_stat_type": sp.get("Ratio Stat Type"),
                    "ratio_stat": sp.get("Ratio Stat"),
                    "ci_start": sp.get("Confidence Interval Start"),
                    "ci_stop": sp.get("Confidence Interval Stop"),
                    "biogeographical_groups": sp.get("Biogeographical Groups"),
                }
            )
    return params


def extract_association(ann: dict, annotation: dict) -> dict:
    """Extract a structured association from a var_drug_ann or var_pheno_ann entry."""
    variant_ann_id = str(
        ann.get("Variant Annotation ID_norm", ann.get("Variant Annotation ID", ""))
    )

    assoc = {
        "variant": ann.get("Variant/Haplotypes"),
        "gene": ann.get("Gene"),
        "drug": ann.get("Drug(s)"),
        "phenotype_category": ann.get("Phenotype Category"),
        "significance": ann.get("Significance"),
        "direction": ann.get("Direction of effect"),
        "alleles": ann.get("Alleles"),
        "comparison": ann.get("Comparison Allele(s) or Genotype(s)"),
        "sentence": ann.get("Sentence"),
        "is_associated": ann.get("Is/Is Not associated"),
    }

    # Add type-specific fields
    if "PD/PK terms" in ann:
        assoc["pd_pk_terms"] = ann["PD/PK terms"]
    if "Side effect/efficacy/other" in ann:
        assoc["side_effect_terms"] = ann["Side effect/efficacy/other"]
    if "Phenotype" in ann:
        assoc["phenotype"] = ann["Phenotype"]

    # Population info
    population_phenotypes = ann.get("Population Phenotypes or diseases")
    if population_phenotypes:
        assoc["population"] = population_phenotypes

    # Link study parameters
    assoc["study_parameters"] = extract_study_params(annotation, variant_ann_id)

    return assoc


def generate_summary_bench():
    """Generate summary_bench.jsonl from all annotation files."""
    sentence_data = load_sentence_bench()

    records = []
    annotation_files = sorted(ANNOTATIONS_DIR.glob("PMC*.json"))

    for ann_file in annotation_files:
        with open(ann_file) as f:
            annotation = json.load(f)

        pmcid = annotation["pmcid"]

        # Extract all associations from var_drug_ann and var_pheno_ann
        associations = []
        for ann in annotation.get("var_drug_ann", []):
            associations.append(extract_association(ann, annotation))
        for ann in annotation.get("var_pheno_ann", []):
            associations.append(extract_association(ann, annotation))

        if not associations:
            print(f"  {pmcid}: no associations found, skipping")
            continue

        # Get ground truth sentences for this article
        gt_sentences = sentence_data.get(pmcid, {})

        # Count unique variants
        variants = list(set(a["variant"] for a in associations if a["variant"]))

        record = {
            "pmcid": pmcid,
            "pmid": annotation.get("pmid"),
            "title": annotation.get("title"),
            "num_associations": len(associations),
            "num_variants": len(variants),
            "variants": variants,
            "associations": associations,
            "ground_truth_sentences": gt_sentences,
        }
        records.append(record)

    # Sort by PMCID for consistency
    records.sort(key=lambda r: r["pmcid"])

    # Write output
    with open(OUTPUT_PATH, "w") as f:
        for rec in records:
            f.write(json.dumps(rec) + "\n")

    print(f"Generated {OUTPUT_PATH}")
    print(f"  {len(records)} articles")
    total_assoc = sum(r["num_associations"] for r in records)
    print(f"  {total_assoc} total associations")
    total_variants = sum(r["num_variants"] for r in records)
    print(f"  {total_variants} total unique variants")


if __name__ == "__main__":
    generate_summary_bench()
