# Raw Sentence Ask Experiment Log

Started: 2025-01-19 8:24 PM

## Overview
For an article, let's have the model be given the markdown text + a single variant and ask it to construct a sentence that describes the associations for the experiment similar to how the ground truth sentences are made. Will need some prompt iterations to get this right. Let's just try the largest available claude model and the largest available openai model and see how things looks


## Process Hypothesis
1. Get the markdown for the article
2. Run the best performing variant extraction pipeline (v5) to get all the variants
3. Ask the model to construct a sentence that describes the association for the experiment similar to how the ground truth sentences are made. Additional notes can be made at the end but the first sentence should be the main association identical to the ground truth sentence for that variant.
4. Compare the results against the ground truth sentences and iterate on the prompt a bit until you get reasonable accuracy.
5. Only do this for two articles to start with and report results in the Experiment Results section of this file.

## Notes
- Save the tried prompts in separate files from the code. Preferably some sort of yaml file(s)
- Use litellm for api calls
- use load_dotenv() for api keys


## Experiment Results

### Test Articles
- **PMC5508045**: Warfarin dosing study (4 variants: rs9923231, rs1057910, rs2108622, rs887829)
- **PMC554812**: Allopurinol HLA study (5 variants: HLA-B*58:01, HLA-DRB1*03:01, HLA-C*03:02, HLA-A*33:03, rs1594)

### Prompt Versions
- **v1**: Basic prompt with general guidelines
- **v2**: Structured prompt with concrete examples of expected output format
- **v3**: Dual format prompt - explicitly asks for genotype format FIRST (e.g., "Genotypes CT + TT of rs9923231")

### Results Summary

| Model | Prompt | Avg Similarity | High (≥70%) | Medium (40-70%) | Low (<40%) |
|-------|--------|----------------|-------------|-----------------|------------|
| Claude Sonnet 4 | v1 | 33.9% | 0 | 4 | 5 |
| **Claude Sonnet 4** | **v2** | **51.8%** | **2** | **4** | **3** |
| Claude Opus 4.5 | v1 | 29.3% | 0 | 0 | 9 |
| Claude Opus 4.5 | v2 | 44.5% | 0 | 6 | 3 |
| GPT-4o | v1 | 37.3% | 0 | 3 | 6 |
| **GPT-4o** | **v2** | **53.9%** | **1** | **6** | **2** |

### Key Findings

1. **v2 prompt significantly outperforms v1**: Providing concrete examples of expected output format improved similarity by ~15-20% across all models.

2. **GPT-4o with v2 achieved best results** (53.9% avg similarity), followed closely by Claude Sonnet 4 with v2 (51.8%).

3. **Surprisingly, Claude Opus 4.5 performed worse than Claude Sonnet 4** across both prompt versions. This may be due to Opus being more verbose and including additional context.

4. **HLA variants performed better than rsID variants**: The HLA article (PMC554812) had higher similarity scores, likely because the ground truth format closely matches how HLA associations are described in the literature.

### Main Issues Identified

1. **Genotype format mismatch**: Models often output rsID or gene names instead of specific genotypes
   - Ground truth: "Genotypes CT + TT are associated with..."
   - Model output: "rs9923231 is associated with..." or "VKORC1 genotypes GA and AA are associated with..."

2. **Population description differences**: Models describe specific study populations rather than standardized medical conditions
   - Ground truth: "...in people with Atrial Fibrillation, heart valve replacement..."
   - Model output: "...in Thai patients..."

3. **Wrong variant association**: Some variants (especially rs1594) returned information about a different variant (HLA-B*58:01), likely because the article emphasizes the HLA finding more prominently.

4. **Additional explanatory text**: v1 prompt sometimes produced explanatory text before/after the sentence, reducing similarity scores.

### Sample Outputs (v2 Prompt, GPT-4o)

**High similarity (81%)** - HLA-C*03:02:
- Generated: "HLA-C*03:02 is associated with increased risk of severe cutaneous adverse reactions when treated with allopurinol."
- Ground truth: "HLA-C *03:02 is associated with increased risk of severe cutaneous adverse reactions when treated with allopurinol."

**Medium similarity (41%)** - rs2108622:
- Generated: "The CYP4F2 rs2108622 TT genotype is associated with increased warfarin dose requirements in Thai patients as compared to genotype CC."
- Ground truth: "Genotype TT is associated with increased dose of warfarin in people with Atrial Fibrillation, heart valve replacement, Hypertension, Pulmonary, Pulmonary Embolism and Venous Thrombosis as compared to genotype CC."

---

## Enhanced Experiment (with Term Normalization)

Added improvements from the regex variant extraction pipeline:
1. **BioC supplement integration**: Uses `get_combined_text()` to include supplementary material
2. **SNP expander variant context**: Provides alternative notations for rsIDs (e.g., "rs9923231 → VKORC1 -1639G>A")

### Enhanced Results Summary

| Model | Prompt | Enhancements | Avg Similarity | High (≥70%) | Medium (40-70%) | Low (<40%) |
|-------|--------|--------------|----------------|-------------|-----------------|------------|
| Claude Sonnet 4 | v2 | None | 51.8% | 2 | 4 | 3 |
| **Claude Sonnet 4** | **v2** | **+suppl +context** | **54.4%** | **2** | **4** | **3** |
| GPT-4o | v2 | None | 53.9% | 1 | 6 | 2 |
| GPT-4o | v2 | +suppl +context | 52.0% | 2 | 3 | 4 |

### Enhanced Experiment Observations

1. **Claude Sonnet improved slightly** (+2.6% with enhancements), while GPT-4o slightly decreased (-1.9%)

2. **Variant context is being provided** - For rsIDs, the model now sees alternative notations:
   - rs9923231: "Also known as: VKORC1 -1639G>A, VKORC1 1639G>A"
   - rs1057910: "Also known as: CYP2C9 1075A>C, CYP2C9*1"
   - rs887829: "Also known as: UGT1A1 -364C>T, UGT1A1*80"

3. **Supplement integration limited impact**: Only 1/2 test articles had supplement data available (PMC5508045)

4. **Core issues persist**: The main bottleneck is still:
   - Models use rsID/gene names instead of genotype format (CT+TT)
   - Population descriptions don't match standardized vocabulary

### Article Sizes

| PMCID | Article | Supplement | Total | ~Tokens |
|-------|---------|------------|-------|---------|
| PMC5508045 | 53KB | 3KB | 56KB | ~14k |
| PMC554812 | 42KB | 0 | 42KB | ~11k |

Both well within model context limits (Claude: 200k, GPT-4o: 128k).

---

## V3 Prompt Experiment (Dual Format)

To address the genotype format mismatch issue, created v3 prompt that explicitly asks models to:
1. Start with genotype format FIRST (e.g., "Genotypes CT + TT", "Allele T")
2. Include the rsID after (e.g., "of rs9923231")
3. Provides explicit examples of the desired format

### V3 Results Summary

| Model | v2 Similarity | v3 Similarity | Change | High (≥70%) |
|-------|---------------|---------------|--------|-------------|
| **GPT-4o** | 52.0% | **63.6%** | **+11.6%** | **4** |
| Claude Sonnet 4 | 54.4% | 50.9% | -3.5% | 0 |
| Gemini 2.0 Flash | 50.1% | 49.0% | -1.1% | 4 |

### V3 Key Finding

**GPT-4o significantly improved with v3** (+11.6%), achieving:
- **100% match** on HLA-B*58:01
- **81% matches** on HLA-DRB1*03:01, HLA-C*03:02, HLA-A*33:03
- Better genotype format adherence for rsID variants

Claude and Gemini did not benefit from v3 - they appear less responsive to explicit format instructions.

### V3 Sample Outputs (GPT-4o)

**Perfect match (100%)** - HLA-B*58:01:
- Generated: "HLA-B *58:01 is associated with increased risk of severe cutaneous adverse reactions when treated with allopurinol."
- Ground truth: "HLA-B *58:01 is associated with increased risk of severe cutaneous adverse reactions when treated with allopurinol."

**Good format (48%)** - rs2108622:
- Generated: "Genotype TT of rs2108622 is associated with increased dose of warfarin as compared to genotype CC."
- Ground truth: "Genotype TT is associated with increased dose of warfarin in people with Atrial Fibrillation, heart valve replacement... as compared to genotype CC."

**Improved format (48%)** - rs887829:
- Generated: "Allele T of rs887829 is not associated with dose of warfarin as compared to allele C."
- Ground truth: "Allele C is not associated with dose of warfarin in people with Atrial Fibrillation... as compared to allele T."

---

## Multi-Model Comparison (All Models Tested)

### Full Results Table (v2 prompt with enhancements)

| Model | Avg Similarity | High (≥70%) | Medium (40-70%) | Low (<40%) |
|-------|----------------|-------------|-----------------|------------|
| Claude Sonnet 4 | 54.4% | 2 | 4 | 3 |
| GPT-4o | 52.0% | 2 | 3 | 4 |
| Gemini 2.0 Flash | 50.1% | 1 | 4 | 4 |
| Gemini 2.5 Pro | 46.8% | 0 | 5 | 4 |
| Claude Opus 4.5 | 44.5% | 0 | 6 | 3 |

### Full V3 Results (All Models)

| Model | v3 Similarity | High (≥70%) | Medium (40-70%) | Low (<40%) |
|-------|---------------|-------------|-----------------|------------|
| **GPT-4o** | **63.6%** | **4** | 3 | 2 |
| Claude Sonnet 4 | 50.9% | 0 | 7 | 2 |
| o1 | 50.6% | 0 | 6 | 3 |
| Gemini 2.0 Flash | 49.0% | 4 | 1 | 4 |
| GPT-5 | 48.5% | 0 | 7 | 2 |
| o3-mini | 40.0% | 1 | 2 | 6 |

### Best Configuration Found

**GPT-4o + v3 prompt + enhancements = 63.6% average similarity**

This is the highest performing configuration tested, with 4 high-similarity matches (≥70%).

**Surprising finding**: GPT-4o outperforms both GPT-5 and o1 reasoning models on this task. The newer models may be overthinking or not following format instructions as closely.

---

## Manual Qualitative Analysis: GPT-4o vs GPT-5

The similarity metric doesn't tell the full story. Here's a side-by-side comparison of actual outputs:

### PMC5508045 (Warfarin Study)

#### rs9923231
| Source | Sentence |
|--------|----------|
| **Ground truth** | "Genotypes **CT + TT** are associated with decreased dose of warfarin in people with Atrial Fibrillation... as compared to genotype **CC**." |
| **GPT-4o** | "Genotypes **GA + AA** of rs9923231 are associated with decreased dose of warfarin as compared to genotype **GG**." |
| **GPT-5** | "Genotypes **GA + AA** of rs9923231 are associated with decreased **stable weekly** dose of warfarin as compared to genotype **GG**." |

**Analysis**: Both use GA/AA instead of CT/TT - this is the **same variant on opposite DNA strand** (both scientifically correct). GPT-5 adds "stable weekly" which is more specific to the article.

#### rs1057910
| Source | Sentence |
|--------|----------|
| **Ground truth** | "Genotypes **AC + CC** are associated with decreased dose of warfarin... as compared to genotype **AA**." |
| **GPT-4o** | "Genotypes ***1/*3 and *3/*3** of CYP2C9*3 (rs1057910) are associated with decreased dose..." |
| **GPT-5** | "Genotypes **AC + CC** of rs1057910 are associated with decreased stable weekly dose... as compared to genotype **AA**." |

**Analysis**: **GPT-5 matches the exact genotype format (AC + CC)!** GPT-4o uses star allele notation which is equivalent but different style.

#### rs2108622
| Source | Sentence |
|--------|----------|
| **Ground truth** | "Genotype **TT** is associated with **increased** dose of warfarin... as compared to genotype **CC**." |
| **GPT-4o** | "Genotype **TT** of rs2108622 is associated with **increased** dose of warfarin as compared to genotype **CC**." |
| **GPT-5** | "Genotype **TT** of rs2108622 is associated with **increased** stable weekly dose of warfarin as compared to genotype **CC**." |

**Analysis**: Both nail the core association - nearly identical outputs.

### PMC554812 (Allopurinol HLA Study)

#### HLA-B*58:01
| Source | Sentence |
|--------|----------|
| **Ground truth** | "HLA-B *58:01 is associated with increased risk of severe cutaneous adverse reactions when treated with allopurinol." |
| **GPT-4o** | "HLA-B *58:01 is associated with increased risk of severe cutaneous adverse reactions when treated with allopurinol." |
| **GPT-5** | "HLA-B*58:01 **carriers** are associated with increased risk... **as compared to non-carriers**." |

**Analysis**: **GPT-4o = PERFECT 100% match**. GPT-5 adds "carriers" and comparison language - scientifically accurate but more verbose.

### Key Qualitative Findings

1. **GPT-4o follows the ground truth format more closely** - concise, matches expected style
2. **GPT-5 is more scientifically verbose** - adds "carriers", "stable weekly", "as compared to non-carriers"
3. **Both models capture the correct associations** - the underlying science is right in both
4. **Strand differences (CT/TT vs GA/AA) are NOT errors** - just different strand conventions, both valid
5. **GPT-5 sometimes uses better genotype format** (e.g., "AC + CC" for rs1057910 matches ground truth exactly)
6. **GPT-5's verbosity hurts similarity scores** but produces scientifically thorough outputs

### Conclusion

The Jaccard similarity metric penalizes GPT-5 for being more thorough. For actual pharmacogenomics annotation:
- **GPT-4o** is better if you need outputs matching a specific format/style
- **GPT-5** may be preferable if you want more scientifically complete sentences

Both models produce usable, scientifically accurate outputs for the core associations.

---

### Next Steps

1. **Test v3 on more articles**: Validate that GPT-4o + v3 improvement holds across more articles.

2. **Add phenotype vocabulary**: Provide list of standardized phenotype/condition terms from PharmGKB to address population description differences.

3. **Consider article-type-specific prompts**: HLA articles may need different prompting than CYP/rsID articles.

4. **Improve similarity metric**: Current Jaccard similarity penalizes minor word differences. Consider semantic similarity or key-element extraction.
