import json
from pathlib import Path

import pytest

from shared.term_normalization.genes import (
    GeneLookup,
    VariantLookup,
    normalize_genes_and_variants_in_text,
    normalize_genes_in_abstract,
)


@pytest.fixture()
def genetic_lookups(tmp_path: Path) -> tuple[GeneLookup, VariantLookup]:
    lookup_dir = tmp_path / "term_lookup_info"
    lookup_dir.mkdir()
    (lookup_dir / "genes.tsv").write_text(
        "PharmGKB Accession Id\tNCBI Gene ID\tHGNC ID\tName\tSymbol\t"
        "Alternate Names\tAlternate Symbols\n"
        "PA1\t1555\tHGNC:2615\tcytochrome P450 family 2 subfamily B member 6\t"
        "CYP2B6\t\t\n"
        "PA2\t282617\tHGNC:18365\tinterferon lambda 3\tIFNL3\t\tIL28B\n"
        "PA3\t3106\tHGNC:4932\tmajor histocompatibility complex class I B\t"
        "HLA-B\t\t\n",
        encoding="utf-8",
    )
    (lookup_dir / "variants.tsv").write_text(
        "Variant Name\tVariant ID\tSynonyms\n"
        "rs12345\tPAV1\t12345, NM_000001.1:c.10A>G\n",
        encoding="utf-8",
    )
    (lookup_dir / "snp_notation_mappings.json").write_text(
        json.dumps(
            {
                "mappings": [
                    {
                        "gene": "CYP2B6",
                        "notation": "10A>G",
                        "rsid": "rs12345",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    return GeneLookup(data_dir=tmp_path), VariantLookup(data_dir=tmp_path)


def test_gene_lookup_resolves_symbols_aliases_and_names(genetic_lookups):
    genes, _ = genetic_lookups

    assert genes.search("cyp2b6")[0].normalized_term == "CYP2B6"
    alias = genes.search("IL28B")[0]
    assert alias.normalized_term == "IFNL3"
    assert alias.id == "PA2"
    assert alias.match_type == "exact_alternate_symbol"
    assert genes.search("interferon lambda 3")[0].normalized_term == "IFNL3"


def test_deterministic_text_candidates_include_genes_and_variants(genetic_lookups):
    genes, variants = genetic_lookups
    text = "CYP2B6 10A>G and RS12345 were studied with HLA-B."

    result = normalize_genes_and_variants_in_text(
        text,
        gene_lookup=genes,
        variant_lookup=variants,
        use_api_fallback=False,
    )

    assert result["genes"]["CYP2B6"]["matches"][0]["id"] == "PA1"
    assert result["genes"]["HLA-B"]["matches"][0]["id"] == "PA3"
    assert result["variants"]["CYP2B6 10A>G"]["matches"][0]["id"] == "PAV1"
    assert result["variants"]["RS12345"]["matches"][0]["id"] == "PAV1"


def test_llm_reviews_mentions_and_lookup_supplies_ids(genetic_lookups):
    genes, variants = genetic_lookups
    abstract = "CYP2B6 10A>G was studied alongside IL28B and RS12345."

    result = normalize_genes_in_abstract(
        abstract,
        gene_lookup=genes,
        variant_lookup=variants,
        use_api_fallback=False,
        llm_callable=lambda **kwargs: json.dumps(
            {
                "gene_mentions": ["CYP2B6", "IL28B", "NOT_PRESENT"],
                "variant_mentions": ["CYP2B6 10A>G", "RS12345"],
            }
        ),
    )

    assert set(result["genes"]) == {"CYP2B6", "IL28B"}
    assert result["genes"]["IL28B"]["matches"][0]["normalized_term"] == "IFNL3"
    assert result["genes"]["IL28B"]["matches"][0]["id"] == "PA2"
    assert result["variants"]["CYP2B6 10A>G"]["matches"][0]["id"] == "PAV1"
    assert result["variants"]["RS12345"]["matches"][0]["id"] == "PAV1"


def test_llm_preserves_unresolved_literal_mentions(genetic_lookups):
    genes, variants = genetic_lookups
    abstract = "MYSTERY1 and GENE9*99 require follow-up."

    result = normalize_genes_in_abstract(
        abstract,
        gene_lookup=genes,
        variant_lookup=variants,
        use_api_fallback=False,
        llm_callable=lambda **kwargs: (
            '{"gene_mentions":["MYSTERY1"],'
            '"variant_mentions":["GENE9*99"]}'
        ),
    )

    assert result["genes"]["MYSTERY1"]["matches"] == []
    assert result["variants"]["GENE9*99"]["matches"] == []
    assert result["genes"]["MYSTERY1"]["spans"] == [[0, 8]]


def test_invalid_llm_output_falls_back_to_candidates(genetic_lookups):
    genes, variants = genetic_lookups
    result = normalize_genes_in_abstract(
        "CYP2B6 and rs12345 were studied.",
        gene_lookup=genes,
        variant_lookup=variants,
        llm_callable=lambda **kwargs: "not json",
    )

    assert "CYP2B6" in result["genes"]
    assert "rs12345" in result["variants"]


def test_empty_abstract_skips_llm(genetic_lookups):
    genes, variants = genetic_lookups
    called = False

    def fake_llm(**kwargs):
        nonlocal called
        called = True
        return '{"gene_mentions":[],"variant_mentions":[]}'

    assert normalize_genes_in_abstract(
        " ",
        gene_lookup=genes,
        variant_lookup=variants,
        llm_callable=fake_llm,
    ) == {"genes": {}, "variants": {}}
    assert not called
