# Term Normalization Module

Maps raw variant names extracted from articles to standardized PharmGKB nomenclature using fuzzy matching.

## Inputs

| Parameter | Type | Description |
|-----------|------|-------------|
| `pmcid` | `str` | PubMed Central article ID (e.g. `"PMC10275785"`) |
| `variants` | `list[str]` | Raw variant names from the variant extraction stage |

## Outputs

| Return | Type | Description |
|--------|------|-------------|
| result | `NormalizationResult` | Contains `normalized_variants: list[str]` and `mappings: list[VariantMapping]` |

### `NormalizationResult` fields

| Field | Type | Description |
|-------|------|-------------|
| `normalized_variants` | `list[str]` | The normalized variant names |
| `mappings` | `list[VariantMapping]` | Per-variant mapping details |

### `VariantMapping` fields

| Field | Type | Description |
|-------|------|-------------|
| `original` | `str` | Original variant name |
| `normalized` | `str` | Normalized variant name |
| `pharmgkb_id` | `str \| None` | PharmGKB ID if matched |
| `score` | `float \| None` | Match confidence score |
| `changed` | `bool` | Whether the name was modified |

## Example

```python
from generation.modules.term_normalization.term_normalizer import TermNormalizer

normalizer = TermNormalizer(method="pharmgkb_fuzzy", threshold=80)
result = normalizer.normalize("PMC10275785", ["CYP2C19 *2", "cyp2d6*4"])
# result.normalized_variants = ["CYP2C19*2", "CYP2D6*4"]
# result.mappings[0].original = "CYP2C19 *2"
# result.mappings[0].normalized = "CYP2C19*2"
# result.mappings[0].changed = True
```

## Available Methods

| Method | Description |
|--------|-------------|
| `pharmgkb_fuzzy` | Fuzzy matching against PharmGKB's variant/allele database |

## How It's Used in the Generation Pipeline

This is **Stage 1.5** of the pipeline, running between variant extraction and sentence generation:

```python
# pipeline.py — Stage 1.5: Term Normalization
normalizer = TermNormalizer(method="pharmgkb_fuzzy")
norm_result: NormalizationResult = normalizer.normalize(pmcid, variants)

# The normalized list replaces the raw variants for all downstream stages
variants = norm_result.normalized_variants
```

The normalized variant names are what get passed to Sentence Generation (Stage 2), ensuring consistent naming across the output.
