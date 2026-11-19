# AutoGKB annotation v2 experiment

This package is independent of the current pipeline. It uses one structured LLM call
to discover studied pharmacogenomic variants, extract atomic associations, and attach
exact source quotes. Records without a verifiable quote are discarded.

The package loads the repository-level `.env` automatically and does not override
credentials or settings already present in the process environment.

Run an article:

```bash
uv run --package autogkb-annotation annotate-v2 data/articles/PMC5508045.md \
  --output data/annotation_v2/PMC5508045.json
```

Run the rough field-level benchmark against
`data/benchmark/benchmark_annotations/by_pmcid`:

```bash
uv run --package autogkb-annotation benchmark-annotations-v2 data/annotation_v2
```

Run the complete benchmark corpus with resumable outputs:

```bash
uv run --package autogkb-annotation annotate-v2-batch \
  data/articles data/benchmark/benchmark_annotations/by_pmcid \
  data/annotation_v2/runs/gpt-6.2-sol --workers 3
```

The benchmark reports variant recall and association precision/recall/F1 using the
variant, drug, and direction fields. It is intentionally a cheap first-pass metric;
it does not replace expert review of evidence grounding or clinical meaning.
