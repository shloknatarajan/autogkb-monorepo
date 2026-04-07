"""
PubTator3 API-based variant extraction.

Queries the NCBI PubTator3 API to get variant annotations for articles.
"""

import json
import time

import requests
from loguru import logger

from shared.utils import ROOT

PUBTATOR_API_URL = (
    "https://www.ncbi.nlm.nih.gov/research/pubtator3-api/publications/export/biocjson"
)
REQUEST_DELAY = 0.35

_pmid_mapping = None
_last_request_time = 0.0


def _get_pmid_mapping() -> dict[str, str]:
    """Get or initialize the PMCID-to-PMID mapping singleton."""
    global _pmid_mapping
    if _pmid_mapping is None:
        data_path = ROOT / "data" / "benchmark_v2" / "variant_bench.jsonl"
        _pmid_mapping = {}
        with open(data_path) as f:
            for line in f:
                record = json.loads(line)
                _pmid_mapping[record["pmcid"]] = record["pmid"]
    return _pmid_mapping


def _fetch_pubtator_annotations(pmid: str, full_text: bool = True) -> dict | None:
    """Fetch annotations from PubTator3 API for a given PMID."""
    params = {"pmids": pmid}
    if full_text:
        params["full"] = "true"

    try:
        response = requests.get(PUBTATOR_API_URL, params=params, timeout=30)
        response.raise_for_status()
        return response.json()
    except (requests.exceptions.RequestException, json.JSONDecodeError) as e:
        logger.error(f"Failed to fetch PubTator annotations for PMID {pmid}: {e}")
        return None


def _extract_variants_from_biocjson(biocjson: dict) -> list[str]:
    """Extract variant identifiers from BioC JSON response."""
    variants = set()

    documents = []
    if isinstance(biocjson, dict):
        if "PubTator3" in biocjson:
            documents = biocjson.get("PubTator3", [])
        else:
            documents = [biocjson]
    elif isinstance(biocjson, list):
        documents = biocjson

    for doc in documents:
        for passage in doc.get("passages", []):
            for annotation in passage.get("annotations", []):
                infons = annotation.get("infons", {})
                ann_type = infons.get("type", "")

                if ann_type.lower() in ["mutation", "variant", "snp", "dnamutation"]:
                    rsid = infons.get("rsid", "")
                    if rsid:
                        variants.add(rsid)
                        continue

                    rsids = infons.get("rsids", [])
                    if rsids:
                        for rs in rsids:
                            if rs:
                                variants.add(rs)
                        continue

                    text_mention = annotation.get("text", "").strip()
                    if text_mention:
                        if "*" in text_mention or text_mention.lower().startswith(
                            "hla-"
                        ):
                            variants.add(text_mention)
                        elif text_mention.lower().startswith("rs"):
                            variants.add(text_mention)

    return list(variants)


def pubtator_extract(pmcid: str, full_text: bool = True) -> list[str]:
    global _last_request_time

    pmid_mapping = _get_pmid_mapping()
    pmid = pmid_mapping.get(pmcid)
    if not pmid:
        logger.warning(f"No PMID found for {pmcid}")
        return []

    elapsed = time.time() - _last_request_time
    if elapsed < REQUEST_DELAY:
        time.sleep(REQUEST_DELAY - elapsed)

    biocjson = _fetch_pubtator_annotations(pmid, full_text=full_text)
    _last_request_time = time.time()

    if biocjson is None:
        return []

    return _extract_variants_from_biocjson(biocjson)
