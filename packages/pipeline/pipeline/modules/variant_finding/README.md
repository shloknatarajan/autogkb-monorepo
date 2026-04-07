# Variant Finding Module

Extracts pharmacogenomic variant names (e.g. `CYP2D6*4`, `rs1234567`) from a PubMed Central article.

## Inputs

| Parameter | Type | Description |
|-----------|------|-------------|
| `pmcid` | `str` | PubMed Central article ID (e.g. `"PMC10275785"`) |

## Outputs

| Return | Type | Description |
|--------|------|-------------|
| variants | `list[str]` | List of variant names found in the article |

## Example

```python
from generation.modules.variant_finding.variant_extractor import VariantExtractor

extractor = VariantExtractor(method="regex_v5")
variants = extractor.get_variants("PMC10275785")
# ["CYP2C19*2", "CYP2C19*3", "CYP2C19*17", "rs4244285"]
```

## Available Methods

| Method | Description |
|--------|-------------|
| `regex_v1` through `regex_v5` | Regex-based extraction (iterating on patterns) |
| `regex_llm_filter` | Regex extraction + LLM filtering of false positives |
| `regex_term_norm` | Regex extraction + term normalization |
| `just_ask` | LLM-only extraction |
| `pubtator` | PubTator API-based extraction |
| `pgxmine` | PGxMine database lookup |

## How It's Used in the Generation Pipeline

This is **Stage 1** of the pipeline. In `pipeline.py`, the extractor is built from config and called first:

```python
# pipeline.py — Stage 1: Variant Extraction
extractor = VariantExtractor(method=config["variant_extraction"]["method"])
variants: list[str] = extractor.get_variants(pmcid)

# Variants are then filtered to only those in Methods/Results/Supplements
variants = filter_studied_variants(pmcid, variants)
```

If no variants are found, the article is skipped (assumed not to be a PGx article). The resulting `list[str]` is passed downstream to:
- **Term Normalization** (Stage 1.5) — normalizes variant names
- **Sentence Generation** (Stage 2) — generates clinical sentences per variant
