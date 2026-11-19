from pathlib import Path

import pytest

from shared.term_normalization.drugs import (
    DrugLookup,
    clear_drug_index_cache,
    normalize_drug_text,
)


@pytest.fixture()
def drug_data_dir(tmp_path: Path) -> Path:
    lookup_dir = tmp_path / "term_lookup_info"
    lookup_dir.mkdir()
    (lookup_dir / "drugs.tsv").write_text(
        "\t".join(
            [
                "PharmGKB Accession Id",
                "Name",
                "Generic Names",
                "Trade Names",
                "RxNorm Identifiers",
            ]
        )
        + "\n"
        + "PA1\twarfarin\tWarfarin sodium\tCoumadin, SharedBrand\t11289\n"
        + "PA2\tacetaminophen\tAPAP, Paracetamol\tTylenol, SharedBrand\t161, 999\n"
        + "PA3\tacetaminophen / codeine\t\tCoPain\t817579\n"
        + "PA4\tclopidogrel\t\tPlavix\t32968\n"
        + "PA5\tclopidogril\t\t\t99999\n",
        encoding="utf-8",
    )
    clear_drug_index_cache()
    yield tmp_path
    clear_drug_index_cache()


def test_normalize_drug_text_preserves_ingredients_and_can_strip_formulation():
    assert normalize_drug_text("Acetaminophen / Codeine") == "acetaminophen codeine"
    assert (
        normalize_drug_text(
            "Warfarin Sodium 5 mg oral tablet",
            strip_formulation=True,
        )
        == "warfarin sodium"
    )


def test_exact_canonical_match(drug_data_dir: Path):
    result = DrugLookup(data_dir=drug_data_dir).search(
        "WARFARIN", use_rxnorm_fallback=False
    )[0]

    assert result.id == "PA1"
    assert result.normalized_term == "warfarin"
    assert result.score == 1.0
    assert result.match_type == "exact_canonical"
    assert result.source == "pharmgkb"
    assert not result.ambiguous


def test_exact_trade_name_precedes_fuzzy_matching(drug_data_dir: Path):
    result = DrugLookup(data_dir=drug_data_dir).search(
        "Coumadin", use_rxnorm_fallback=False
    )[0]

    assert result.id == "PA1"
    assert result.matched_term == "Coumadin"
    assert result.match_type == "exact_trade"


def test_strength_and_dosage_form_are_removed_before_exact_alias_match(
    drug_data_dir: Path,
):
    result = DrugLookup(data_dir=drug_data_dir).search(
        "Warfarin sodium 5 mg oral tablet",
        use_rxnorm_fallback=False,
    )[0]

    assert result.id == "PA1"
    assert result.match_type == "exact_generic"
    assert result.score == 1.0


def test_fuzzy_alias_match_handles_typo(drug_data_dir: Path):
    result = DrugLookup(data_dir=drug_data_dir).search(
        "Coumadn", use_rxnorm_fallback=False
    )[0]

    assert result.id == "PA1"
    assert result.match_type == "fuzzy_trade"
    assert 0.8 <= result.score < 1.0


def test_combination_drug_matches_as_a_single_canonical_record(drug_data_dir: Path):
    result = DrugLookup(data_dir=drug_data_dir).search(
        "acetaminophen/codeine", use_rxnorm_fallback=False
    )[0]

    assert result.id == "PA3"
    assert result.match_type == "exact_canonical"


def test_shared_exact_alias_is_marked_ambiguous(drug_data_dir: Path):
    results = DrugLookup(data_dir=drug_data_dir).search(
        "SharedBrand", top_k=2, use_rxnorm_fallback=False
    )

    assert {result.id for result in results} == {"PA1", "PA2"}
    assert all(result.ambiguous for result in results)


def test_top_k_is_enforced_and_close_fuzzy_results_are_ambiguous(
    drug_data_dir: Path,
):
    results = DrugLookup(data_dir=drug_data_dir).search(
        "clopidogrl",
        threshold=0.7,
        top_k=2,
        ambiguity_margin=0.1,
        use_rxnorm_fallback=False,
    )

    assert len(results) == 2
    assert results[0].score >= results[1].score
    assert all(result.ambiguous for result in results)


def test_low_confidence_text_abstains_without_network_fallback(drug_data_dir: Path):
    assert (
        DrugLookup(data_dir=drug_data_dir).search(
            "completely unrelated phrase",
            use_rxnorm_fallback=False,
        )
        == []
    )


def test_short_strings_only_use_exact_aliases(drug_data_dir: Path):
    lookup = DrugLookup(data_dir=drug_data_dir)

    assert lookup.search("APAP", use_rxnorm_fallback=False)[0].id == "PA2"
    assert lookup.search("APA", use_rxnorm_fallback=False) == []


def test_rxcui_mapping_is_exact_not_fuzzy(drug_data_dir: Path):
    lookup = DrugLookup(data_dir=drug_data_dir)

    assert lookup.rxcui_to_pa_id("RXN11289")[0].id == "PA1"
    assert lookup.rxcui_to_pa_id("1128") == []
    assert lookup.rxcui_to_pa_id("not-an-id") == []


def test_quoted_aliases_with_internal_commas_are_not_split(tmp_path: Path):
    lookup_dir = tmp_path / "term_lookup_info"
    lookup_dir.mkdir()
    (lookup_dir / "drugs.tsv").write_text(
        "PharmGKB Accession Id\tName\tGeneric Names\tTrade Names\t"
        "RxNorm Identifiers\n"
        'PA6\tinsulin-aspart\t"""aspart"", ""insulin, asp(b28)"""\t\t\n',
        encoding="utf-8",
    )
    lookup = DrugLookup(data_dir=tmp_path)

    assert lookup.search("insulin", use_rxnorm_fallback=False) == []
    assert (
        lookup.search("insulin, asp(b28)", use_rxnorm_fallback=False)[0].id
        == "PA6"
    )


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"threshold": 1.1}, "threshold"),
        ({"top_k": 0}, "top_k"),
        ({"ambiguity_margin": -0.1}, "ambiguity_margin"),
    ],
)
def test_invalid_search_parameters_raise(
    drug_data_dir: Path,
    kwargs: dict,
    message: str,
):
    with pytest.raises(ValueError, match=message):
        DrugLookup(data_dir=drug_data_dir).search(
            "warfarin",
            use_rxnorm_fallback=False,
            **kwargs,
        )
