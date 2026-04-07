# Citations Module

Finds exact supporting quotations from the article text for each generated variant-drug/phenotype association sentence.

## Inputs

| Parameter | Type | Description |
|-----------|------|-------------|
| `pmcid` | `str` | PubMed Central article ID (e.g. `"PMC10275785"`) |
| `associations` | `list[dict]` | List of association dicts, each with keys `"variant"` (`str`), `"sentence"` (`str`), and `"explanation"` (`str`) |

## Outputs

| Return | Type | Description |
|--------|------|-------------|
| citations | `list[Citation]` | One `Citation` per input association |

### `Citation` fields

| Field | Type | Description |
|-------|------|-------------|
| `variant` | `str` | The variant name |
| `sentence` | `str` | The generated association sentence |
| `explanation` | `str` | The explanation (may be empty) |
| `citations` | `list[str]` | Verbatim excerpts from the article supporting the sentence |

## Example

```python
from generation.modules.citations.citation_finder import CitationFinder

finder = CitationFinder(method="one_shot_citations", model="gpt-4o")
associations = [
    {
        "variant": "CYP2C19*2",
        "sentence": "CYP2C19*2 carriers showed reduced clopidogrel efficacy.",
        "explanation": "Based on Table 2 pharmacokinetic data.",
    }
]
citations = finder.find_citations("PMC10275785", associations)
# [
#     Citation(
#         variant="CYP2C19*2",
#         sentence="CYP2C19*2 carriers showed reduced clopidogrel efficacy.",
#         explanation="Based on Table 2 pharmacokinetic data.",
#         citations=[
#             "The AUC of the active metabolite was 34% lower in CYP2C19*2 carriers (P = 0.003).",
#             "Participants with the *2 allele had significantly reduced platelet inhibition."
#         ]
#     )
# ]
```

## Available Methods

| Method | Description |
|--------|-------------|
| `one_shot_citations` | Single LLM call to extract verbatim citations from the article for each association |

## How It's Used in the Generation Pipeline

This is **Stage 3** of the pipeline. It receives the flattened sentence output from Stage 2:

```python
# pipeline.py — Stage 3: Citation Finding
finder = CitationFinder(method=config["citation_finding"]["method"], model=config["citation_finding"]["model"])

# Sentences are flattened into association dicts
associations = [
    {"variant": v, "sentence": s.sentence, "explanation": s.explanation}
    for v, sents in sentences.items()
    for s in sents
]
citations: list[Citation] = finder.find_citations(pmcid, associations)
```

The citation results are passed to:
- **Summary Generation** (Stage 4) — optionally included as `citations_data` for richer summaries
- **Final output** — stored in the `annotation_citations` field of the `GenerationRecord`
