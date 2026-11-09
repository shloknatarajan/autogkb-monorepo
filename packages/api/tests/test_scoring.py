from unittest.mock import patch, MagicMock
from src.scoring import classify_additional_labels, score_for_va, fetch_pubmed_abstract
from shared.triage_scoring import (
    _ADDITIONAL_CLASSIFIERS,
    _PRODUCTION_PROMPT_VERSION,
    _VA_TRIAGE_SYSTEM_CURRENT,
    _classification_response,
    _load_prompt_config,
)


def test_production_prompt_loads_from_yaml():
    config = _load_prompt_config()
    assert _PRODUCTION_PROMPT_VERSION == "v3"
    assert _VA_TRIAGE_SYSTEM_CURRENT == config["prompts"]["v3"]["system"].strip()
    assert "annotations_by_pmcid" not in _VA_TRIAGE_SYSTEM_CURRENT
    assert "ClinPGx-style variant annotation" not in _VA_TRIAGE_SYSTEM_CURRENT
    assert "clinical, biochemical, or functional phenotype" in _VA_TRIAGE_SYSTEM_CURRENT
    assert set(_ADDITIONAL_CLASSIFIERS) == {
        "paper_types",
        "pgx_information_types",
    }


def test_score_for_va_parses_relevant_response():
    with patch("shared.triage_scoring.call_llm") as mock_llm:
        mock_llm.return_value = '{"score": 85, "label": "relevant", "reasoning": "Reports CYP2C19 allele effect on clopidogrel efficacy."}'
        result = score_for_va(
            "41969197", "CYP2C19 Genotype and Clopidogrel", "Abstract...", "gpt-4o"
        )
        assert result["score"] == 85
        assert result["label"] == "relevant"
        assert "CYP2C19" in result["reasoning"]
        assert result["tags"] == []


def test_score_for_va_parses_additive_tags_and_enforces_hierarchy():
    with patch("shared.triage_scoring.call_llm") as mock_llm:
        mock_llm.return_value = (
            '{"score": 88, "label": "relevant", '
            '"tags": ["variant_annotations", "unknown"], '
            '"reasoning": "Specific allele-drug outcome."}'
        )
        result = score_for_va("12345", "Title", "Abstract", "gpt-4o")
        assert result["tags"] == [
            "variant_annotations",
            "general_pharmacogenomics",
        ]


def test_score_for_va_clamps_score_to_100():
    with patch("shared.triage_scoring.call_llm") as mock_llm:
        mock_llm.return_value = (
            '{"score": 150, "label": "relevant", "reasoning": "..."}'
        )
        result = score_for_va("12345", "Title", "Abstract", "gpt-4o")
        assert result["score"] == 100


def test_score_for_va_handles_malformed_response():
    with patch("shared.triage_scoring.call_llm") as mock_llm:
        mock_llm.return_value = "not json"
        result = score_for_va("12345", "Title", "Abstract", "gpt-4o")
        assert result["score"] == 0
        assert result["label"] == "not_relevant"
        assert result["tags"] == []
        assert "failed" in result["reasoning"].lower()


def test_score_for_va_derives_label_from_score_if_invalid():
    with patch("shared.triage_scoring.call_llm") as mock_llm:
        mock_llm.return_value = (
            '{"score": 80, "label": "unknown_label", "reasoning": "..."}'
        )
        result = score_for_va("12345", "Title", "Abstract", "gpt-4o")
        assert result["label"] == "relevant"  # 80 >= 75


def test_score_for_va_enforces_label_from_score_when_model_label_conflicts():
    with patch("shared.triage_scoring.call_llm") as mock_llm:
        mock_llm.return_value = (
            '{"score": 82, "label": "not_relevant", "reasoning": "..."}'
        )
        result = score_for_va("12345", "Title", "Abstract", "gpt-4o")
        assert result["label"] == "relevant"


def test_score_for_va_caps_title_only_record_at_borderline():
    with patch("shared.triage_scoring.call_llm") as mock_llm:
        mock_llm.return_value = (
            '{"score": 90, "label": "relevant", "reasoning": "Explicit title"}'
        )
        result = score_for_va("12345", "UGT1A1 and irinotecan toxicity", None, "gpt-4o")
        assert result["score"] == 74
        assert result["label"] == "borderline"


def test_score_for_va_skips_model_when_pubmed_has_no_text():
    with patch("shared.triage_scoring.call_llm") as mock_llm:
        result = score_for_va("12345", None, None, "gpt-4o")
        mock_llm.assert_not_called()
        assert result["score"] == 0
        assert result["label"] == "not_relevant"
        assert result["tags"] == []
        assert "No title or abstract" in result["reasoning"]


def test_classification_response_filters_unknowns_deduplicates_and_drops_other():
    result = _classification_response(
        '{"pgx_information_types": ["other", "efficacy", "efficacy", "unknown"], "reasoning": "Clinical response."}',
        "pgx_information_types",
        ["pd", "dosage", "efficacy", "toxicity", "other", "metabolism_pk"],
    )
    assert result["pgx_information_types"] == ["efficacy"]
    assert result["reasoning"] == "Clinical response."


def test_classify_additional_labels_runs_both_self_contained_classifiers():
    with patch("shared.triage_scoring.call_llm") as mock_llm:
        mock_llm.side_effect = [
            '{"paper_types": ["methods", "unknown"], "reasoning": "Validates an assay."}',
            '{"pgx_information_types": ["pd", "efficacy"], "reasoning": "Reports biomarker and clinical response."}',
        ]
        result = classify_additional_labels(
            "12345", "PGx assay and response", "Abstract", "gpt-4o"
        )

    assert result == {
        "paper_types": ["methods"],
        "paper_type_reasoning": "Validates an assay.",
        "pgx_information_types": ["pd", "efficacy"],
        "pgx_information_reasoning": "Reports biomarker and clinical response.",
    }
    assert mock_llm.call_count == 2
    for call_args in mock_llm.call_args_list:
        assert "PMID: 12345" in call_args.args[2]
        assert "data/" not in call_args.args[1]


def test_classify_additional_labels_isolates_classifier_failures():
    with patch("shared.triage_scoring.call_llm") as mock_llm:
        mock_llm.side_effect = [
            "not json",
            '{"pgx_information_types": ["metabolism_pk"], "reasoning": "Reports clearance."}',
        ]
        result = classify_additional_labels("12345", "Title", "Abstract", "gpt-4o")

    assert result["paper_types"] == []
    assert "failed" in result["paper_type_reasoning"].lower()
    assert result["pgx_information_types"] == ["metabolism_pk"]


def test_classify_additional_labels_skips_model_without_text():
    with patch("shared.triage_scoring.call_llm") as mock_llm:
        result = classify_additional_labels("12345", None, None, "gpt-4o")
    mock_llm.assert_not_called()
    assert result["paper_types"] == []
    assert result["pgx_information_types"] == []


def test_fetch_pubmed_abstract_parses_xml():
    xml_text = """<PubmedArticle>
        <MedlineCitation>
            <Article>
                <ArticleTitle>CYP2C19 Genotype Study</ArticleTitle>
                <Abstract>
                    <AbstractText>Background: We studied...</AbstractText>
                    <AbstractText Label="Methods">Methods text...</AbstractText>
                </Abstract>
            </Article>
        </MedlineCitation>
    </PubmedArticle>"""
    with patch("shared.triage_scoring.requests.get") as mock_get:
        mock_resp = MagicMock()
        mock_resp.text = xml_text
        mock_get.return_value = mock_resp
        result = fetch_pubmed_abstract("41969197", "test@example.com")
        assert result["title"] == "CYP2C19 Genotype Study"
        assert "We studied" in result["abstract"]
        assert result["error"] is None
