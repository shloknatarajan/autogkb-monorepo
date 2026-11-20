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

## Entity-table prompt and ID-based matching

Two changes followed the baseline above.

**Evaluator.** Reference and generated variants and drugs are now resolved through
the same `shared.term_normalization` lookup and compared on canonical keys. A
reference annotation that lists several alleles or drugs matches a generated record
that shares at least one of each. Rescoring the unchanged baseline run:

| Baseline run | Matched | Precision | Recall | F1 | Macro variant recall |
|---|---:|---:|---:|---:|---:|
| String matching | 39 | 0.0522 | 0.2727 | 0.0876 | 0.4533 |
| Canonical-key matching | 47 | 0.0629 | 0.3287 | 0.1056 | 0.6814 |

**Pipeline.** Variants and drugs are looked up before the LLM call and listed in the
prompt; the model cites table references instead of free text, and the prompt defines
one record as one curator-level finding. Post-hoc normalization heuristics,
`variant_mentions`, and `confidence` were removed. Output:
`data/annotation_v2/runs/gpt-6.2-sol-entity-table` (32/32 articles completed).

| Metric (canonical-key matching) | Baseline | Entity table |
|---|---:|---:|
| Generated associations | 747 | 542 |
| Matched associations (of 143) | 47 | 74 |
| Micro precision | 0.0629 | 0.1365 |
| Micro recall | 0.3287 | 0.5175 |
| Micro F1 | 0.1056 | 0.2161 |
| Macro precision | 0.0948 | 0.2212 |
| Macro recall | 0.3720 | 0.6290 |
| Macro F1 | 0.1400 | 0.2857 |
| Macro variant recall | 0.6814 | 0.9116 |

**Model.** The same prompt was run with `gpt-6-astra`, which is now the default.
Output: `data/annotation_v2/runs/gpt-6-astra-entity-table` (32/32 completed).

| Metric (entity-table prompt) | `gpt-6.2-sol` | `gpt-6-astra` |
|---|---:|---:|
| Generated associations | 542 | 517 |
| Matched associations (of 143) | 74 | 75 |
| Micro precision | 0.1365 | 0.1451 |
| Micro recall | 0.5175 | 0.5245 |
| Micro F1 | 0.2161 | 0.2273 |
| Macro F1 | 0.2857 | 0.3164 |
| Macro variant recall | 0.9116 | 0.9428 |

The two models agree on the match count in 29 of 32 articles and share the same
zero-match articles; with one run each, the difference is within plausible run-to-run
variation.

**Direction fix and HLA rule.** Two further changes:

- The evaluator now treats a reference annotation marked "Not associated with" as
  `no_association` even when it carries a direction word ("is not associated with
  increased risk"). All figures above this point predate that fix.
- The prompt now asks for one record per HLA allele. Output:
  `data/annotation_v2/runs/gpt-6-astra-hla-per-allele` (32/32 completed).

| Metric (`gpt-6-astra`, fixed evaluator) | Entity table | + HLA per allele |
|---|---:|---:|
| Generated associations | 517 | 564 |
| Matched associations (of 143) | 86 | 116 |
| Micro precision | 0.1663 | 0.2057 |
| Micro recall | 0.6014 | 0.8112 |
| Micro F1 | 0.2606 | 0.3281 |
| Macro precision | 0.3038 | 0.3048 |
| Macro recall | 0.7707 | 0.7925 |
| Macro F1 | 0.3872 | 0.3950 |
| Macro variant recall | 0.9428 | 0.9428 |

The micro gain comes from one article: PMC5561238 holds 43 of the 143 reference
annotations and went from 6 to 36 matched. No other article changed its match count,
which is why the macro figures barely move.

A deterministic consolidation pass over the baseline (regex exclusion and merging)
reached micro F1 0.1213 under the same evaluator and was dropped in favour of the
prompt change.

Remaining gaps:

- The model still returns about four records per reference annotation. Part of that
  is real findings the curated set does not include; the rest is over-splitting in
  large multi-variant papers (for example PMC8973308: 57 records, 0 matched).
- Only 319 of 1,060 variant entries in the run carry a ClinPGx ID, because star and
  HLA alleles are not in the local variant table. They are compared by canonical
  notation instead.
- Five articles have full variant recall but no matched record, so the mismatch there
  is in drug or direction.

## Next evaluation work

Before considering API integration:

1. Add star and HLA alleles to the local lookup so they resolve to ClinPGx IDs.
2. Inspect the articles with full variant recall and no matched record to separate
   drug-naming mismatches from direction mismatches.
3. Evaluate atomic fields independently from record-level matching.
4. Add an entailment audit for each structured record and its evidence bundle.
5. Have a curator review a stratified sample of exact matches, benchmark misses, and
   generated-only records.
