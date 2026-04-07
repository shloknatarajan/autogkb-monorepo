# AutoGKB Benchmark System Overview

## Purpose

The AutoGKB Benchmark System evaluates the quality and accuracy of genomic knowledge base annotations by comparing automatically generated annotations against manually curated ground truth. It provides quantitative scoring across multiple annotation types to assess performance of annotation systems.

## Data Flow

```
Ground Truth JSON + Proposed JSON
    ↓
Load & Parse Annotations
    ↓
Expand Multi-Variant Entries
    ↓
Align by Variant/Gene/Drug
    ↓
Validate Dependencies
    ↓
Compute Field-Level Scores
    ↓
Apply Penalties for Violations
    ↓
Aggregate to Benchmark Scores
    ↓
Weighted Overall Score
```

## Annotation Types Evaluated

The system benchmarks four distinct annotation categories:

1. **Drug Annotations** (`var_drug_ann`) - Weight: 1.5
   - Drug-gene-variant associations
   - Pharmacokinetic/pharmacodynamic relationships
   - Dosage effects and metabolizer types
   - ~20 structured fields including variants, genes, drugs, significance, direction of effect

2. **Phenotype Annotations** (`var_pheno_ann`) - Weight: 1.5
   - Disease-gene-variant associations
   - Clinical phenotypes linked to genetic variants
   - Similar structure to drug annotations but focused on disease outcomes

3. **Functional Analysis** (`var_fa_ann`) - Weight: 1.0
   - Molecular and functional effects of variants
   - Gene products, molecular consequences, and functional terms
   - Links variants to their biological mechanisms

4. **Study Parameters** (`study_parameters`) - Weight: 1.0
   - Statistical evidence from research studies
   - Study design, sample sizes, p-values, effect sizes
   - Allele frequencies and confidence intervals

## Scoring Methodology

### Field-Level Evaluation

Each annotation field is scored using the most appropriate comparison metric:

- **Exact Match** (0 or 1): PMIDs, categorical IDs
- **Category Equal** (0 or 1): Normalized categorical fields (Significance, Study Type)
- **Semantic Similarity** (0-1): Free text fields using PubMedBERT embeddings
- **Variant Substring Match** (0-1): Normalized variant/haplotype strings
- **Numeric Tolerance** (0-1): Numeric fields with tolerance bands (±5% = 0.9, ±10% = 0.8)
- **P-value Comparison**: Special handling for statistical significance values with operators

### Score Aggregation

1. **Field scores** are computed for each matched annotation pair
2. **Annotation scores** are averaged across all fields (optionally weighted)
3. **Benchmark scores** aggregate all annotations of that type
4. **Overall score** is computed as weighted average across all four benchmarks

The weighting gives higher priority to drug and phenotype annotations (1.5x) versus functional analysis and study parameters (1.0x).

## Intelligent Alignment System

The benchmark uses sophisticated matching to pair ground truth and predicted annotations:

**Priority Matching Order:**
1. **Normalized Variant ID** - Exact match on standardized variant identifiers
2. **rsID Intersection** - Matching by reference SNP IDs
3. **Substring Containment** - Normalized variant string matching
4. **Gene + Drug Fallback** - For multi-gene/drug scenarios

This ensures annotations are correctly paired even when variant representations differ between systems (e.g., "rs9923231" vs "chr16:31107689:C:T").

**Variant Expansion:** Annotations listing multiple variants are automatically expanded into separate entries for more accurate alignment.

## Validation and Penalties

### Dependency Validation

The system checks for logical inconsistencies:

**Drug Annotations:**
- Direction of effect requires "Associated with" status (not "Not associated")
- Comparison alleles require variants to be specified
- Multiple drug operators should match drugs field

**Functional Analysis:**
- Gene product requires gene specification
- Functional terms should reference gene products

**Study Parameters:**
- Confidence intervals must be valid ranges (start < stop)
- Effect sizes should fall within their confidence intervals
- Frequencies must be between 0 and 1

**Penalty System:** Violations result in score reductions up to 30% for affected fields.

## Tracking Unmatched Annotations

The benchmark explicitly tracks two types of mismatches:

- **Unmatched Ground Truth**: Annotations that should exist but are missing (gaps in coverage)
- **Unmatched Predictions**: Annotations predicted but not in ground truth (hallucinations)

Both reduce the overall score and are reported in detail for error analysis.

## Output Structure

Results are structured JSON containing:

```json
{
  "pmid": "28550460",
  "pmcid": "PMC5508045",
  "overall_score": 0.197,
  "benchmarks": {
    "drug_annotations": {
      "overall_score": 0.357,
      "total_samples": 4,
      "field_scores": {"Gene": 0.95, "Drug(s)": 0.87, ...},
      "detailed_results": [...],
      "unmatched_ground_truth": [...],
      "unmatched_predictions": [...]
    },
    ...
  }
}
```

Key metrics:
- `overall_score`: Weighted aggregate across all benchmarks
- `field_scores`: Per-field averages showing which fields perform poorly
- `detailed_results`: Granular field-by-field comparison for each matched pair
- `unmatched_*`: Lists of missing/hallucinated annotations

## Score Interpretation

| Score Range | Interpretation |
|-------------|---------------|
| 1.0         | Perfect match |
| 0.9-0.99    | Excellent (minor differences) |
| 0.7-0.89    | Good (small issues) |
| 0.5-0.69    | Fair (significant differences) |
| 0.0-0.49    | Poor (major issues) |

## Quick Usage

```bash
# Run benchmark on all files
PYTHONPATH=src pixi run python src/benchmark/run_benchmark.py

# Benchmark single file with details
PYTHONPATH=src pixi run python src/benchmark/run_benchmark.py \
    --single_file PMC5508045 --save_analysis

# Compare against custom annotations
PYTHONPATH=src pixi run python src/benchmark/run_benchmark.py \
    --proposed_dir data/my_annotations --output_file results.json
```

## Implementation Details

**Core Files:**
- `run_benchmark.py` (586 lines): Main orchestration and CLI
- `drug_benchmark.py` (418 lines): Drug annotation evaluation
- `pheno_benchmark.py` (473 lines): Phenotype annotation evaluation
- `fa_benchmark.py` (471 lines): Functional analysis evaluation
- `study_parameters_benchmark.py` (494 lines): Study parameters evaluation
- `shared_utils.py` (195 lines): Common utilities and metrics

**Key Dependencies:**
- `sentence-transformers`: PubMedBERT for semantic similarity
- `scikit-learn`, `numpy`: Numerical operations
- Standard library for file I/O and regex processing



## Use Cases

- **System Development**: Iteratively improve annotation quality by identifying weak fields
- **Comparative Evaluation**: Compare multiple annotation systems on standardized ground truth
- **Quality Assurance**: Validate annotation pipelines before production deployment
- **Error Analysis**: Drill down into specific mismatches to understand failure modes
