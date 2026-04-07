# PGxMine: Research & Local Pipeline Analysis

## What is PGxMine?

PGxMine is a text-mining tool developed by Jake Lever and Russ Altman (Stanford) that extracts **pharmacogenomic associations** (drug-variant relationships) from biomedical literature. It uses the Kindred relation classifier (logistic regression) to identify sentences where drugs and genetic variants co-occur, then classifies whether a genuine pharmacogenomic association is described. Results are used for curation into PharmGKB.

- **GitHub:** https://github.com/jakelever/pgxmine
- **Data (Zenodo):** https://zenodo.org/records/6617348
- **Last data update:** ~June 2022

## Current Implementation (Static Lookup)

Our `pxgmine.py` method downloads the pre-computed `pgxmine_sentences.tsv` (~110 MB) from Zenodo and performs a PMID lookup. This works for any paper already processed in PGxMine's corpus (PubMed/PMC up to mid-2022) but cannot handle newer papers.

## PGxMine Pipeline Architecture

The pipeline has 4 main stages:

```
Input: BioC XML with PubTator NER annotations (Chemical, Gene, Mutation entities)
  │
  ├─ [findPGxSentences.py]  Sentence filtering + star allele annotation
  │     - Filters docs containing PGx filter terms (~31 prefixes like "associat", "pharmaco")
  │     - Annotates star alleles (e.g., "CYP2D6*4") by pairing Gene + *N patterns
  │     - Keeps only sentences with BOTH a Mutation AND Chemical entity
  │     - Parses with scispaCy for tokenization + dependency parsing
  │
  ├─ [getRelevantMeSH.py]   MeSH age-group classification (optional, parallel)
  │     - Classifies papers as pediatric/adult based on MeSH headings
  │     - Can be skipped if age flags aren't needed
  │
  ├─ [createKB.py]          Kindred classifier training + relation extraction
  │     - Loads bundled gold-standard training annotations
  │     - Filters entities against curated drug list and variant stopwords
  │     - Trains LogisticRegression classifier (Kindred) from scratch
  │     - Runs in TWO modes: "star_rs" (star alleles + rsIDs) and "other" (protein/DNA mutations)
  │     - Applies classifier to target sentences, outputs scored predictions
  │
  └─ [filterAndCollate.py]  Score thresholding + deduplication
        - Filters predictions at score >= 0.75
        - Deduplicates by (pmid, formatted_sentence)
        - Outputs: pgxmine_unfiltered.tsv, pgxmine_sentences.tsv, pgxmine_collated.tsv
```

## Dependencies

### Python Packages
- `kindred` — relation extraction framework (wraps scikit-learn)
- `scispacy` + `en_core_sci_sm-0.4.0` — sentence splitting and dependency parsing
- `bioc` — BioC XML parsing
- `snakemake` — workflow manager (for batch runs)

### Reference Data (one-time build via `prepareData.sh`)
| File | Source | Purpose |
|---|---|---|
| `selected_chemicals.json` | MeSH + DrugBank + PharmGKB | Curated drug list with IDs |
| `dbsnp_selected.tsv` | dbSNP VCF + PubTator | rsID-to-gene name mappings |
| `gene_names.tsv` | NCBI Entrez Gene | Gene ID to symbol mappings |
| `stopword_variants.txt` | Bundled | ~300+ false positive variant terms |
| `pgx_filter_terms.txt` | Bundled | ~31 PGx-related filter prefixes |

### Training Annotations (bundled in repo)
- `annotations.variant_star_rs.bioc.xml.gz` — gold-standard examples for star alleles and rsIDs
- `annotations.variant_other.bioc.xml.gz` — gold-standard examples for protein/DNA mutations

## Key Design Characteristics

1. **No saved model.** The Kindred classifier retrains from scratch every run using the bundled annotation files. Training is fast (small corpus) but architecturally awkward for wrapping.

2. **Requires PubTator NER pre-annotations.** PGxMine does NOT do its own named entity recognition. It expects BioC XML input with Chemical, Gene, and Mutation entities already tagged by PubTator Central (using tmChem, GNormPlus, tmVar).

3. **Two-pass classification.** The classifier runs separately for "star_rs" variants and "other" variants with different entity filtering logic.

4. **Relation extraction, not just NER.** PGxMine specifically identifies **drug-variant associations** — it may miss variants that appear in text without a drug co-mention in the same sentence.

## Feasibility for Local Single-Paper Inference

### Approach Options

| Approach | Effort | Coverage | Notes |
|---|---|---|---|
| Zenodo lookup (current) | Done | Papers up to June 2022 | Static, no new papers |
| PubTator API → PGxMine pipeline | Medium-high | Any PubMed/PMC paper | PubTator API already used in `pubtator.py` |
| Full local NER + PGxMine | High | Any arbitrary text | Requires running tmVar/tmChem/GNormPlus locally |

### Minimum Steps for Single-Paper Wrapping

```
1. Get BioC XML with NER annotations
   - Call PubTator3 API (already done in pubtator.py) to get BioC JSON
   - Convert to BioC XML format expected by PGxMine

2. Run findPGxSentences.py
   - Needs: pgx_filter_terms.txt, scispaCy model
   - Filters and parses sentences

3. Run createKB.py
   - Needs: training annotations, selected_chemicals.json,
     dbsnp_selected.tsv, gene_names.tsv, stopword_variants.txt
   - Trains classifier + runs inference

4. Parse TSV output, filter by score >= 0.75
```

### Challenges

- **BioC format mismatch:** PubTator3 API returns BioC JSON; PGxMine expects BioC XML. Conversion is needed.
- **Reference data build:** Creating `selected_chemicals.json` requires DrugBank (manual download with account), MeSH, and PharmGKB data. The test data versions are minimal but functional.
- **scispaCy version pinning:** PGxMine pins `en_core_sci_sm-0.4.0` which may conflict with other spaCy versions in the project.

### Value Proposition

Questionable for this codebase given:
- regex_v5 already achieves **93.4% recall** for variant extraction
- PGxMine is optimized for **drug-variant associations**, not raw variant mentions — it will miss variants not paired with a drug in the same sentence
- The static lookup already covers the benchmark papers (all pre-2022)
- PubTator API (already implemented) provides similar external-database coverage for new papers
