# Plan: Four Paper-Tagging Systems

## Objective

Run four distinct classification systems against every paper using its title and abstract. Each system answers a different question and must remain separately stored, displayed, filtered, and evaluated.

The review specification is in [`label_prompts.yaml`](label_prompts.yaml), with a flattened copy in [`label_prompts.csv`](label_prompts.csv). The active production configuration lives in `packages/shared/shared/triage_prompts.yaml`; its established v3 prompt remains unchanged, and the Paper Type and PGx Information classifiers are defined alongside it as additional classifiers.

## 1. Paper Type

**Question:** What type of paper or PGx contribution is it?

This is an additive multi-label output. A paper may have zero, one, or several types.

- Methods
- Population Frequency
- Allele Function
- Implementation
- Review
- Phenoconversion

Proposed field: `paper_types`

## 2. Annotation Label

**Question:** Should the paper remain in the human literature-review queue?

This is a mutually exclusive classification. Every scored paper receives exactly one value.

- Relevant
- Not Relevant
- Borderline

Production field: `triage_label`

This is the existing three-way triage decision currently represented by `relevant`, `not_relevant`, and `borderline`.

## 3. Curatable Labels

**Question:** Which broad curation workflows could use this paper?

This is an additive multi-label output. A paper may receive neither label, `General PGx` alone, or both labels.

- General PGx
- Variant Annotations

Production field: `triage_tags`

`Variant Annotations` implies `General PGx`. Parser-side validation should add `General PGx` whenever the model returns `Variant Annotations` alone.

## 4. PGx Information Type

**Question:** What subset of PGx information does the paper provide?

This is an additive multi-label output. A paper may have zero, one, or several information types.

- PD — pharmacodynamic results that are not clinical outcomes, such as platelet activity
- Dosage
- Efficacy
- Toxicity
- Other
- Metabolism/PK — all drug-metabolism and pharmacokinetic information

Production field: `pgx_information_types`

`Other` is a fallback for substantive PGx information not covered by the five named categories. It should be used sparingly and normally should not accompany another information type.

## Proposed Data Contract

```json
{
  "triage_label": "relevant",
  "paper_types": ["methods"],
  "triage_tags": ["general_pharmacogenomics", "variant_annotations"],
  "pgx_information_types": ["pd", "efficacy"],
  "reasoning": "The paper reports a substantive gene-drug association.",
  "paper_type_reasoning": "The study develops and validates a PGx assay.",
  "pgx_information_reasoning": "It includes a pharmacodynamic biomarker and clinical response."
}
```

All multi-label fields default to empty arrays for legacy records. Unknown model-produced values must be removed during parsing.

## Model Execution

Run all four classifiers on every paper using:

```text
PMID: {{pmid}}
Title: {{title}}
Abstract: {{abstract_or_not_available}}
```

The prompts must be self-contained and must not rely on local files, hidden annotations, or external lookup. Production uses three model calls: the established v3 call continues producing the annotation label and curatable labels, while Paper Type and PGx Information each use a separate new call. This preserves the evaluated behavior of the existing classifications.

## Backend Work

1. Store all four outputs as separate fields.
2. Validate each output against its own allowlist.
3. Enforce exactly one annotation label.
4. Enforce `variant_annotations` → `general_pharmacogenomics`.
5. Deduplicate multi-label values and preserve a stable display order.
6. Default missing fields to empty arrays when loading older sessions.
7. Populate all four outputs during fetch and regeneration.
8. Preserve submitted curator decisions during regeneration.
9. Add parser, persistence, endpoint, regeneration, and legacy-record tests.

## Frontend Work

1. Display each taxonomy in a separately labeled group.
2. Keep annotation labels visually distinct from additive tags.
3. Provide filters for every label without mixing taxonomies.
4. Use OR semantics within a multi-label filter group by default.
5. Preserve the existing green background and white text for `Variant Annotations`.
6. Keep large filter sets on separate rows so the session header remains usable at narrow widths.

## Evaluation

Evaluate all four systems independently:

- Per-label precision, recall, and F1
- Micro- and macro-F1 for each multi-label taxonomy
- Exact-set match and Hamming loss for multi-label outputs
- Confusion matrix for the three-way annotation label
- Label prevalence and evaluated positive count
- Inspectable false positives and false negatives

Important boundary audits include:

- Methods vs routine use of a standard assay
- Allele Function vs variant association alone
- PD vs Efficacy
- PD vs Metabolism/PK
- General PGx without Variant Annotations
- Borderline vs Not Relevant when an abstract is absent or inconclusive
- Incorrect use of Other when a named PGx information type applies

The classifications should remain advisory during the initial shadow release. They should not automatically submit, exclude, or route papers until the relevant per-label results have been reviewed.

## Acceptance Criteria

- Every scored paper receives one annotation label and outputs from the other three systems.
- The three additive systems support zero or more labels.
- The API and UI preserve taxonomy boundaries.
- Existing sessions load without migration errors.
- Regeneration populates the new fields without resetting submitted decisions.
- Invalid labels cannot reach persisted data or the UI.
- Benchmark reports show metrics and failure cases separately for all four systems.
