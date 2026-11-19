# Term Normalization

## Goal
Take incoming variant annotations and replace all terms with normalized identifiers that map to current entries in ClinPGx and PharmGKB. This ensures consistent terminology across pharmacogenomic data.

## Overview

The term normalization package is split into domain-specific subpackages:
- **`genes/`**: Gene-associated variants, alleles, and SNP notation expansion
- **`drugs/`**: Drug names, generic names, trade names, and RxNorm fallback

## Architecture

### Main Components

```text
term_normalization/
├── drugs/
│   └── search.py          # DrugLookup and DrugSearchResult
├── genes/
│   ├── variant_search.py  # VariantLookup and VariantSearchResult
│   └── snp_expansion.py   # SNPExpander and SNPMapping
├── search_utils.py        # Cross-domain similarity matching
└── term_lookup.py         # Combined lookup and annotation normalization
```

The package root continues to export the primary lookup classes for convenient,
backwards-compatible imports.

## Usage

### Normalizing an Annotation File

```python
from pathlib import Path
from shared.term_normalization.term_lookup import normalize_annotation

input_path = Path("data/example_annotation.json")
output_path = Path("data/example_annotation_normalized.json")

normalize_annotation(input_path, output_path)
```

This will:
1. Load the annotation JSON file
2. Normalize all `Variant/Haplotypes` and `Drug(s)` fields in annotation types: `var_pheno_ann`, `var_fa_ann`, `var_drug_ann`
3. Add normalized fields with `_normalized` suffix (e.g., `Variant/Haplotypes_normalized`)
4. Include a `term_mappings` section with details about each normalized term

### Using TermLookup Directly

```python
from shared.term_normalization.term_lookup import TermLookup, TermType

lookup = TermLookup()

# Search for a variant
variant_results = lookup.search("rs12345", term_type=TermType.VARIANT, threshold=0.8, top_k=1)

# Search for a drug
drug_results = lookup.search("aspirin", term_type=TermType.DRUG, threshold=0.8, top_k=1)
```

## Variant Normalization

The `VariantLookup` class handles variant normalization with the following features:

### Search Strategy

1. **rsID Lookup** (for variants starting with "rs"):
   - Queries PharmGKB API (`/v1/data/variant`)
   - Searches local ClinPGx variant database (`data/term_lookup_info/variants.tsv`)
   - Searches variant names and synonyms

2. **Star Allele Lookup** (for variants like *1, *2):
   - Queries PharmGKB API (`/v1/data/haplotype`)
   - Searches local ClinPGx variant database

### Return Format

```python
VariantSearchResult(
    raw_input="rs12345",
    id="PA166154595",
    normalized_term="rs12345",
    url="https://www.clinpgx.org/variant/PA166154595",
    score=1.0
)
```

## Drug Normalization

The `DrugLookup` class handles drug normalization with the following features:

### Search Strategy

1. **ClinPGx Lookup** (primary):
   - Builds and caches an index from `data/term_lookup_info/drugs.tsv`
   - Normalizes case, punctuation, strength, route, and dosage-form wording
   - Checks exact canonical, generic, and trade names first
   - Falls back to ranked fuzzy matching only when there is no exact match
   - Marks close or shared-alias matches as ambiguous
   - Returns canonical names and PharmGKB Accession IDs

2. **RxNorm Lookup** (fallback):
   - Queries RxNorm API when ClinPGx search yields no results
   - Returns the RxNorm term and RxCUI independently of PharmGKB coverage
   - Applies a local confidence threshold to approximate API candidates
   - Provides broader brand, formulation, and drug-name coverage

The optional `rxcui_to_pa_id()` utility can enrich an RxNorm identifier with a
PharmGKB ID when a mapping exists, but fallback success does not require one.
The `source` field distinguishes `pharmgkb` results from `rxnorm` results.

### Return Format

```python
DrugSearchResult(
    raw_input="Coumadin",
    id="PA451906",
    normalized_term="warfarin",
    url="https://www.clinpgx.org/chemical/PA451906",
    score=1.0,
    matched_term="Coumadin",
    match_type="exact_trade",
    ambiguous=False,
    source="pharmgkb",
)
```

### Normalizing Drugs in Text

Use the separate `normalize_drugs_in_text()` function to find known drug names
in a paragraph while preserving their original spelling and locations:

```python
from shared.term_normalization.drugs import normalize_drugs_in_text

result = normalize_drugs_in_text(
    "Patients receiving Coumadin were switched to warfarin sodium 5 mg."
)
```

The result is keyed by each original mention:

```python
{
    "Coumadin": {
        "count": 1,
        "spans": [[19, 27]],
        "ambiguous": False,
        "matches": [
            {
                "normalized_term": "warfarin",
                "id": "PA451906",
                "url": "https://www.clinpgx.org/chemical/PA451906",
                "score": 1.0,
                "matched_term": "Coumadin",
                "match_type": "exact_trade",
                "source": "pharmgkb",
            }
        ],
    }
}
```

Text extraction uses known exact aliases and longest-match precedence. It does
not fuzzy-match arbitrary words in prose, because doing so would turn ordinary
language into false drug mentions. Each extracted mention is resolved through
the same `DrugLookup` index used for single-term normalization.

This function is a grounded candidate extractor, not a semantic classifier.
Broad or ordinary-word aliases present in ClinPGx can still require contextual
review; the preserved spans, candidate IDs, and ambiguity flags are designed to
support a later deterministic or LLM-based review pass.

### LLM-Reviewed Abstract Normalization

Use `normalize_drugs_in_abstract()` when contextual review is required:

```python
from shared.term_normalization.drugs import normalize_drugs_in_abstract

matches = normalize_drugs_in_abstract(abstract, model="gpt-5.4")
```

This function sends the abstract and deterministic candidate suggestions to the
LLM. The model returns only exact mention strings; it never supplies identifiers.
Each accepted string is verified against the abstract and resolved through
`DrugLookup`, so PharmGKB and RxNorm identifiers remain grounded in their source
vocabularies. The model may also recover a valid mention omitted by the initial
alias scan. A valid mention unavailable in both lookup sources is retained with
`matches: []` rather than being discarded.

Malformed model output falls back to deterministic candidates by default. Set
`fallback_to_deterministic=False` when callers should receive the model error
instead.

## Data Requirements

The module requires local TSV files in the `data/term_lookup_info/` directory:

- `variants.tsv`: Variant names, IDs, and synonyms from ClinPGx
- `drugs.tsv`: Drug names, generic names, trade names, RxNorm IDs, and PharmGKB IDs

## Configuration

### Parameters

- **`threshold`** (default: 0.8): Minimum similarity score for fuzzy matching (0.0-1.0)
- **`top_k`** (default: 1): Number of top results to return
- **`ambiguity_margin`** (default: 0.03): Marks close top candidates as ambiguous
- **`use_rxnorm_fallback`** (default: true): Enables lookup for locally unresolved terms
- **`data_dir`** (default: "data"): Base directory for lookup TSV files

### Similarity Matching

The drug package compares normalized full strings and token-sorted strings after
exhausting exact canonical and alias matches, allowing for:
- Typos and spelling variations
- Case insensitivity
- Different token ordering

Strings shorter than four characters are fuzzy-matched only through exact aliases
to avoid collision-prone guesses. Results below the threshold are left unresolved.

## Output Format

The `normalize_annotation()` function adds:

1. **Normalized fields** in each annotation object:
   - `Variant/Haplotypes_normalized`: PharmGKB variant ID
   - `Drug(s)_normalized`: PharmGKB drug ID

2. **Term mappings section** at the root level:
```json
{
  "term_mappings": {
    "rs6539870": {
      "raw_input": "rs6539870",
      "id": "PA166154595",
      "normalized_term": "rs6539870",
      "url": "https://www.clinpgx.org/variant/PA166154595",
      "score": 1.0
    },
    "etoposide": {
      "raw_input": "etoposide",
      "id": "PA449552",
      "normalized_term": "etoposide",
      "url": "https://www.clinpgx.org/chemical/PA449552",
      "score": 1.0
    }
  }
}
```

## SNP Notation Expansion

The `SNPExpander` class handles informal SNP notation expansion - converting shorthand notations like "516G>T" to rsIDs like "rs3745274".

### The Problem

Scientific articles often use informal SNP notations:
- `CYP2B6 516G>T` (position + substitution)
- `CYP2B6-G516T` (reversed format)
- `VKORC1-1639 G>A` (promoter variant with spaces)

But databases use formal rsIDs (`rs3745274`) or HGVS notation (`NM_000767.4:c.516G>T`).

### Usage

```python
from shared.term_normalization.genes import SNPExpander, create_expander

# Quick initialization
expander = create_expander()

# Lookup a single notation
mapping = expander.lookup("CYP2B6", "516G>T")
print(mapping.rsid)  # "rs3745274"

# Expand notations in text
text = "The CYP2B6 516G>T variant affects metabolism"
expanded = expander.expand_text(text)
# "The CYP2B6 516G>T (rs3745274) variant affects metabolism"
```

### Integrated with TermLookup

```python
from shared.term_normalization.term_lookup import TermLookup

lookup = TermLookup()  # SNP expansion enabled by default

# Extract all rsIDs from article text (including expanded notations)
rsids = lookup.extract_rsids_from_text(article_text)

# Expand SNP notations in text
expanded_text = lookup.expand_text(article_text)
```

### Data Source

Mappings are built from PharmGKB haplotype data:
- Fetches star allele definitions for key pharmacogenes
- Extracts HGVS synonyms and parses to simple notations
- Caches to `data/term_lookup_info/snp_notation_mappings.json`

Covered genes: CYP2B6, CYP2C9, CYP2C19, CYP2D6, CYP3A4, CYP3A5, VKORC1, TPMT, DPYD, UGT1A1, SLCO1B1, NUDT15

## Future Work

### Gene Symbol Normalization
The `genes` subpackage currently covers gene-associated variants and SNP
notation. Canonical gene-symbol normalization is not yet implemented and will
require:
- HGNC gene symbol lookup
- Gene ID normalization
- Alias resolution

### Phenotype Normalization
Currently not implemented. Will require:
- Ontology mapping (HPO, MeSH, etc.)
- Phenotype term standardization

### Metabolizer Status Extraction
For extracting phenotype classifications like "poor metabolizer" - requires semantic extraction, not just lookup.
