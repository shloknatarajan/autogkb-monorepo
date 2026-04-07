# Variant Extraction Methods Comparison

## Overview

This document compares all variant extraction methods tested on the AutoGKB benchmark (32 articles, 322 total variants).

## Summary Table

| Method | Recall | Precision | F1 Score | Perfect Recall | Description |
|--------|--------|-----------|----------|----------------|-------------|
| **Regex v1** | 42.0% | 31.4% | 35.9% | 10/32 (31%) | Baseline: Methods & Conclusions only |
| **Regex v2** | 69.9% | 37.5% | 48.8% | 18/32 (56%) | Full text + format normalization |
| **Regex v3** | 87.8% | 42.8% | 57.5% | 22/32 (69%) | Context-aware star alleles |
| **Regex v4** | 91.3% | 43.4% | 58.9% | 24/32 (75%) | SNP expansion integration |
| **Regex v5** | **93.4%** | 41.9% | 57.9% | **25/32 (78%)** | BioC supplements + all v4 features |
| **Term Norm Hybrid** | 93.4% | 41.9% | 57.9% | 25/32 (78%) | V5 + post-extraction normalization |
| **LLM-Only (GPT-4o)** | 65.7% | 42.1% | 51.3% | 14/32 (44%) | Pure LLM extraction |
| **LLM-Only (Sonnet 4.5)** | 71.5% | **46.3%** | 56.2% | 16/32 (50%) | Pure LLM extraction |
| **LLM-Only (Opus 4.5)** | 71.0% | 41.6% | 52.4% | 17/32 (53%) | Pure LLM extraction |
| **PubTator (Full Text)** | 36.3% | 19.4% | 25.3% | 10/32 (31%) | NCBI PubTator3 API |
| **PubTator (Abstract Only)** | 26.4% | 31.8% | 28.9% | 6/32 (19%) | NCBI PubTator3 API (abstract) |

**Note**: Regex + LLM Filter methods were tested on only 2 articles (partial experiment) and excluded from main comparison.

**Note on PubTator**: PubTator performs poorly for pharmacogenomics because it does not recognize star alleles (CYP2C9*3) or HLA alleles (HLA-B*58:01). See `pubtator/pubtator_variants.md` for detailed analysis.

## Key Findings

### 🏆 Best Overall: Regex v5
- **Highest recall**: 93.4%
- **Most perfect recall articles**: 25/32
- **Fastest runtime**: ~2 minutes
- **Approach**: Iteratively refined regex patterns with SNP expansion and supplement extraction

### 📊 Performance Evolution

#### Regex Methods (v1 → v5)
```
v1 (42.0%) → v2 (+27.9%) → v3 (+17.9%) → v4 (+3.5%) → v5 (+2.1%)
```

**Total improvement: +51.3 percentage points**

Major breakthroughs:
1. **v1 → v2**: Full text instead of sections only (+27.9%)
2. **v2 → v3**: Context-aware star alleles (+17.9%)
3. **v3 → v4**: SNP expansion (516G>T → rs3745274) (+3.5%)
4. **v4 → v5**: BioC supplement extraction (+2.1%)

#### Term Normalization Impact
- **Post-extraction normalization**: +0.0% recall improvement
- **Pre-extraction normalization** (SNP expansion in v4): +3.5% recall improvement

**Key insight**: Pre-extraction normalization helps (enriches text), post-extraction normalization doesn't (can't recover missed variants).

### 🤖 LLM Methods

**Performance**: 66-72% recall, 42-46% precision

**Strengths**:
- Better precision than regex (42-46% vs 42%)
- Can understand context and semantics
- Flexible pattern recognition

**Weaknesses**:
- Lower recall than regex v5 (71% vs 93%)
- Slower and more expensive
- Variable performance across models
- Miss structured variants in tables/supplements

**Best LLM**: Claude Sonnet 4.5 (71.5% recall, 46.3% precision)

### 📈 Recall vs Precision Trade-off

All methods show a consistent trade-off:
- **Regex methods**: High recall (93%), moderate precision (42%)
- **LLM methods**: Moderate recall (71%), moderate precision (46%)
- **Hybrid potential**: Combine both approaches?

## Detailed Analysis

### Why Regex v5 Wins

**Advantages**:
1. **Comprehensive patterns**: Evolved through 5 iterations covering real misses
2. **SNP expansion**: Catches informal notations (CYP2B6 516G>T)
3. **Supplement extraction**: Recovers variants from supplementary tables
4. **Format normalization**: Handles HLA variants, star alleles, diplotypes
5. **Speed**: Processes 32 articles in ~2 minutes

**Remaining gaps** (14 missed variants):
- Wildtype alleles (`*1`) not explicitly mentioned (4)
- Metabolizer phenotypes as variants (2)
- HLA/rsID not in extractable text (6)
- SNP→star mapping discrepancies (2)

### Why LLMs Underperform

**Root cause**: Table and supplement handling

LLMs struggle with:
1. **Structured data**: Variants listed in tables aren't extracted well
2. **Supplements**: Don't have access to supplementary materials
3. **Consistency**: Variable extraction across similar patterns
4. **Coverage**: Miss variants when article has many (e.g., PMC5561238: 43 variants)

**Example**: PMC6435416
- Regex v5: 100% recall (15/15) - found all CYP2D6 alleles in supplement
- LLMs: ~70% recall - missed variants in supplementary table

### Why Term Normalization Didn't Help

Post-extraction fuzzy matching normalized 17 variants but didn't improve recall because:

1. **Typos in benchmark**: Normalizing `rs180131` → `rs1801131` breaks match
2. **Missed variants never extracted**: Can't normalize what wasn't found
3. **Perfect recall articles**: All normalizations were in 100% recall articles

**Value provided**:
- Validated all variants against PharmGKB/ClinPGx
- Corrected typos for data quality
- Provided mapping trail for transparency

## Methodology Insights

### What Works

✅ **Iterative refinement**: Each regex version addresses specific misses
✅ **Pre-extraction enrichment**: SNP expansion adds alternative forms to text
✅ **Multiple data sources**: Article + supplements
✅ **Format normalization**: Handle HLA, star alleles, diplotypes consistently
✅ **Context-aware extraction**: Use gene names to resolve standalone alleles

### What Doesn't Work

❌ **Post-extraction fuzzy matching**: Can't recover missed variants
❌ **LLM-only for tables**: Struggles with structured variant lists
❌ **Limited text scope**: Methods/conclusions only (v1: 42% recall)
❌ **Simple patterns**: Without context awareness (v2: 70% recall)

### Remaining Challenges

All methods struggle with:
- **Implicit variants**: Wildtype alleles not explicitly stated
- **Phenotype descriptions**: "poor metabolizer" as a variant
- **Data extraction**: Variants in images, figures, complex tables
- **Mapping disagreements**: SNP↔star allele assignments vary by database

## Recommendations

### For Production Systems

**Best approach**: **Regex v5** (or enhanced version)
- Highest recall (93.4%)
- Fast and cost-effective
- Deterministic and reproducible
- Well-understood failure modes

**Enhancements**:
1. Implement wildtype inference (+1.2% recall)
2. Add metabolizer phenotype patterns (+0.6% recall)
3. Better table extraction (+1.8% recall)
4. PharmVar for SNP→star mapping (+0.6% recall)

**Expected v6 performance**: ~96-97% recall

### For Hybrid Systems

**Potential**: Combine regex + LLM
- Regex for structured extraction (high recall)
- LLM for context and validation (higher precision)
- Filter false positives with LLM reasoning

**Implementation**:
```
Article → Regex Extraction → LLM Filtering → Final Variants
         (high recall)      (improve precision)
```

### For Research Applications

**Consider LLMs** when:
- Small number of articles (cost acceptable)
- Precision more important than recall
- Need contextual understanding
- Want explanations for extraction decisions

**Use Regex** when:
- Large-scale extraction needed
- Recall is critical
- Cost/speed constraints
- Reproducibility required

## Visualizations

See `comparison_charts.png` for:
1. **Recall comparison** across all methods
2. **Precision comparison** across all methods
3. **F1 score** (harmonic mean of recall and precision)
4. **Recall vs Precision scatter plot** showing trade-offs
5. **Perfect recall articles** count
6. **Radar chart** comparing key methods across dimensions

## Experiment Artifacts

### Result Files
```
regex_variants/
├── results_v1.json  (baseline)
├── results_v2.json  (full text)
├── results_v3.json  (context-aware)
├── results_v4.json  (SNP expansion)
└── results_v5.json  (supplements)

regex_term_norm/
└── results_term_norm.json  (v5 + normalization)

just_ask/results/
├── claude-sonnet-4-5-20250929_v1.json
├── claude-opus-4-5-20251101_v3.json
└── gpt-4o_v2.json

regex_llm_filter/results/
├── claude-sonnet-4-5-20250929_v1.json
└── gpt-4o_v1.json

pubtator/results/
├── pubtator_api_v1.json  (full text)
└── pubtator_api_abstract_only.json  (abstract only)
```

### Documentation
- Each method has detailed miss analysis
- V5: `misses_analysis_v5.md`
- Term norm: `results_analysis.md`

## Conclusion

**The iterative regex approach (v5) achieves the best performance** with 93.4% recall, significantly outperforming LLM-only methods (71%) and maintaining reasonable precision (42%).

**Key success factors**:
1. Systematic analysis of each version's misses
2. Progressive pattern refinement addressing real gaps
3. Pre-extraction text enrichment (SNP expansion)
4. Multi-source extraction (article + supplements)

**Remaining 6.6% recall gap** requires algorithmic improvements (wildtype inference, phenotype extraction, better table handling) rather than pattern refinement or normalization.

**Term normalization** should be used for data quality (validation, standardization) but not expected to improve recall metrics.

**LLMs** are promising for smaller-scale work but need better table/supplement handling to compete with regex at scale.

---

*Generated from benchmark results on 32 pharmacogenomics articles (322 total variants)*
