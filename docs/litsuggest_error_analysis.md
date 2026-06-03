# LitSuggest Triage Error Analysis

**Model:** gpt-5.2 | **Dataset:** 500 PharmGKB ground truth records | **Date:** 2026-06-03

See `docs/litsuggest_eval_report.md` for full metric context.

---

## Summary

| Category | Count |
|---|---|
| True relevant → predicted not_relevant (worst misses) | 33 |
| True borderline → predicted not_relevant | 55 |
| True not_relevant → predicted relevant (false positives) | 17 |
| Correctly predicted relevant | 104 / 189 |
| Correctly predicted not_relevant | 119 / 161 |

The dominant failure mode is the model being **too conservative**: 88 papers that should have been surfaced (relevant + borderline) were buried as `not_relevant`.

---

## Where the model got it RIGHT

### Example A — Prototypical correct call (PMID 42092593)
**Title:** "HLA-B alleles confer susceptibility to sulfasalazine-induced severe cutaneous adverse reactions"  
**True:** relevant | **Predicted:** relevant  
**Why it worked:** All three elements are explicit in the title — named HLA alleles (B*39:01, B*13:01, B*38:02), named drug (sulfasalazine), named phenotype (SCARs/DRESS). The model reasoning: *"Primary research identifying specific HLA-B alleles associated with sulfasalazine-induced SCARs."* Unambiguous; this is exactly what the prompt was designed for.

### Example B — Correct star-allele detection (PMID 42080361)
**Title:** "Impact of UGT1A1\*28 polymorphism in patients with metastatic pancreatic ductal adenocarcinoma treated with NALIRIFOX in the NAPOLI-3 trial"  
**True:** relevant | **Predicted:** relevant  
**Why it worked:** Named star allele (UGT1A1\*28) + named drug (irinotecan) + toxicity phenotype (diarrhea, neutropenia). Textbook pharmacogenomics.

### Example C — Correct CYP + pharmacological phenotype (PMID 42072346)
**Title:** "Impact of CYP and ABCB1 Polymorphisms on Bortezomib-Induced Adverse Events in Multiple Myeloma"  
**True:** relevant | **Predicted:** relevant  
**Why it worked:** Explicit CYP3A4 metabolizer phenotypes + ABCB1 genotypes + bortezomib + adverse event phenotypes. The model caught that *"specific genetic polymorphisms (ABCB1 C1236T, CYP3A4 metabolizer phenotypes) in relation to a specific drug and multiple pharmacological phenotypes"* qualified.

### What the model handles well
- Papers with rsIDs, star alleles, or HLA alleles explicitly in the title/abstract are reliably caught
- Clear not-relevant noise (gene-disease papers, sequencing-only papers without drug context) is correctly filtered
- Precision on the binary task is high (0.857): when it surfaces something, it's almost always worth reviewing

---

## Where it got it WRONG: False Negatives

### Pattern 1 — CYP enzyme-level studies (most impactful, ~20 of the 33 missed papers)

The model requires **named genetic variants** (rsIDs/star alleles/HLA alleles) to classify a paper as relevant or borderline. But PharmGKB also curates papers where the pharmacogene is mentioned only at **enzyme level** — because CYP inhibition data implies annotatable allele associations (e.g. CYP2C19 poor metabolizer = CYP2C19\*2/\*3).

| PMID | Title (truncated) | Pred | True | Model reasoning |
|---|---|---|---|---|
| 42053458 | "In Vitro and Clinical Evaluation of Potential Interactions of Bemnifosbuvir with Drug-Metabolizing Enzymes" | not_relevant | relevant | "drug–drug interaction study (bemnifosbuvir with midazolam/CYP3A4) reporting PK changes but contains no genetic variant information" |
| 42039693 | "A Drug-Drug Interaction of Ainuovirine with Fluconazole in Healthy Adults… Pharmacokinetics Model" | not_relevant | relevant | "CYP2C19 inhibition… does not report any specific genetic variants or genotype–phenotype associations" |
| 42003086 | "Effects of Cedirogant on the Pharmacokinetics of Sensitive Cytochrome P450 Probe Substrates" | not_relevant | relevant | "examines cedirogant's effects on CYP enzyme activities… does not report any genetic variants" |

**What's happening:** All three papers have drug + named CYP enzyme + PK phenotype — the exact three elements a human curator looks for. The model rejects them because no rsID/star allele is named. PharmGKB curates them because the enzyme-level data is extractable as variant annotations (e.g. CYP3A4\*22 carriers would be affected by CYP3A4 inhibition data).

**Same pattern drives ~half of the 55 borderline → not_relevant misses:** DDI studies with CYP pathway involvement and drug + PK outcomes are consistently buried.

### Pattern 2 — Attitude/survey papers curated for context (harder to fix)

| PMID | Title | Pred | True | Note |
|---|---|---|---|---|
| 42026041 | "Cardiovascular prescriber attitudes to pharmacogenomics: a survey by the ESC working group" | not_relevant | relevant | Model correctly notes no variant annotations; PharmGKB curates survey papers for context. Genuinely ambiguous whether these belong in scope. |

---

## False Positives ("junk surfaced")

**Key insight: most of the 17 "false positives" are actually correct PGx calls.** The ground truth label `Not Curated` means this PharmGKB project team hasn't processed the paper yet — **not** that it lacks PGx content.

| PMID | Title (truncated) | Pred | True label | Analysis |
|---|---|---|---|---|
| 41892871 | "Explainable AI Unravels…VKORC1 and CYP2C9 in Predicting Warfarin Anticoagulation" | relevant | Not Curated | Classic warfarin PGx paper with VKORC1/CYP2C9 genotypes + TTR phenotype. Model is almost certainly right. |
| 41882691 | "Methotrexate metabolism-related gene polymorphisms and therapeutic efficacy and toxicity" | relevant | Not Curated | SLC19A1 variants (rs4149096, rs2291075) + MTX + toxicity outcomes. Textbook PGx. |
| 41889154 | "Novel Genetic Risk Factor Identified for L-Asparaginase-Induced Pancreatitis in Pediatric Patients" | relevant | Not Curated | GWAS identifying rs149210846 for drug-induced pancreatitis in ALL patients. Clearly cuatable. |
| 42014364 | "Utility of Patch Testing, LTT, and HLA Genotyping in Identifying Culprit Drugs" | relevant | Not Curated | HLA-B\*58:01, HLA-A\*31:01 + multiple drugs + DRESS/SJS phenotypes. Legitimate PGx. |

**Conclusion:** The model's true false positive rate is lower than 17/500. These represent gaps in the ground truth, not model errors. The pipeline is actually more aggressive than PharmGKB's current curation queue, which is a reasonable behavior.

---

## Borderline Confusion

The `borderline` class has the worst F1 (0.429). The confusion matrix shows it bleeds in both directions:

- **33 true-borderline → predicted relevant** (over-promoted): Papers where the model saw gene + drug + phenotype language but PharmGKB considered it preliminary/uncertain evidence. Often EGFR-mutant NSCLC trial papers where the mutation is an eligibility criterion, not the study endpoint.
- **55 true-borderline → predicted not_relevant** (over-demoted): Same CYP enzyme pattern as false negatives. DDI studies with CYP pathway + drug + PK outcome but no named alleles.

The borderline class is inherently noisy in the ground truth: PharmGKB's "Relevant" state means "flagged as potentially interesting but not yet fully curated." Improving borderline F1 is partly a data quality problem.

---

## Root Cause Summary

| Issue | Impact | Fix |
|---|---|---|
| Requires explicit rsID/star allele to score borderline | ~20 relevant + ~30 borderline papers missed | Add rule: named CYP enzyme + drug + PK/PD outcome = borderline minimum |
| No tie-break when uncertain | Conservative bias pushes uncertain cases to not_relevant | Add: "when uncertain between borderline and not_relevant, prefer borderline" |
| "Not Curated" ground truth is noisy | Inflates false positive count by ~10-15 | Acknowledge in eval metrics; true precision is higher |

See `feature/litsuggest-prompt-v2` branch for the improved prompt and re-scoring experiment.
