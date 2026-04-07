# Summary Module

Generates a high-level natural language summary of all pharmacogenomic findings for an article, incorporating variant associations and optional citation data.

## Inputs

| Parameter | Type | Description |
|-----------|------|-------------|
| `pmcid` | `str` | PubMed Central article ID (e.g. `"PMC10275785"`) |
| `variants_data` | `list[dict]` | List of dicts with keys `"variant"` (`str`) and `"sentences"` (`list[str]`) |
| `citations_data` | `dict[str, list[dict]] \| None` | Optional. Maps pmcid to list of citation dicts from Stage 3 |

## Outputs

| Return | Type | Description |
|--------|------|-------------|
| summary | `ArticleSummary` | Summary object for the article |

### `ArticleSummary` fields

| Field | Type | Description |
|-------|------|-------------|
| `pmcid` | `str` | The article's PMCID |
| `summary` | `str` | The generated natural language summary |
| `num_variants` | `int` | Number of variants covered |
| `variants` | `list[str]` | List of variant names included |

## Example

```python
from generation.modules.summary.summary_generator import SummaryGenerator

summarizer = SummaryGenerator(method="basic_summary", model="gpt-4o")
variants_data = [
    {"variant": "CYP2C19*2", "sentences": ["CYP2C19*2 carriers showed reduced clopidogrel efficacy."]},
    {"variant": "CYP2D6*4", "sentences": ["CYP2D6*4 was associated with poor metabolizer status."]},
]
summary = summarizer.generate("PMC10275785", variants_data, citations_data=None)
# ArticleSummary(
#     pmcid="PMC10275785",
#     summary="This study examined pharmacogenomic associations in clopidogrel and tamoxifen metabolism...",
#     num_variants=2,
#     variants=["CYP2C19*2", "CYP2D6*4"]
# )
```

## Available Methods

| Method | Description |
|--------|-------------|
| `basic_summary` | Single LLM call to summarize all variant findings for the article |

## How It's Used in the Generation Pipeline

This is **Stage 4** (final stage) of the pipeline. It consumes output from Stages 2 and 3:

```python
# pipeline.py — Stage 4: Summary Generation
summarizer = SummaryGenerator(method=config["summary_generation"]["method"], model=config["summary_generation"]["model"])

variants_data = [
    {"variant": v, "sentences": [s.sentence for s in sents]}
    for v, sents in sentences.items()
]
citations_data = {pmcid: [c.model_dump() for c in citations]} if citations else None

summary: ArticleSummary = summarizer.generate(pmcid, variants_data, citations_data)
```

The summary is stored in the `annotation_data.summary` field of the final `GenerationRecord` written to `data/generations.jsonl`.
