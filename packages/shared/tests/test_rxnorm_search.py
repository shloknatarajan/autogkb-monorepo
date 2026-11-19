from pathlib import Path

import requests

from shared.term_normalization.drugs import DrugLookup, DrugSearchResult
from shared.term_normalization.drugs import search as search_module


class _Response:
    def __init__(self, payload: dict):
        self.payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


def test_rxnorm_selects_best_rxnorm_candidate_and_preserves_its_identifier(
    monkeypatch,
):
    payload = {
        "approximateGroup": {
            "candidate": [
                {
                    "rxcui": "855332",
                    "name": "WARFARIN NA 5MG TAB",
                    "rank": "1",
                    "source": "VANDF",
                },
                {
                    "rxcui": "855331",
                    "name": "warfarin sodium 5 MG",
                    "rank": "4",
                    "source": "RXNORM",
                },
            ]
        }
    }
    captured = {}

    def fake_get(url, *, params, timeout):
        captured.update(url=url, params=params, timeout=timeout)
        return _Response(payload)

    monkeypatch.setattr(search_module.requests, "get", fake_get)

    result = search_module.rxnorm_search("warfarin sodium 5 mg tablet")

    assert result is not None
    assert result.id == "RXN855331"
    assert result.normalized_term == "warfarin sodium 5 MG"
    assert result.source == "rxnorm"
    assert result.match_type == "rxnorm_approximate"
    assert result.score == 1.0
    assert captured["params"]["maxEntries"] == 10


def test_rxnorm_rejects_candidate_below_threshold(monkeypatch):
    payload = {
        "approximateGroup": {
            "candidate": [
                {
                    "rxcui": "83367",
                    "name": "atorvastatin",
                    "rank": "1",
                    "source": "RXNORM",
                }
            ]
        }
    }
    monkeypatch.setattr(
        search_module.requests,
        "get",
        lambda *args, **kwargs: _Response(payload),
    )

    assert search_module.rxnorm_search("warfarin", threshold=0.8) is None


def test_rxnorm_network_failure_returns_none(monkeypatch):
    def fail(*args, **kwargs):
        raise requests.Timeout("timed out")

    monkeypatch.setattr(search_module.requests, "get", fail)

    assert search_module.rxnorm_search("warfarin") is None


def test_rxnorm_malformed_payload_returns_none(monkeypatch):
    monkeypatch.setattr(
        search_module.requests,
        "get",
        lambda *args, **kwargs: _Response({"approximateGroup": []}),
    )

    assert search_module.rxnorm_search("warfarin") is None


def test_rxnorm_lookup_does_not_require_a_pharmgkb_mapping(
    monkeypatch,
    tmp_path: Path,
):
    result = DrugSearchResult(
        raw_input="Coumadin",
        id="RXN202421",
        normalized_term="Coumadin",
        url="https://example.test/RXN202421",
        score=1.0,
        matched_term="Coumadin",
        match_type="rxnorm_approximate",
        source="rxnorm",
    )
    monkeypatch.setattr(search_module, "rxnorm_search", lambda *args, **kwargs: result)

    assert DrugLookup(data_dir=tmp_path).rxnorm_lookup("Coumadin") == [result]


def test_rxnorm_validates_search_options():
    try:
        search_module.rxnorm_search("warfarin", threshold=1.1)
    except ValueError as error:
        assert "threshold" in str(error)
    else:
        raise AssertionError("invalid threshold did not raise")

    try:
        search_module.rxnorm_search("warfarin", max_entries=0)
    except ValueError as error:
        assert "max_entries" in str(error)
    else:
        raise AssertionError("invalid max_entries did not raise")
