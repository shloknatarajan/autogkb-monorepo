from pathlib import Path

import pytest

from shared.term_normalization.phenotypes import (
    PhenotypeLookup,
    normalize_functional_phenotype,
    search_phenotypes,
)


@pytest.fixture()
def phenotype_data(tmp_path: Path) -> Path:
    table_dir = tmp_path / "term_lookup_info"
    table_dir.mkdir()
    (table_dir / "phenotypes.tsv").write_text(
        "PharmGKB Accession Id\tName\tAlternate Names\tExternal Vocabulary\n"
        'PA1\tAbdominal Pain\t"Pain, Abdominal"\tMeSH:D015746\n'
        'PA2\tAbortion, Spontaneous\t"Miscarriage"\tMeSH:D000022\n'
        "PA3\tNeutropenia\tNeutropenias\tMeSH:D009503\n"
        "PA4\tFebrile neutropenia\t\tMedDRA:10016288\n"
        "PA5\tStevens-Johnson Syndrome\tSJS\tMeSH:D013262\n",
        encoding="utf-8",
    )
    return tmp_path


def test_exact_canonical_and_alias_matches(phenotype_data: Path):
    lookup = PhenotypeLookup(phenotype_data)

    canonical = lookup.search("abdominal pain")
    alias = lookup.search("miscarriage")

    assert canonical[0].id == "PA1"
    assert canonical[0].match_type == "exact_canonical"
    assert alias[0].normalized_term == "Abortion, Spontaneous"
    assert alias[0].match_type == "exact_alias"


def test_ranked_search_returns_official_top_candidates(phenotype_data: Path):
    results = search_phenotypes(
        "severe neutropenia",
        data_dir=phenotype_data,
        top_k=5,
    )

    assert [result.id for result in results[:2]] == ["PA3", "PA4"]
    assert all(result.source == "clinpgx" for result in results)
    assert all(result.url and result.id in result.url for result in results)


def test_search_rejects_invalid_top_k(phenotype_data: Path):
    with pytest.raises(ValueError, match="top_k"):
        search_phenotypes("pain", data_dir=phenotype_data, top_k=0)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("PM", "Poor Metabolizer"),
        ("extensive metaboliser", "Normal Metabolizer"),
        ("Likely Intermediate Metabolizer", "Likely Intermediate Metabolizer"),
    ],
)
def test_functional_phenotype_vocabulary(raw: str, expected: str):
    result = normalize_functional_phenotype(raw)

    assert result is not None
    assert result.normalized_term == expected
    assert result.source == "cpic"
