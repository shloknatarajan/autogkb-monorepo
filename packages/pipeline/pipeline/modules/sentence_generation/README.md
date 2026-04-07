# Sentence Generation Module

Generates clinical pharmacogenomic association sentences for each variant found in an article (e.g. "Patients with CYP2C19*2 showed reduced clopidogrel efficacy").

## Inputs

| Parameter | Type | Description |
|-----------|------|-------------|
| `pmcid` | `str` | PubMed Central article ID (e.g. `"PMC10275785"`) |
| `variants` | `list[str]` | Variant names to generate sentences for |

## Outputs

| Return | Type | Description |
|--------|------|-------------|
| sentences | `dict[str, list[GeneratedSentence]]` | Maps each variant name to its generated sentences |

### `GeneratedSentence` fields

| Field | Type | Description |
|-------|------|-------------|
| `sentence` | `str` | The clinical association sentence |
| `explanation` | `str` | Supporting explanation/reasoning (may be empty) |

## Example

```python
from generation.modules.sentence_generation.sentence_generator import SentenceGenerator

generator = SentenceGenerator(method="llm_judge_ask", model="gpt-4o")
sentences = generator.generate("PMC10275785", ["CYP2C19*2", "CYP2D6*4"])
# {
#     "CYP2C19*2": [
#         GeneratedSentence(
#             sentence="CYP2C19*2 carriers showed significantly lower active metabolite levels of clopidogrel.",
#             explanation="Table 2 shows reduced AUC in *2 carriers (p<0.01)."
#         )
#     ],
#     "CYP2D6*4": [
#         GeneratedSentence(
#             sentence="CYP2D6*4 was associated with poor metabolizer status for tamoxifen.",
#             explanation="Figure 3 demonstrates reduced endoxifen concentrations."
#         )
#     ]
# }
```

## Available Methods

| Method | Description |
|--------|-------------|
| `raw_sentence_ask` | Single LLM call per variant to generate sentences |
| `llm_judge_ask` | LLM generates + a judge LLM filters for quality |
| `batch_judge_ask` | Batch version of judge-filtered generation |

## How It's Used in the Generation Pipeline

This is **Stage 2** of the pipeline. It receives the (optionally normalized) variants from earlier stages:

```python
# pipeline.py — Stage 2: Sentence Generation
generator = SentenceGenerator(method=config["sentence_generation"]["method"], model=config["sentence_generation"]["model"])
sentences: dict[str, list[GeneratedSentence]] = generator.generate(pmcid, variants)
```

The output dict is consumed by two downstream stages:
- **Citation Finding** (Stage 3) — flattens sentences into `[{variant, sentence, explanation}]` associations to find supporting citations
- **Summary Generation** (Stage 4) — uses `[{variant, sentences: [str]}]` to produce an article-level summary
