from pathlib import Path

import pytest

from shared.term_normalization.drugs import DrugLookup, normalize_drugs_in_text


@pytest.fixture()
def text_lookup(tmp_path: Path) -> DrugLookup:
    lookup_dir = tmp_path / "term_lookup_info"
    lookup_dir.mkdir()
    (lookup_dir / "drugs.tsv").write_text(
        "PharmGKB Accession Id\tName\tGeneric Names\tTrade Names\t"
        "RxNorm Identifiers\n"
        "PA1\twarfarin\tWarfarin sodium\tCoumadin, SharedBrand\t11289\n"
        "PA2\tacetaminophen\tAPAP, Paracetamol\tTylenol, SharedBrand\t161\n"
        "PA3\tacetaminophen / codeine\t\tCoPain\t817579\n",
        encoding="utf-8",
    )
    return DrugLookup(data_dir=tmp_path)


def test_maps_original_mentions_to_normalized_terms_and_ids(text_lookup: DrugLookup):
    text = "Patients received Coumadin and Tylenol."

    result = normalize_drugs_in_text(text, lookup=text_lookup)

    assert result["Coumadin"]["matches"][0]["normalized_term"] == "warfarin"
    assert result["Coumadin"]["matches"][0]["id"] == "PA1"
    assert result["Coumadin"]["matches"][0]["source"] == "pharmgkb"
    assert result["Tylenol"]["matches"][0]["normalized_term"] == "acetaminophen"
    assert result["Tylenol"]["matches"][0]["id"] == "PA2"


def test_preserves_original_case_counts_and_character_spans(text_lookup: DrugLookup):
    text = "Coumadin was replaced. Later, Coumadin was resumed."

    result = normalize_drugs_in_text(text, lookup=text_lookup)

    assert result["Coumadin"]["count"] == 2
    assert result["Coumadin"]["spans"] == [[0, 8], [30, 38]]
    assert all(
        text[start:end] == "Coumadin"
        for start, end in result["Coumadin"]["spans"]
    )


def test_prefers_longest_combination_drug_match(text_lookup: DrugLookup):
    result = normalize_drugs_in_text(
        "The acetaminophen/codeine combination was withheld.",
        lookup=text_lookup,
    )

    assert list(result) == ["acetaminophen/codeine"]
    assert result["acetaminophen/codeine"]["matches"][0]["id"] == "PA3"


def test_generic_multiword_alias_is_preserved(text_lookup: DrugLookup):
    result = normalize_drugs_in_text(
        "Warfarin sodium 5 mg was administered.",
        lookup=text_lookup,
    )

    assert result["Warfarin sodium"]["matches"][0]["id"] == "PA1"
    assert result["Warfarin sodium"]["matches"][0]["match_type"] == "exact_generic"


def test_shared_alias_returns_all_candidates_as_ambiguous(text_lookup: DrugLookup):
    result = normalize_drugs_in_text("SharedBrand was given.", lookup=text_lookup)

    assert result["SharedBrand"]["ambiguous"]
    assert {match["id"] for match in result["SharedBrand"]["matches"]} == {
        "PA1",
        "PA2",
    }


def test_trade_names_can_be_excluded(text_lookup: DrugLookup):
    result = normalize_drugs_in_text(
        "Coumadin and warfarin were compared.",
        lookup=text_lookup,
        include_trade_names=False,
    )

    assert "Coumadin" not in result
    assert result["warfarin"]["matches"][0]["id"] == "PA1"


def test_does_not_join_combination_alias_across_sentences(text_lookup: DrugLookup):
    result = normalize_drugs_in_text(
        "Acetaminophen was stopped. Codeine was continued.",
        lookup=text_lookup,
    )

    assert "Acetaminophen was stopped. Codeine" not in result
    assert result["Acetaminophen"]["matches"][0]["id"] == "PA2"


def test_contextual_abbreviations_are_mapped_without_fuzzy_prose_search(
    text_lookup: DrugLookup,
):
    result = normalize_drugs_in_text(
        "Warfarin 400 mg (WRF400) was compared with WRF600 and WRF.",
        lookup=text_lookup,
    )

    assert result["WRF400"]["matches"][0]["id"] == "PA1"
    assert result["WRF600"]["matches"][0]["id"] == "PA1"
    assert result["WRF"]["matches"][0]["match_type"] == "contextual_abbreviation"


def test_numeric_only_aliases_are_not_extracted(tmp_path: Path):
    lookup_dir = tmp_path / "term_lookup_info"
    lookup_dir.mkdir()
    (lookup_dir / "drugs.tsv").write_text(
        "PharmGKB Accession Id\tName\tGeneric Names\tTrade Names\t"
        "RxNorm Identifiers\n"
        "PA4\tcompound\t2-[3]\t\t123\n",
        encoding="utf-8",
    )

    assert normalize_drugs_in_text("CYP2C19 *2/*3", data_dir=tmp_path) == {}


def test_empty_text_returns_empty_dictionary(text_lookup: DrugLookup):
    assert normalize_drugs_in_text("   ", lookup=text_lookup) == {}


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"max_candidates": 0}, "max_candidates"),
        ({"min_single_token_length": 0}, "min_single_token_length"),
    ],
)
def test_invalid_options_raise(
    text_lookup: DrugLookup,
    kwargs: dict,
    message: str,
):
    with pytest.raises(ValueError, match=message):
        normalize_drugs_in_text("warfarin", lookup=text_lookup, **kwargs)
