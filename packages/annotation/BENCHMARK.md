# Annotation v2 benchmark baseline

Run date: 2026-11-19

Model: `gpt-6.2-sol`

Corpus: 32 articles in `data/benchmark/benchmark_annotations/by_pmcid`

All 32 articles completed without request, schema, or job-level failures. Generated
outputs are stored locally in `data/annotation_v2/runs/gpt-6.2-sol` and are ignored
by Git.

## Rough field-level results

The evaluator greedily matches generated and reference records on canonical variant,
drug text, and direction when the reference provides one.

| Metric | Micro | Macro article average |
|---|---:|---:|
| Precision | 0.0522 | 0.0500 |
| Recall | 0.2727 | 0.2467 |
| F1 | 0.0876 | 0.0786 |
| Variant recall | — | 0.4533 |

- Reference associations: 143
- Generated associations: 747
- Matched associations: 39
- Exact, validated evidence quotes: 2,358
- Generated associations with canonical rsID/HLA notation: 67.2%
- Low-confidence generated associations: 9
- Articles with perfect reference-variant recall: 12/32
- Articles with zero reference-variant recall: 13/32

## Interpretation

These scores are a conservative alignment baseline, not an estimate of clinical
correctness. The generated schema is exhaustive and separates adjusted, unadjusted,
negative, and variance-contribution findings. The curated reference often consolidates
those into one annotation. Consequently, many extra grounded records count as false
positives under this evaluator.

Manual inspection of mismatches found four recurring categories:

1. Drug class or abbreviation versus named drug, such as `anti-TNF-alpha therapy`
   versus `etanercept, infliximab`, and `SMX/CTX` versus
   `sulfamethoxazole/trimethoprim`.
2. Source notation versus curated identifier, especially transcript-free `c.`
   notation and composite star-allele/diplotype annotations.
3. Curated composite annotations versus separate model records for each genotype,
   outcome, analysis, or effect direction.
4. Genuine extraction or normalization misses, which still require article-level
   expert review.

Evidence validation proves that each retained quote occurs in its claimed source
block. It does not by itself prove that the quote entails every structured field.

## Next evaluation work

Before considering API integration:

1. Add normalized drug identifiers and aliases using `shared.term_normalization`.
2. Add explicit haplotype/diplotype-to-variant relationships rather than treating one
   display string as the sole variant identity.
3. Evaluate atomic fields independently from record-level matching.
4. Add an entailment audit for each structured record and its evidence bundle.
5. Have a curator review a stratified sample of exact matches, benchmark misses, and
   generated-only records.
