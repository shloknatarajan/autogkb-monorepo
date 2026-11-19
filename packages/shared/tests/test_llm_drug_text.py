from pathlib import Path

import pytest

from shared.term_normalization.drugs import DrugLookup, normalize_drugs_in_abstract


@pytest.fixture()
def llm_lookup(tmp_path: Path) -> DrugLookup:
    lookup_dir = tmp_path / "term_lookup_info"
    lookup_dir.mkdir()
    (lookup_dir / "drugs.tsv").write_text(
        "PharmGKB Accession Id\tName\tGeneric Names\tTrade Names\t"
        "RxNorm Identifiers\n"
        "PA1\twarfarin\tWarfarin sodium\tCoumadin\t11289\n"
        "PA2\tacetaminophen\tParacetamol\tTylenol\t161\n"
        "PA3\ts-adenosylmethionine\tSAM-e, same\t\t9504\n",
        encoding="utf-8",
    )
    return DrugLookup(data_dir=tmp_path)


def test_llm_reviews_candidates_and_lookup_supplies_ids(llm_lookup: DrugLookup):
    captured = {}

    def fake_llm(**kwargs):
        captured.update(kwargs)
        return '{"drug_mentions": ["Coumadin", "warfrin"]}'

    abstract = "The same result occurred with Coumadin and later warfrin treatment."
    result = normalize_drugs_in_abstract(
        abstract,
        lookup=llm_lookup,
        llm_callable=fake_llm,
        use_rxnorm_fallback=False,
    )

    assert "same" not in result
    assert result["Coumadin"]["matches"][0]["id"] == "PA1"
    assert result["Coumadin"]["matches"][0]["source"] == "pharmgkb"
    assert result["warfrin"]["matches"][0]["id"] == "PA1"
    assert result["warfrin"]["matches"][0]["match_type"] == "fuzzy_canonical"
    assert "same" in captured["user_prompt"]
    assert captured["temperature"] == 0


def test_llm_hallucinated_mention_is_ignored(llm_lookup: DrugLookup):
    result = normalize_drugs_in_abstract(
        "Warfarin was administered.",
        lookup=llm_lookup,
        llm_callable=lambda **kwargs: '{"drug_mentions": ["aspirin", "Warfarin"]}',
        use_rxnorm_fallback=False,
    )

    assert list(result) == ["Warfarin"]
    assert result["Warfarin"]["matches"][0]["id"] == "PA1"


def test_valid_unresolved_mention_is_preserved(llm_lookup: DrugLookup):
    abstract = "The active metabolite hydroxybupropion was measured."
    result = normalize_drugs_in_abstract(
        abstract,
        lookup=llm_lookup,
        llm_callable=lambda **kwargs: (
            '{"drug_mentions": ["hydroxybupropion"]}'
        ),
        use_rxnorm_fallback=False,
    )

    assert result["hydroxybupropion"] == {
        "count": 1,
        "spans": [[22, 38]],
        "ambiguous": False,
        "matches": [],
    }


def test_non_english_mentions_are_ignored_but_greek_notation_is_preserved(
    llm_lookup: DrugLookup,
):
    abstract = (
        "The drugs bedaquiline and Δ9-THC were measured. "
        "Концентрации бедаквилина измерялись."
    )
    result = normalize_drugs_in_abstract(
        abstract,
        lookup=llm_lookup,
        llm_callable=lambda **kwargs: (
            '{"drug_mentions": ["bedaquiline", "Δ9-THC", "бедаквилина"]}'
        ),
        use_rxnorm_fallback=False,
    )

    assert set(result) == {"bedaquiline", "Δ9-THC"}
    assert result["bedaquiline"]["matches"] == []
    assert result["Δ9-THC"]["matches"] == []


def test_llm_mentions_preserve_actual_case_counts_and_spans(llm_lookup: DrugLookup):
    abstract = "Tylenol was stopped; later TYLENOL was resumed."
    result = normalize_drugs_in_abstract(
        abstract,
        lookup=llm_lookup,
        llm_callable=lambda **kwargs: '{"drug_mentions": ["Tylenol"]}',
    )

    assert result["Tylenol"]["count"] == 1
    assert result["TYLENOL"]["count"] == 1
    assert result["Tylenol"]["spans"] == [[0, 7]]
    assert result["TYLENOL"]["spans"] == [[27, 34]]


def test_invalid_llm_json_falls_back_to_deterministic_candidates(
    llm_lookup: DrugLookup,
):
    result = normalize_drugs_in_abstract(
        "Coumadin was administered.",
        lookup=llm_lookup,
        llm_callable=lambda **kwargs: "not json",
    )

    assert result["Coumadin"]["matches"][0]["id"] == "PA1"


def test_invalid_llm_json_can_raise(llm_lookup: DrugLookup):
    with pytest.raises(ValueError, match="JSON object"):
        normalize_drugs_in_abstract(
            "Coumadin was administered.",
            lookup=llm_lookup,
            llm_callable=lambda **kwargs: "not json",
            fallback_to_deterministic=False,
        )


def test_empty_abstract_skips_llm(llm_lookup: DrugLookup):
    called = False

    def fake_llm(**kwargs):
        nonlocal called
        called = True
        return '{"drug_mentions": []}'

    assert (
        normalize_drugs_in_abstract(
            "   ",
            lookup=llm_lookup,
            llm_callable=fake_llm,
        )
        == {}
    )
    assert not called


def test_invalid_max_candidates_raises(llm_lookup: DrugLookup):
    with pytest.raises(ValueError, match="max_candidates"):
        normalize_drugs_in_abstract(
            "warfarin",
            lookup=llm_lookup,
            llm_callable=lambda **kwargs: '{"drug_mentions": []}',
            max_candidates=0,
        )
