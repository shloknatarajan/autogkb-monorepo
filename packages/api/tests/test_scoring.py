from unittest.mock import patch, MagicMock
from src.scoring import score_for_va, fetch_pubmed_abstract
from shared.triage_scoring import (
    _PRODUCTION_PROMPT_VERSION,
    _VA_TRIAGE_SYSTEM_CURRENT,
    _load_prompt_config,
)


def test_production_prompt_loads_from_yaml():
    config = _load_prompt_config()
    assert _PRODUCTION_PROMPT_VERSION == "v3"
    assert _VA_TRIAGE_SYSTEM_CURRENT == config["prompts"]["v3"]["system"].strip()
    assert "annotations_by_pmcid.json" in config["prompts"]["v3"]["reference_dataset"]


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
