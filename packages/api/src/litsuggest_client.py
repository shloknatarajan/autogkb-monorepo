"""Client for the public NCBI LitSuggest API."""

import csv
import io

import requests

_LITSUGGEST_API = "https://www.ncbi.nlm.nih.gov/research/litsuggest-api"


def list_weekly_jobs(project_id: str) -> list[dict]:
    """Return weekly digest jobs for a project, newest first.

    Each entry: {"id": str, "name": str, "status": str}
    """
    resp = requests.get(
        f"{_LITSUGGEST_API}/project/{project_id}/jobs/",
        timeout=30,
    )
    resp.raise_for_status()
    raw: list[dict] = resp.json()
    return [
        {
            "id": item["_id"]["$oid"],
            "name": item.get("name", ""),
            "status": item.get("status", ""),
        }
        for item in raw
    ]


def fetch_weekly_pmids(
    project_id: str, job_id: str, min_score: float = 0.9
) -> list[dict]:
    """Fetch PMIDs from a specific weekly digest job, filtered by min_score.

    Returns list of {"pmid": str, "litsuggest_score": float}, sorted score desc.
    """
    resp = requests.get(
        f"{_LITSUGGEST_API}/project/{project_id}/job/{job_id}/export",
        params={"name": "litsuggest.tsv"},
        timeout=60,
    )
    resp.raise_for_status()
    reader = csv.DictReader(io.StringIO(resp.text), delimiter="\t")
    results = []
    for row in reader:
        try:
            score = float(row.get("score", ""))
        except (ValueError, TypeError):
            continue
        if score >= min_score:
            pmid = row.get("pmid", "").strip()
            if pmid:
                results.append({"pmid": pmid, "litsuggest_score": score})
    return sorted(results, key=lambda x: x["litsuggest_score"], reverse=True)
