# Summary Generation

## Overview

This module generates concise, accessible summaries of pharmacogenomic findings from scientific articles. Given the article text, variants, associations, and optionally citations, it produces structured summaries that highlight key genetic variants and their clinical implications.

The summaries are designed to be more focused and accessible than original abstracts, specifically emphasizing the pharmacogenomic relationships discovered in each study.

## Usage

### Basic Usage

```bash
# Run with defaults (gpt-5 model, v1 prompt, 1 PMCID)
python src/modules/summary/summary.py

# Or run as a module
python -m src.modules.summary.summary
```

### Command Line Options

| Option | Default | Description |
|--------|---------|-------------|
| `--model` | `gpt-5` | LLM model to use (supports gpt-4o, claude-3-5-sonnet, etc.) |
| `--prompt` | `v1` | Prompt version from summary_prompts.yaml |
| `--num-pmcids` | `1` | Number of PMCIDs to process (0 for all) |
| `--citations-file` | None | Path to citations JSON from one_shot_citations.py |

### Examples

```bash
# Process 3 PMCIDs with GPT-4o
python src/modules/summary/summary.py --model gpt-4o --num-pmcids 3

# Use the detailed variant-focused prompt
python src/modules/summary/summary.py --prompt v2

# Process all PMCIDs
python src/modules/summary/summary.py --num-pmcids 0

# Include citations from a previous generation run
python src/modules/summary/summary.py --citations-file src/modules/citations/one_shot_citations/outputs/citations_gpt-4o_v1_20240115.json
```

## Prompt Versions

- **v1 (Basic)**: Generates summaries with Background, Key Findings, and Clinical Implications sections
- **v2 (Variant-focused)**: Organizes findings by genetic variant with detailed clinical context

## Output

Outputs are saved to `outputs/` with the naming convention:
```
summary_{model}_{prompt}_{timestamp}.json
```

### Output Structure

```json
{
  "metadata": {
    "model": "gpt-5",
    "prompt_name": "v1",
    "prompt_description": "Basic summary generation",
    "timestamp": "20240120_143052",
    "num_pmcids": 1,
    "citations_file": null
  },
  "summaries": [
    {
      "pmcid": "PMC5508045",
      "summary": "## Background\n...\n## Key Findings\n...\n## Clinical Implications\n...",
      "num_variants": 4,
      "variants": ["rs9923231", "rs1057910", "rs2108622", "rs887829"]
    }
  ]
}
```

## Data Sources

- **Article text**: Loaded from `data/articles/{pmcid}.md`
- **Variants/Associations**: Loaded from `data/benchmark_v2/sentence_bench.jsonl`
- **Citations** (optional): From one_shot_citations.py output

## Notes

- No evaluation component is currently implemented
- Each PMCID receives a single consolidated summary covering all its variants
- The module follows the same patterns as other experiments in this repository
