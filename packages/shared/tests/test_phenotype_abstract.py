import json

import pytest

from shared.term_normalization.phenotypes import (
    PhenotypeSearchResult,
    normalize_phenotypes_in_abstract,
)


class FakeLookup:
    def __init__(self, results=None):
        self.results = results or {}
        self.queries = []

    def search(self, term, *, top_k=5, min_score=0.5):
        self.queries.append((term, top_k, min_score))
        return self.results.get(term, [])[:top_k]


def candidate(identifier: str, name: str, score: float):
    return PhenotypeSearchResult(
        raw_input="neutropenia",
        normalized_term=name,
        id=identifier,
        url=f"https://www.clinpgx.org/phenotype/{identifier}",
        source="clinpgx",
        match_type="fuzzy_canonical",
        score=score,
        matched_term=name,
        external_vocabulary="MeSH:D009503",
    )


def test_llm_extracts_frame_then_selects_only_offered_candidate():
    abstract = (
        "UGT1A1 variants were associated with increased risk of severe neutropenia "
        "during irinotecan treatment."
    )
    lookup = FakeLookup(
        {
            "neutropenia": [
                candidate("PA1", "Neutropenia", 1.0),
                candidate("PA2", "Febrile neutropenia", 0.82),
            ]
        }
    )
    calls = []

    def fake_llm(**kwargs):
        calls.append(kwargs)
        if "extract structured" in kwargs["system_prompt"]:
            return json.dumps(
                {
                    "phenotypes": [
                        {
                            "mention": "increased risk of severe neutropenia",
                            "core_phenotype": "neutropenia",
                            "search_query": "neutropenia",
                            "category": "toxicity",
                            "direction": "increased",
                            "severity": "severe",
                            "drug_mentions": ["irinotecan"],
                            "gene_mentions": ["UGT1A1"],
                            "evidence_text": abstract,
                        }
                    ]
                }
            )
        return '{"selections":[{"frame_index":0,"candidate_id":"PA1"}]}'

    frames = normalize_phenotypes_in_abstract(
        abstract,
        drug_tags=[
            {
                "name": "irinotecan",
                "id": "PA450085",
                "source": "pharmgkb",
                "mentions": ["irinotecan"],
                "resolved": True,
            }
        ],
        gene_tags=[
            {
                "name": "UGT1A1",
                "id": "PA420",
                "source": "pharmgkb",
                "mentions": ["UGT1A1"],
                "resolved": True,
            }
        ],
        lookup=lookup,
        llm_callable=fake_llm,
    )

    assert len(calls) == 2
    assert lookup.queries == [("neutropenia", 5, 0.5)]
    assert frames[0]["search_query"] == "neutropenia"
    assert frames[0]["normalized_term"] == "Neutropenia"
    assert frames[0]["id"] == "PA1"
    assert frames[0]["resolved"] is True
    assert frames[0]["direction"] == "increased"
    assert frames[0]["severity"] == "severe"
    assert frames[0]["drugs"][0]["id"] == "PA450085"
    assert frames[0]["genes"][0]["id"] == "PA420"
    assert len(frames[0]["candidates"]) == 2


def test_model_cannot_select_candidate_that_was_not_offered():
    abstract = "Severe neutropenia occurred."
    lookup = FakeLookup({"neutropenia": [candidate("PA1", "Neutropenia", 1.0)]})

    responses = iter(
        [
            json.dumps(
                {
                    "phenotypes": [
                        {
                            "mention": "Severe neutropenia",
                            "core_phenotype": "neutropenia",
                            "category": "toxicity",
                            "direction": "not_stated",
                            "severity": "severe",
                            "drug_mentions": [],
                            "gene_mentions": [],
                            "evidence_text": abstract,
                        }
                    ]
                }
            ),
            '{"selections":[{"frame_index":0,"candidate_id":"INVENTED"}]}',
        ]
    )
    frames = normalize_phenotypes_in_abstract(
        abstract,
        lookup=lookup,
        llm_callable=lambda **kwargs: next(responses),
    )

    assert frames[0]["resolved"] is False
    assert frames[0]["id"] is None


def test_unmatched_study_outcome_is_preserved_without_forced_mapping():
    abstract = "Endoxifen levels were lower in CYP2D6 poor metabolizers."
    lookup = FakeLookup()
    response = json.dumps(
        {
            "phenotypes": [
                {
                    "mention": "Endoxifen levels were lower",
                    "core_phenotype": "Endoxifen levels",
                    "category": "metabolism_pk",
                    "direction": "decreased",
                    "severity": "not_stated",
                    "drug_mentions": [],
                    "gene_mentions": ["CYP2D6"],
                    "evidence_text": abstract,
                }
            ]
        }
    )

    frames = normalize_phenotypes_in_abstract(
        abstract,
        gene_tags=[{"name": "CYP2D6", "mentions": ["CYP2D6"]}],
        lookup=lookup,
        llm_callable=lambda **kwargs: response,
    )

    assert frames[0]["mention"] == "Endoxifen levels were lower"
    assert frames[0]["resolved"] is False
    assert frames[0]["candidates"] == []


def test_functional_phenotype_requires_and_retains_gene_context():
    abstract = "CYP2C19 poor metabolizers had higher exposure."
    response = json.dumps(
        {
            "phenotypes": [
                {
                    "mention": "poor metabolizers",
                    "core_phenotype": "poor metabolizers",
                    "category": "functional_phenotype",
                    "direction": "not_stated",
                    "severity": "not_stated",
                    "drug_mentions": [],
                    "gene_mentions": ["CYP2C19"],
                    "evidence_text": abstract,
                }
            ]
        }
    )
    lookup = FakeLookup()
    frames = normalize_phenotypes_in_abstract(
        abstract,
        gene_tags=[
            {
                "name": "CYP2C19",
                "id": "PA124",
                "mentions": ["CYP2C19"],
                "resolved": True,
            }
        ],
        lookup=lookup,
        llm_callable=lambda **kwargs: response,
    )

    assert frames[0]["genes"][0]["name"] == "CYP2C19"
    assert frames[0]["normalized_term"] == "Poor Metabolizer"
    assert frames[0]["source"] == "cpic"
    assert frames[0]["resolved"] is True
    assert lookup.queries == []


def test_unrecognized_functional_measurement_is_not_sent_to_general_lookup():
    abstract = "Inflammation was associated with decreased CYP2C19 activity."
    response = json.dumps(
        {
            "phenotypes": [
                {
                    "mention": "decreased CYP2C19 activity",
                    "core_phenotype": "CYP2C19 activity",
                    "search_query": "enzyme activity",
                    "category": "functional_phenotype",
                    "direction": "decreased",
                    "severity": "not_stated",
                    "drug_mentions": [],
                    "gene_mentions": ["CYP2C19"],
                    "evidence_text": abstract,
                }
            ]
        }
    )
    lookup = FakeLookup(
        {"enzyme activity": [candidate("WRONG", "Hypokinesia", 0.8)]}
    )

    frames = normalize_phenotypes_in_abstract(
        abstract,
        gene_tags=[
            {
                "name": "CYP2C19",
                "id": "PA124",
                "mentions": ["CYP2C19"],
                "resolved": True,
            }
        ],
        lookup=lookup,
        llm_callable=lambda **kwargs: response,
    )

    assert frames[0]["resolved"] is False
    assert frames[0]["candidates"] == []
    assert lookup.queries == []


def test_nonliteral_and_non_english_frames_are_rejected():
    abstract = "Warfarin dose was lower. Доза варфарина была ниже."
    response = json.dumps(
        {
            "phenotypes": [
                {
                    "mention": "invented bleeding risk",
                    "core_phenotype": "bleeding",
                    "category": "toxicity",
                    "direction": "increased",
                    "severity": "not_stated",
                    "drug_mentions": [],
                    "gene_mentions": [],
                    "evidence_text": abstract,
                },
                {
                    "mention": "Доза варфарина была ниже",
                    "core_phenotype": "Доза",
                    "category": "dosage",
                    "direction": "decreased",
                    "severity": "not_stated",
                    "drug_mentions": [],
                    "gene_mentions": [],
                    "evidence_text": "Доза варфарина была ниже.",
                },
            ]
        }
    )

    assert normalize_phenotypes_in_abstract(
        abstract,
        lookup=FakeLookup(),
        llm_callable=lambda **kwargs: response,
    ) == []


def test_invalid_json_returns_empty_or_can_raise():
    assert normalize_phenotypes_in_abstract(
        "A phenotype was measured.",
        lookup=FakeLookup(),
        llm_callable=lambda **kwargs: "not json",
    ) == []

    with pytest.raises(ValueError, match="JSON object"):
        normalize_phenotypes_in_abstract(
            "A phenotype was measured.",
            lookup=FakeLookup(),
            llm_callable=lambda **kwargs: "not json",
            fallback_to_empty=False,
        )
