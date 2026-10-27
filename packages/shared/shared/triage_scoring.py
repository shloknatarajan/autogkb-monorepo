"""VA triage LLM prompt and PubMed abstract fetching helpers."""

import json
from pathlib import Path

import defusedxml.ElementTree as ET  # stdlib ET is vulnerable to XXE; defusedxml is safe
import requests
import yaml
from shared.utils import call_llm

_LEGACY_VA_TRIAGE_SYSTEM = (
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


_LEGACY_VA_TRIAGE_SYSTEM_V2 = """You are screening PubMed records for a ClinPGx literature-review queue. Given only the
title and abstract, decide whether a human ClinPGx curator should see the paper. This is a
high-recall screening task, not a final decision that the paper contains a curatable
variant annotation.

Score 0-100. The score determines the label:
- 75-100 = relevant
- 40-74 = borderline
- 0-39 = not_relevant

First identify the paper's actual contribution. A gene name, drug name, or speculative
sentence about possible treatment implications is not by itself a ClinPGx contribution.

RELEVANT (75-100)
- A variant, allele, genotype, or metabolizer phenotype is associated with drug efficacy,
  toxicity, dose, adverse reaction, concentration, PK, or PD.
- A pharmacogene variant is functionally tested with a named drug or substrate.
- A study reports quantitative human PK/PD, therapeutic drug monitoring,
  exposure-response, or a drug-drug/food-drug/enzyme/transporter interaction.
- Treatment is explicitly genotype/variant targeted, including CFTR modulators, and the
  paper reports biological, safety, or clinical treatment outcomes—not merely adherence,
  access, prevalence, or routine service improvement.
- The paper is a PGx guideline, recommendation, implementation resource, allele-function
  resource, or other authoritative synthesis.

BORDERLINE / SEND TO REVIEW (40-74)
- Population frequencies of established pharmacogene alleles, diplotypes, or metabolizer
  phenotypes, even without drug outcomes.
- PGx testing, assay validation, quality assessment, implementation, access, uptake,
  disparities, or medication-use research when it explicitly concerns actionable
  gene-drug pairs or pharmacogenetic testing.
- Comments, letters, editorials, perspectives, case reports, or title-only records with a
  recognizable PGx, precision-dosing, pharmacogene-testing, malignant-hyperthermia, or
  serious suspected adverse-drug-reaction topic.
- Genotype-to-clinical, biochemical, structural, or functional phenotype work involving
  an established pharmacogene when the phenotype is plausibly useful for interpreting
  that gene. Examples include CYP metabolizer phenotypes, UGT1A1 bilirubin, G6PD
  deficiency, CFTR genotype/phenotype cohorts, RYR1 malignant hyperthermia, and XDH
  enzyme/urate phenotypes.
- Preclinical, animal, modeling, or methods work only when it directly tests a drug
  response, a pharmacogene mechanism, or a recognized pharmacogenetic phenotype.

NOT_RELEVANT (0-39)
- Merely mentioning an established pharmacogene is insufficient. Reject disease-only
  susceptibility, diagnosis, natural history, case description, or broad mechanistic
  hypothesis when the supplied text offers neither a drug/PGx contribution nor a useful
  pharmacogene genotype-to-phenotype result.
- Reject treatment adherence, quality improvement, health-services, or utilization work
  that merely involves a pharmacogene-targeted drug but contributes no genetics, PK/PD,
  exposure-response, drug-safety signal, or PGx implementation evidence.
- Reject a general treatment, dose-regimen comparison, or narrative drug review without
  genetics or quantitative PK/PD, TDM, exposure-response, or interaction evidence.
- Reject a disease-risk association for a gene that happens to be a pharmacogene when the
  result has no pharmacologic or useful pharmacogene-interpretation contribution.
- Reject an animal genetic-disease model when a therapy is tested for a disease phenotype
  unrelated to PGx or interpretation of the pharmacogene.
- Reject discovery/frequency of a novel HLA or other allele with no drug, functional, PGx,
  or established pharmacogene interpretation.

Boundary examples:
- CYP2C19 allele frequencies in a population: borderline.
- CFTR genotype/phenotype cohort or CFTR screening-panel limitations: borderline.
- CFTR-modulator adherence or a general cystic-fibrosis quality-improvement program:
  not_relevant.
- An unrelated antisense treatment in a CFTR-mutant mouse: not_relevant.
- RYR1 animal response to volatile anesthetic or dantrolene: borderline or relevant;
  veterinary malignant-hyperthermia case evidence alone: not_relevant unless it informs
  human pharmacogene interpretation.
- Serious suspected adverse drug reaction case report: borderline; routine dosing review
  without genetics or quantitative clinical pharmacology: not_relevant.
- HLA allele frequency plus only a speculative statement about future therapy response:
  not_relevant.

Do not invent details absent from the supplied text. Absence of an abstract increases
uncertainty but does not automatically make a clearly PGx-related title irrelevant.

Add zero, one, or both of these independent tags:
- variant_annotations: The supplied title/abstract likely contains evidence suitable for
  one or more ClinPGx variant annotations: a specific variant, allele, genotype, diplotype,
  or metabolizer phenotype connected to a named drug and a pharmacological phenotype.
  Reviews, guidelines, implementation papers, allele-frequency-only studies, general
  pharmacokinetic studies, and drug studies without genetic stratification do not receive
  this tag.
- general_pharmacogenomics: The paper has genuine pharmacogenomic relevance, including
  variant annotations as well as PGx guidelines, implementation, testing, allele-function
  resources, pharmacogene frequencies, and other useful gene-drug evidence. A paper tagged
  variant_annotations must also be tagged general_pharmacogenomics.

Tags describe likely content and are additive; the score and label still describe triage
confidence. Do not add a tag merely because a gene or drug is mentioned.

Respond only with valid JSON:
{"score": <integer 0-100>, "label": "relevant|borderline|not_relevant", "tags": [<zero or more of "variant_annotations", "general_pharmacogenomics">], "reasoning": "<1-2 sentences explaining the decisive evidence and tags>"}"""


_PROMPT_CONFIG_PATH = Path(__file__).with_name("triage_prompts.yaml")


def _load_prompt_config(path: Path = _PROMPT_CONFIG_PATH) -> dict:
    """Load and validate the versioned triage prompt configuration."""
    with path.open(encoding="utf-8") as fh:
        config = yaml.safe_load(fh)
    if not isinstance(config, dict):
        raise ValueError(f"Triage prompt config must be a mapping: {path}")
    prompts = config.get("prompts")
    production = config.get("production")
    if not isinstance(prompts, dict) or production not in prompts:
        raise ValueError(f"Unknown production triage prompt {production!r} in {path}")
    if not isinstance(prompts[production].get("system"), str):
        raise ValueError(f"Production triage prompt {production!r} has no system text")
    return config


_TRIAGE_PROMPT_CONFIG = _load_prompt_config()
_PRODUCTION_PROMPT_VERSION = _TRIAGE_PROMPT_CONFIG["production"]
_VA_TRIAGE_SYSTEM_CURRENT = _TRIAGE_PROMPT_CONFIG["prompts"][_PRODUCTION_PROMPT_VERSION][
    "system"
].strip()
_VA_TRIAGE_SYSTEM_V2 = _VA_TRIAGE_SYSTEM_CURRENT
_VA_TRIAGE_SYSTEM = _VA_TRIAGE_SYSTEM_CURRENT


def _label_for_score(score: int) -> str:
    return (
        "relevant" if score >= 75 else "borderline" if score >= 40 else "not_relevant"
    )


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
    raw_tags = parsed.get("tags", [])
    if not isinstance(raw_tags, list):
        raw_tags = []
    allowed_tags = {"variant_annotations", "general_pharmacogenomics"}
    tags = [tag for tag in raw_tags if tag in allowed_tags]
    if "variant_annotations" in tags and "general_pharmacogenomics" not in tags:
        tags.append("general_pharmacogenomics")
    return {
        "score": score,
        "label": _label_for_score(score),
        "tags": tags,
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

    Returns {"score": int, "label": str, "tags": list[str], "reasoning": str}.
    label is one of: "relevant", "borderline", "not_relevant".
    """
    if not title and not abstract:
        return {
            "score": 0,
            "label": "not_relevant",
            "tags": [],
            "reasoning": "No title or abstract was available from PubMed.",
        }
    content = f"Title: {title or 'N/A'}\n\nAbstract: {abstract or 'N/A'}"
    try:
        kwargs = {"reasoning_effort": "xhigh"} if model == "gpt-6-astra" else {}
        raw = call_llm(model, _VA_TRIAGE_SYSTEM_V2, content, **kwargs)
        return _score_response(raw, has_abstract=bool(abstract))
    except Exception as exc:
        return {
            "score": 0,
            "label": "not_relevant",
            "tags": [],
            "reasoning": f"Scoring failed: {exc}",
        }


def score_for_va_with_system(
    pmid: str,
    title: str | None,
    abstract: str | None,
    model: str,
    system_prompt: str,
    reasoning_effort: str | None = None,
) -> dict:
    """Score a paper using a caller-supplied system prompt.

    Same return shape as score_for_va, including additive content tags.
    """
    if not title and not abstract:
        return {
            "score": 0,
            "label": "not_relevant",
            "tags": [],
            "reasoning": "No title or abstract was available from PubMed.",
        }
    content = f"Title: {title or 'N/A'}\n\nAbstract: {abstract or 'N/A'}"
    try:
        kwargs = {"reasoning_effort": reasoning_effort} if reasoning_effort else {}
        raw = call_llm(model, system_prompt, content, **kwargs)
        return _score_response(raw, has_abstract=bool(abstract))
    except Exception as exc:
        return {
            "score": 0,
            "label": "not_relevant",
            "tags": [],
            "reasoning": f"Scoring failed: {exc}",
        }
