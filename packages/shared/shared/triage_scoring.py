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
    "Given only a paper's title and abstract, estimate whether the paper's own methods and results "
    "are likely to yield a curatable variant-drug annotation. Use only the supplied text; do not "
    "infer findings from outside knowledge or from background statements in the abstract.\n\n"
    "A curatable VA links all three of these elements:\n"
    "  1. a named genetic variant, allele, genotype, haplotype, or genetically determined "
    "metabolizer phenotype;\n"
    "  2. a specific drug or drug class; and\n"
    "  3. a measured pharmacological phenotype such as efficacy, dose requirement, toxicity, "
    "adverse reaction, drug concentration, pharmacokinetics, or pharmacodynamics.\n\n"
    "Score 0-100:\n\n"
    "75-100 (relevant): Primary human research whose own methods/results evaluate all three "
    "elements above. Genetic evidence may be an rsID (rs28341583), star allele (CYP2C19*2), "
    "HLA allele (HLA-B*57:01), genotype/haplotype, or an explicitly genetically determined "
    "metabolizer phenotype. A named enzyme alone is insufficient unless the study compares "
    "genetic variation or genetically inferred activity groups.\n\n"
    "40-74 (borderline): Ambiguous cases:\n"
    "  - The title suggests variant + drug + outcome, but the abstract does not confirm that the "
    "paper itself tested the association\n"
    "  - Drug + pharmacogene/enzyme is present, but genetic stratification or outcome is unclear\n"
    "  - Drug-induced adverse reaction without visible genotype data\n"
    "  - Systematic review or meta-analysis synthesizing variant-drug-phenotype associations\n"
    "  - Case report, preclinical/animal study, guideline, or methods paper with potentially useful "
    "PGx evidence but no clear primary human association\n"
    "  - No abstract is available, even if the title appears relevant; do not score title-only "
    "records above 74\n"
    "  IMPORTANT: When uncertain between borderline and not_relevant, choose borderline. "
    "Missing a curatable paper is a worse error than surfacing one for human review.\n\n"
    "0-39 (not_relevant):\n"
    "  - Gene-disease association with no drug context\n"
    "  - Sequencing paper discovering new alleles only (no drug/phenotype)\n"
    "  - Drug study with no genetic or genetically inferred phenotype comparison\n"
    "  - Drug-drug interaction or enzyme inhibition study that evaluates no human genetic variation\n"
    "  - Pure methodology, software, survey/attitude paper, editorial, or general narrative review\n\n"
    "Examples:\n"
    "  - CYP2C19*2 carriers had reduced clopidogrel response: relevant.\n"
    "  - CYP3A4 metabolized the drug in vitro, with no genetic comparison: not_relevant.\n"
    "  - HLA-B*57:01 is mentioned only as background and was not studied: not_relevant.\n"
    "  - A title names UGT1A1, irinotecan, and toxicity but no abstract is available: borderline.\n\n"
    "Respond ONLY with valid JSON (no markdown): "
    '{"score": <int 0-100>, "label": "relevant|borderline|not_relevant", '
    '"reasoning": "<1-2 sentences identifying which required elements are present or missing and '
    'whether they are findings of this study>"}'
)


def _label_for_score(score: int) -> str:
    return "relevant" if score >= 75 else "borderline" if score >= 40 else "not_relevant"


def _score_response(raw: str, has_abstract: bool) -> dict:
    cleaned = (
        raw.strip()
        .removeprefix("```json")
        .removeprefix("```")
        .removesuffix("```")
        .strip()
    )
    parsed = json.loads(cleaned)
    score = max(0, min(100, int(parsed["score"])))
    if not has_abstract:
        score = min(score, 74)
    return {
        "score": score,
        "label": _label_for_score(score),
        "reasoning": str(parsed.get("reasoning", "")),
    }


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
    if not title and not abstract:
        return {
            "score": 0,
            "label": "not_relevant",
            "reasoning": "No title or abstract was available from PubMed.",
        }
    content = f"Title: {title or 'N/A'}\n\nAbstract: {abstract or 'N/A'}"
    try:
        raw = call_llm(model, _VA_TRIAGE_SYSTEM_V2, content)
        return _score_response(raw, has_abstract=bool(abstract))
    except Exception as exc:
        return {"score": 0, "label": "not_relevant", "reasoning": f"Scoring failed: {exc}"}


def score_for_va_with_system(
    pmid: str, title: str | None, abstract: str | None, model: str, system_prompt: str
) -> dict:
    """Score a paper using a caller-supplied system prompt.

    Same return shape as score_for_va: {"score": int, "label": str, "reasoning": str}.
    """
    if not title and not abstract:
        return {
            "score": 0,
            "label": "not_relevant",
            "reasoning": "No title or abstract was available from PubMed.",
        }
    content = f"Title: {title or 'N/A'}\n\nAbstract: {abstract or 'N/A'}"
    try:
        raw = call_llm(model, system_prompt, content)
        return _score_response(raw, has_abstract=bool(abstract))
    except Exception as exc:
        return {"score": 0, "label": "not_relevant", "reasoning": f"Scoring failed: {exc}"}
