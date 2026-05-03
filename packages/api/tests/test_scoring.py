from unittest.mock import patch, MagicMock
import pytest
from src.scoring import score_for_va, fetch_pubmed_abstract


def test_score_for_va_parses_relevant_response():
    with patch("src.scoring.call_llm") as mock_llm:
        mock_llm.return_value = '{"score": 85, "label": "relevant", "reasoning": "Reports CYP2C19 allele effect on clopidogrel efficacy."}'
        result = score_for_va("41969197", "CYP2C19 Genotype and Clopidogrel", "Abstract...", "gpt-4o")
        assert result["score"] == 85
        assert result["label"] == "relevant"
        assert "CYP2C19" in result["reasoning"]


def test_score_for_va_clamps_score_to_100():
    with patch("src.scoring.call_llm") as mock_llm:
        mock_llm.return_value = '{"score": 150, "label": "relevant", "reasoning": "..."}'
        result = score_for_va("12345", "Title", "Abstract", "gpt-4o")
        assert result["score"] == 100


def test_score_for_va_handles_malformed_response():
    with patch("src.scoring.call_llm") as mock_llm:
        mock_llm.return_value = "not json"
        result = score_for_va("12345", "Title", "Abstract", "gpt-4o")
        assert result["score"] == 0
        assert result["label"] == "not_relevant"
        assert "failed" in result["reasoning"].lower()


def test_score_for_va_derives_label_from_score_if_invalid():
    with patch("src.scoring.call_llm") as mock_llm:
        mock_llm.return_value = '{"score": 80, "label": "unknown_label", "reasoning": "..."}'
        result = score_for_va("12345", "Title", "Abstract", "gpt-4o")
        assert result["label"] == "relevant"  # 80 >= 75


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
    with patch("src.scoring.requests.get") as mock_get:
        mock_resp = MagicMock()
        mock_resp.text = xml_text
        mock_get.return_value = mock_resp
        result = fetch_pubmed_abstract("41969197", "test@example.com")
        assert result["title"] == "CYP2C19 Genotype Study"
        assert "We studied" in result["abstract"]
        assert result["error"] is None
