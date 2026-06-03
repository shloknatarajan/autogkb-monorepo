"""VA triage LLM prompt and PubMed abstract fetching helpers."""

import json

import defusedxml.ElementTree as ET  # stdlib ET is vulnerable to XXE; defusedxml is safe
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


_VA_TRIAGE_SYSTEM_V2 = (
    "You are a pharmacogenomics (PGx) expert curating variant annotations (VAs) for PharmGKB. "
    "Given a paper's title and abstract, determine if the paper is likely to yield cuatable VAs.\n\n"
    "Score 0-100:\n\n"
    "75-100 (relevant): Primary research paper that EITHER:\n"
    "  (a) Reports named genetic variants (rsIDs like rs28341583, star alleles like CYP2C19*2, "
    "HLA alleles like HLA-B*57:01) AND named drug(s) AND pharmacological phenotype "
    "(efficacy, toxicity, adverse reactions, PK/PD), OR\n"
    "  (b) Reports pharmacokinetic or pharmacodynamic effects of a specific drug via a named "
    "pharmacogene or enzyme (CYP2C19, CYP3A4, CYP2D6, UGT1A1, SLCO1B1, DPYD, TPMT, etc.) "
    "even without individual allele identifiers — enzyme activity data implies annotatable "
    "variant associations.\n\n"
    "40-74 (borderline): Ambiguous cases:\n"
    "  - Drug + named pharmacogene/enzyme but phenotype is unclear or may be a review\n"
    "  - Title has gene + drug + outcome elements but abstract reveals a methods paper\n"
    "  - Drug-induced adverse reaction without visible genotype data\n"
    "  - Systematic review or meta-analysis synthesizing variant-drug-phenotype associations\n"
    "  IMPORTANT: When uncertain between borderline and not_relevant, choose borderline. "
    "Missing a cuatable paper is a worse error than surfacing one for human review.\n\n"
    "0-39 (not_relevant):\n"
    "  - Gene-disease association with no drug context\n"
    "  - Sequencing paper discovering new alleles only (no drug/phenotype)\n"
    "  - Drug study with no genetic or enzyme component\n"
    "  - Pure methodology, software, or survey/attitude paper\n\n"
    "Respond ONLY with valid JSON (no markdown): "
    '{"score": <int 0-100>, "label": "relevant|borderline|not_relevant", '
    '"reasoning": "<1-2 sentences on the key signal>"}'
)


def fetch_pubmed_abstract(pmid: str, ncbi_email: str) -> dict:
    """Fetch title, abstract, and pmcid for a PMID from NCBI efetch XML.

    Returns {"title": str|None, "abstract": str|None, "pmcid": str|None, "error": str|None}.
    """
    try:
        resp = requests.get(
            "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi",
            params={"db": "pubmed", "id": pmid, "retmode": "xml", "email": ncbi_email},
            timeout=15,
        )
        resp.raise_for_status()
        root = ET.fromstring(resp.text)
        article = root.find(".//PubmedArticle")
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
        pmcid = None
        if article is not None:
            for aid in article.findall(".//PubmedData/ArticleIdList/ArticleId"):
                if aid.get("IdType") == "pmc":
                    pmcid = aid.text
                    break
        return {"title": title, "abstract": abstract, "pmcid": pmcid, "error": None}
    except Exception as exc:
        return {"title": None, "abstract": None, "pmcid": None, "error": str(exc)}


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
        return {"score": score, "label": label, "reasoning": str(parsed.get("reasoning", ""))}
    except Exception as exc:
        return {"score": 0, "label": "not_relevant", "reasoning": f"Scoring failed: {exc}"}


def score_for_va_with_system(
    pmid: str, title: str | None, abstract: str | None, model: str, system_prompt: str
) -> dict:
    """Score a paper using a caller-supplied system prompt.

    Same return shape as score_for_va: {"score": int, "label": str, "reasoning": str}.
    """
    content = f"Title: {title or 'N/A'}\n\nAbstract: {abstract or 'N/A'}"
    try:
        raw = call_llm(model, system_prompt, content)
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
        return {"score": score, "label": label, "reasoning": str(parsed.get("reasoning", ""))}
    except Exception as exc:
        return {"score": 0, "label": "not_relevant", "reasoning": f"Scoring failed: {exc}"}
