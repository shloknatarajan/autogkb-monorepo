"""VA triage LLM prompt and PubMed abstract fetching helpers."""

import json
import xml.etree.ElementTree as ET

import requests
from shared.utils import call_llm

_VA_TRIAGE_SYSTEM = (
    "You are a pharmacogenomics (PGx) expert curating variant annotations (VAs). "
    "Given a paper's title and abstract, determine if the paper contains VAs: primary "
    "research reporting specific genetic variants (rsIDs like rs28341583, star alleles like "
    "CYP2C19*2, HLA alleles like HLA-B*57:01) in relation to specific drugs AND "
    "pharmacological phenotypes (efficacy, toxicity, adverse reactions, PK/PD).\n\n"
    "Score 0-100:\n"
    "- 75-100 (relevant): Primary research paper with named genetic variants AND named drug(s) "
    "AND pharmacological phenotype. Very likely to yield VAs.\n"
    "- 40-74 (borderline): Has some elements but unclear — e.g. adverse drug reaction title "
    "without visible genotypes, or drug-gene context that may be a review.\n"
    "- 0-39 (not_relevant): Gene-disease association without drug context; sequencing paper "
    "discovering new alleles only; review/meta-analysis/methods paper; drug study without "
    "genetic variants.\n\n"
    "Respond ONLY with valid JSON (no markdown): "
    '{"score": <int 0-100>, "label": "relevant|borderline|not_relevant", '
    '"reasoning": "<1-2 sentences on the key signal>"}'
)


def fetch_pubmed_abstract(pmid: str, ncbi_email: str) -> dict:
    """Fetch title and abstract from NCBI efetch XML.

    Returns {"title": str|None, "abstract": str|None, "error": str|None}.
    """
    try:
        resp = requests.get(
            "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi",
            params={"db": "pubmed", "id": pmid, "retmode": "xml", "email": ncbi_email},
            timeout=15,
        )
        resp.raise_for_status()
        root = ET.fromstring(resp.text)
        title_el = root.find(".//ArticleTitle")
        title = "".join(title_el.itertext()).strip() if title_el is not None else None
        abstract_parts = root.findall(".//AbstractText")
        abstract = (
            " ".join(
                "".join(el.itertext()).strip()
                for el in abstract_parts
                if "".join(el.itertext()).strip()
            )
            or None
        )
        return {"title": title, "abstract": abstract, "error": None}
    except Exception as exc:
        return {"title": None, "abstract": None, "error": str(exc)}


def score_for_va(
    pmid: str, title: str | None, abstract: str | None, model: str
) -> dict:
    """Score a paper for VA relevance using the triage LLM prompt.

    Returns {"score": int, "label": str, "reasoning": str}.
    label is one of: "relevant", "borderline", "not_relevant".
    """
    content = f"Title: {title or 'N/A'}\n\nAbstract: {abstract or 'N/A'}"
    try:
        raw = call_llm(model, _VA_TRIAGE_SYSTEM, content)
        cleaned = (
            raw.strip()
            .removeprefix("```json")
            .removeprefix("```")
            .removesuffix("```")
            .strip()
        )
        parsed = json.loads(cleaned)
        score = max(0, min(100, int(parsed["score"])))
        label = parsed.get("label", "")
        if label not in ("relevant", "borderline", "not_relevant"):
            label = (
                "relevant" if score >= 75 else "borderline" if score >= 40 else "not_relevant"
            )
        return {"score": score, "label": label, "reasoning": str(parsed["reasoning"])}
    except Exception as exc:
        return {"score": 0, "label": "not_relevant", "reasoning": f"Scoring failed: {exc}"}
