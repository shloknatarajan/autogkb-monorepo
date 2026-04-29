from unittest.mock import patch, MagicMock
import pytest
from src.litsuggest_client import list_weekly_jobs, fetch_weekly_pmids


def _mock_jobs_response():
    return [
        {"_id": {"$oid": "abc123"}, "name": "Automatic Weekly Digest (Apr 19 2026 to Apr 25 2026)", "status": "DONE"},
        {"_id": {"$oid": "def456"}, "name": "Automatic Weekly Digest (Apr 12 2026 to Apr 18 2026)", "status": "DONE"},
    ]


def _mock_tsv_response():
    return "pmid\tscore\ttriage.decision\ttriage.note\ttriage.last_update_user\n41969197\t0.9999\t\t\t\n41971283\t0.8500\t\t\t\n42000001\t0.9500\t\t\t\n"


def test_list_weekly_jobs_returns_job_list():
    with patch("src.litsuggest_client.requests.get") as mock_get:
        mock_resp = MagicMock()
        mock_resp.json.return_value = _mock_jobs_response()
        mock_get.return_value = mock_resp
        jobs = list_weekly_jobs("proj123")
        assert len(jobs) == 2
        assert jobs[0]["id"] == "abc123"
        assert jobs[0]["name"] == "Automatic Weekly Digest (Apr 19 2026 to Apr 25 2026)"
        assert jobs[0]["status"] == "DONE"
        mock_get.assert_called_once()
        assert "proj123" in mock_get.call_args[0][0]


def test_fetch_weekly_pmids_filters_by_score():
    with patch("src.litsuggest_client.requests.get") as mock_get:
        mock_resp = MagicMock()
        mock_resp.text = _mock_tsv_response()
        mock_get.return_value = mock_resp
        results = fetch_weekly_pmids("proj123", "abc123", min_score=0.9)
        pmids = [r["pmid"] for r in results]
        assert "41969197" in pmids
        assert "42000001" in pmids
        assert "41971283" not in pmids


def test_fetch_weekly_pmids_returns_score():
    with patch("src.litsuggest_client.requests.get") as mock_get:
        mock_resp = MagicMock()
        mock_resp.text = _mock_tsv_response()
        mock_get.return_value = mock_resp
        results = fetch_weekly_pmids("proj123", "abc123", min_score=0.9)
        assert results[0]["litsuggest_score"] == pytest.approx(0.9999)
