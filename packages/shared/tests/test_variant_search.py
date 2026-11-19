import json
from pathlib import Path
from unittest.mock import Mock, patch

import pytest
import requests

from shared.term_normalization.genes import (
    VariantLookup,
    classify_variant_term,
    clear_variant_index_cache,
    normalize_hla_allele,
    normalize_star_allele,
)


@pytest.fixture()
def variant_lookup(tmp_path: Path) -> VariantLookup:
    lookup_dir = tmp_path / "term_lookup_info"
    lookup_dir.mkdir()
    (lookup_dir / "variants.tsv").write_text(
        "Variant Name\tVariant ID\tSynonyms\n"
        "rs12345\tPA1\t12345, NM_000001.1:c.10A>G, rs12345\n"
        "rs99999\tPA2\t99999, shared-alias\n"
        "rs88888\tPA3\t88888, shared-alias\n",
        encoding="utf-8",
    )
    (lookup_dir / "snp_notation_mappings.json").write_text(
        json.dumps(
            {
                "mappings": [
                    {
                        "gene": "GENE1",
                        "notation": "10A>G",
                        "rsid": "rs12345",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    clear_variant_index_cache()
    return VariantLookup(data_dir=tmp_path)


def test_notation_classification_keeps_hla_and_star_alleles_distinct():
    assert classify_variant_term("RS12345") == "rsid"
    assert classify_variant_term("HLA-B*5801") == "hla"
    assert classify_variant_term("B*58:01") == "hla"
    assert classify_variant_term("CYP2D6*41") == "star_allele"
    assert normalize_hla_allele("HLA-B*5801") == "HLA-B*58:01"
    assert normalize_star_allele("cyp2d6 * 41") == "CYP2D6*41"


def test_uppercase_rsid_uses_cached_exact_local_lookup(variant_lookup: VariantLookup):
    with patch(
        "shared.term_normalization.genes.variant_search.requests.get"
    ) as request:
        results = variant_lookup.search("RS12345")

    request.assert_not_called()
    assert len(results) == 1
    assert results[0].id == "PA1"
    assert results[0].normalized_term == "rs12345"
    assert results[0].match_type == "exact_canonical"
    assert results[0].entity_type == "variant"


def test_exact_hgvs_synonym_resolves_without_fuzzy_matching(
    variant_lookup: VariantLookup,
):
    result = variant_lookup.search(
        "NM_000001.1:c.10A>G",
        use_api_fallback=False,
    )[0]

    assert result.id == "PA1"
    assert result.normalized_term == "rs12345"
    assert result.match_type == "exact_synonym"


def test_shared_alias_returns_unique_ambiguous_records(
    variant_lookup: VariantLookup,
):
    results = variant_lookup.search(
        "shared-alias",
        top_k=5,
        use_api_fallback=False,
    )

    assert {result.id for result in results} == {"PA2", "PA3"}
    assert all(result.ambiguous for result in results)


def test_gene_relative_shorthand_maps_to_grounded_rsid(
    variant_lookup: VariantLookup,
):
    result = variant_lookup.search(
        "GENE1 10A>G",
        use_api_fallback=False,
    )[0]

    assert result.id == "PA1"
    assert result.normalized_term == "rs12345"
    assert result.match_type == "shorthand_to_rsid"


@pytest.mark.parametrize(
    ("term", "api_symbol", "entity_type"),
    [
        ("CYP2D6 * 41", "CYP2D6*41", "haplotype"),
        ("HLA-B*5801", "HLA-B*58:01", "hla_allele"),
    ],
)
def test_alleles_use_guarded_haplotype_lookup(
    variant_lookup: VariantLookup,
    term: str,
    api_symbol: str,
    entity_type: str,
):
    response = Mock()
    response.json.return_value = {
        "data": [{"id": "PAH1", "symbol": api_symbol}]
    }
    with patch(
        "shared.term_normalization.genes.variant_search.requests.get",
        return_value=response,
    ) as request:
        result = variant_lookup.search(term)[0]

    request.assert_called_once_with(
        "https://api.pharmgkb.org/v1/data/haplotype",
        params={"symbol": api_symbol},
        timeout=10,
    )
    response.raise_for_status.assert_called_once()
    assert result.id == "PAH1"
    assert result.normalized_term == api_symbol
    assert result.entity_type == entity_type
    assert result.match_type == "exact_api"


def test_api_does_not_fuzzy_match_a_different_star_allele(
    variant_lookup: VariantLookup,
):
    response = Mock()
    response.json.return_value = {
        "data": [{"id": "WRONG", "symbol": "CYP2D6*41"}]
    }
    with patch(
        "shared.term_normalization.genes.variant_search.requests.get",
        return_value=response,
    ):
        assert variant_lookup.search("CYP2D6*4") == []


def test_api_errors_and_malformed_payloads_return_no_match(
    variant_lookup: VariantLookup,
):
    with patch(
        "shared.term_normalization.genes.variant_search.requests.get",
        side_effect=requests.RequestException("offline"),
    ):
        assert variant_lookup.search("CYP2D6*4") == []

    response = Mock()
    response.json.return_value = {"data": "unexpected"}
    with patch(
        "shared.term_normalization.genes.variant_search.requests.get",
        return_value=response,
    ):
        assert variant_lookup.search("HLA-B*58:01") == []


def test_search_validates_options(variant_lookup: VariantLookup):
    with pytest.raises(ValueError, match="threshold"):
        variant_lookup.search("rs12345", threshold=2)
    with pytest.raises(ValueError, match="top_k"):
        variant_lookup.search("rs12345", top_k=0)
