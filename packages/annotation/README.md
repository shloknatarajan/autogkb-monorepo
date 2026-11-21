# AutoGKB annotation v2 experiment

This package is independent of the current pipeline. Before the LLM call it runs
`shared.term_normalization` over the article and lists the recognised variants and
drugs at the top of the prompt, each with a short reference (`V3`, `D1`). One
structured LLM call then returns curator-level findings that cite those references
and quote the source verbatim. Application code only checks the quotes, attaches the
referenced entities, and merges exact duplicates; records without a verifiable quote
or a usable variant are discarded.

Entities the lookup does not recognise (metabolizer phenotypes, unusual notations)
are returned as the article writes them, with a null `id`. Star and HLA alleles are
canonicalised by notation because the local variant table does not contain them.

## Annotation methods

Every annotation method subclasses `AnnotationExtractor` (`annotation/base.py`) and
registers itself by name. A method returns associations in one display shape
(variant, sentence, explanation, citations), so the API and viewer do not depend on
which method ran. `extractor.run(...)` wraps the result with the method name, model,
start and completion timestamps, and elapsed time.

| Name | Where | What it does |
|---|---|---|
| `grounded_v2` | `annotation/methods/grounded_v2/` | The entity-table extraction described above |
| `sentences_v5` | `annotation/methods/sentences_v5.py` | The earlier regex, sentence, and citation pipeline |

To add a method, subclass `AnnotationExtractor`, set `name` and `default_model`,
implement `extract` in a new module under `annotation/methods/`, and import it from
`annotation/methods/__init__.py`. `base.py` holds only the shared interface; the
benchmark, CLI, and batch runner stay at the package top level.

The API runs the method named by the `ANNOTATION_METHOD` environment variable
(default `grounded_v2`); `/regenerate` and `/analyze` also accept a `method` field,
and `GET /annotation-methods` lists what is available. Each run is stored as its own
`annotation_jobs` row with the method, model, and timestamps in
`generation_metadata`; the viewer shows the newest completed run, and
`GET /jobs/pmid/{pmid}/runs` lists all of them.

The article summary is a separate step (`packages/api/src/summary.py`). It runs after
whichever annotation method was used, with its own model set by `SUMMARY_MODEL`
(default `gpt-6-astra`), and is recorded separately in `generation_metadata`.

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
  data/annotation_v2/runs/gpt-6-astra-entity-table --workers 3
```

The benchmark resolves reference and generated variants and drugs through the same
lookup and matches records that share a variant, a drug, and the direction. A
reference annotation often lists several alleles or drugs; sharing one is enough. It
is intentionally a cheap first-pass metric; it does not replace expert review of
evidence grounding or clinical meaning.
