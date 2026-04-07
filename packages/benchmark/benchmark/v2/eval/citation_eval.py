"""
Evaluation wrapper for citation finding experiments.

Two-step evaluation:
1. Grounding check: verify each citation actually appears in the source article
2. LLM judge: score how well the verified citations support the claim (0-1)
"""

import json
import re
import unicodedata
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

load_dotenv()

from loguru import logger

from shared.utils import call_llm, get_markdown_text
from pipeline.modules.citations.utils import parse_judge_output

RESULTS_DIR = Path(__file__).parent / "results"


# ============================================================================
# Text grounding / verification
# ============================================================================


def normalize_text(text: str) -> str:
    """Normalize text for fuzzy matching.

    Handles unicode variants (dashes, quotes, whitespace), markdown formatting,
    and other common differences between LLM output and source article text.
    """
    # Unicode normalize to NFKC (compatibility decomposition + canonical composition)
    # This maps e.g. \u2212 (minus sign) -> - , \u2019 (right quote) -> '
    text = unicodedata.normalize("NFKC", text)

    # Collapse all whitespace (newlines, tabs, multiple spaces) to single space
    text = re.sub(r"\s+", " ", text)

    # Normalize dashes: en-dash, em-dash, minus sign, etc. -> hyphen
    text = re.sub(r"[\u2010\u2011\u2012\u2013\u2014\u2015\u2212\u2796−–—]", "-", text)

    # Normalize quotes: smart quotes -> simple quotes
    text = re.sub(r"[\u2018\u2019\u201A\u201B`]", "'", text)
    text = re.sub(r"[\u201C\u201D\u201E\u201F]", '"', text)

    # Strip markdown bold/italic markers
    text = re.sub(r"\*{1,3}", "", text)
    text = re.sub(r"_{1,3}", "", text)

    # Strip markdown heading markers
    text = re.sub(r"^#{1,6}\s*", "", text)

    # Normalize common special chars
    text = text.replace("\u00a0", " ")  # non-breaking space
    text = text.replace("\u200b", "")  # zero-width space
    text = text.replace("\u00d7", "x")  # multiplication sign
    text = text.replace("\u2264", "<=")  # less-than-or-equal
    text = text.replace("\u2265", ">=")  # greater-than-or-equal
    text = text.replace("\u03b1", "alpha")
    text = text.replace("\u03b2", "beta")

    # Strip leading/trailing whitespace
    text = text.strip()

    # Lowercase for comparison
    text = text.lower()

    return text


def citation_is_grounded(citation: str, article_text: str) -> bool:
    """Check whether a citation text actually appears in the article.

    Uses normalized fuzzy matching to handle formatting/unicode differences.
    A citation is considered grounded if its normalized form is a substring
    of the normalized article, or if a sufficiently long prefix (80%+ chars)
    matches as a substring.

    Args:
        citation: The citation text to verify.
        article_text: The full article text (markdown).

    Returns:
        True if the citation is grounded in the article.
    """
    norm_citation = normalize_text(citation)
    norm_article = normalize_text(article_text)

    # Skip very short citations (likely parsing artifacts)
    if len(norm_citation) < 20:
        return False

    # Exact substring match after normalization
    if norm_citation in norm_article:
        return True

    # Try matching a long prefix (LLMs sometimes truncate or append to citations)
    # Use 80% of the citation length as threshold
    prefix_len = max(int(len(norm_citation) * 0.8), 40)
    if len(norm_citation) > 40 and norm_citation[:prefix_len] in norm_article:
        return True

    # Try word-level overlap for table references and short citations
    # Split into words and check if most words appear in a nearby window
    citation_words = norm_citation.split()
    if len(citation_words) >= 5:
        # Sliding window: check if 80%+ of citation words appear in any
        # window of 2x the citation word count
        window_size = len(citation_words) * 3
        article_words = norm_article.split()
        threshold = int(len(citation_words) * 0.8)

        for start in range(0, len(article_words) - window_size + 1, 5):
            window = set(article_words[start : start + window_size])
            matches = sum(1 for w in citation_words if w in window)
            if matches >= threshold:
                return True

    return False


def check_grounding(
    pmcid: str,
    associations: list[dict],
) -> list[dict[str, Any]]:
    """Check which citations are actually grounded in the source article.

    Args:
        pmcid: PMCID identifier.
        associations: List of {variant, sentence, citations} dicts.

    Returns:
        List of dicts with grounding info per association:
        {variant, total, grounded, ungrounded, grounded_citations, ungrounded_citations}
    """
    article_text = get_markdown_text(pmcid)
    if not article_text:
        logger.warning(f"No article text for {pmcid}, cannot verify grounding")
        return [
            {
                "variant": a.get("variant", ""),
                "total": len(a.get("citations", [])),
                "grounded": 0,
                "ungrounded": len(a.get("citations", [])),
                "grounding_rate": 0.0,
                "grounded_citations": [],
                "ungrounded_citations": a.get("citations", []),
            }
            for a in associations
        ]

    results = []
    for assoc in associations:
        cites = assoc.get("citations", [])
        grounded = []
        ungrounded = []

        for cite in cites:
            if citation_is_grounded(cite, article_text):
                grounded.append(cite)
            else:
                ungrounded.append(cite)

        rate = len(grounded) / len(cites) if cites else 0.0
        results.append(
            {
                "variant": assoc.get("variant", ""),
                "total": len(cites),
                "grounded": len(grounded),
                "ungrounded": len(ungrounded),
                "grounding_rate": rate,
                "grounded_citations": grounded,
                "ungrounded_citations": ungrounded,
            }
        )

    return results


# ============================================================================
# LLM judge (0-1 scale)
# ============================================================================


JUDGE_SYSTEM_PROMPT = """You are an expert evaluator of scientific citations for pharmacogenomic claims.

Your task is to evaluate how well a set of citations from a scientific article support a given pharmacogenomic association claim.

You will score each citation set on a scale of 0.0 to 1.0 based on:
1. Relevance: Do the citations directly relate to the claimed association?
2. Support: Do the citations provide evidence for the specific claim?
3. Completeness: Do the citations together include all key information (statistical evidence, sample size, effect direction)?
4. Quality: Are the citations from appropriate sections (Results, Methods, Tables)?

Scoring guidelines:
- 0.9-1.0: Excellent - Citations strongly support the claim with statistical evidence and key details
- 0.7-0.89: Good - Citations support the claim with reasonable evidence
- 0.5-0.69: Fair - Citations relate to the claim but lack key supporting details
- 0.3-0.49: Poor - Citations are tangentially related but don't strongly support the claim
- 0.0-0.29: Very Poor - Citations are irrelevant or contradictory
"""

JUDGE_USER_PROMPT_TEMPLATE = """Evaluate the citation quality for the following pharmacogenomic associations from PMCID {pmcid}.

For each numbered association, I will provide:
1. The pharmacogenomic claim (association sentence)
2. The citations found to support this claim (these have been verified to exist in the article)

Note: The same variant may appear multiple times with different association sentences. Please score EACH association separately based on how well the citations support that specific claim.

Please score each association's citation set on a 0.0-1.0 scale and provide a brief justification.

{associations_and_citations}

OUTPUT FORMAT:
For each association (using the same number from the input), provide:
ASSOCIATION: [number]
SCORE: [0.0-1.0]
JUSTIFICATION: [1-2 sentence explanation of the score]

Then a blank line before the next association.

Example:
ASSOCIATION: 1
SCORE: 0.85
JUSTIFICATION: Citations provide strong statistical evidence (p-values) and effect sizes. Table reference is appropriate. Missing explicit sample size but overall well-supported.

ASSOCIATION: 2
SCORE: 0.72
JUSTIFICATION: Citations support the general association but lack specific statistical significance values. Effect direction is clear.
"""


def evaluate_pmcid(
    pmcid: str,
    associations: list[dict],
    judge_model: str,
) -> list[dict[str, Any]]:
    """Evaluate citations for a single PMCID.

    First checks grounding (are citations real?), then uses LLM judge on
    the grounded citations only to score support quality.

    Args:
        pmcid: PMCID identifier.
        associations: List of {variant, sentence, explanation, citations} dicts.
        judge_model: Model name for judge LLM.

    Returns:
        List of result dicts per association with grounding + quality scores.
    """
    logger.info(
        f"Evaluating citations for PMCID: {pmcid} ({len(associations)} associations)"
    )

    # Step 1: Check grounding
    grounding_results = check_grounding(pmcid, associations)

    for i, gr in enumerate(grounding_results):
        if gr["ungrounded"] > 0:
            logger.warning(
                f"  Association {i + 1} ({gr['variant']}): "
                f"{gr['ungrounded']}/{gr['total']} citations NOT found in article"
            )

    # Step 2: Build associations with only grounded citations for the LLM judge
    grounded_associations = []
    for assoc, gr in zip(associations, grounding_results):
        grounded_associations.append(
            {
                **assoc,
                "citations": gr["grounded_citations"],
            }
        )

    # Format for the LLM judge prompt
    associations_text_parts = []
    for i, assoc in enumerate(grounded_associations):
        variant = assoc["variant"]
        sentence = assoc.get("sentence", "")
        cites = assoc.get("citations", [])

        cite_text = "\n   ".join([f"{j + 1}. {c}" for j, c in enumerate(cites)])

        associations_text_parts.append(
            f"ASSOCIATION {i + 1}:\n"
            f"VARIANT: {variant}\n"
            f"CLAIM: {sentence}\n"
            f"CITATIONS:\n   {cite_text if cites else '(No verified citations found)'}"
        )

    associations_and_citations = "\n\n".join(associations_text_parts)

    user_prompt = JUDGE_USER_PROMPT_TEMPLATE.format(
        pmcid=pmcid, associations_and_citations=associations_and_citations
    )

    try:
        logger.debug(f"Calling judge LLM for {len(associations)} association(s)")
        output = call_llm(judge_model, JUDGE_SYSTEM_PROMPT, user_prompt)
    except Exception as e:
        logger.error(f"Error calling judge for {pmcid}: {e}")
        return [
            {
                "variant": assoc["variant"],
                "sentence": assoc.get("sentence", ""),
                "grounding_rate": gr["grounding_rate"],
                "grounded": gr["grounded"],
                "total_citations": gr["total"],
                "quality_score": 0.0,
                "combined_score": 0.0,
                "justification": "Error during evaluation",
            }
            for assoc, gr in zip(associations, grounding_results)
        ]

    parsed_scores = parse_judge_output(output)

    result: list[dict[str, Any]] = []
    for i, (assoc, gr) in enumerate(zip(associations, grounding_results)):
        assoc_idx = i + 1
        score_info = parsed_scores.get(
            assoc_idx, {"score": 0.0, "justification": "No score provided by judge"}
        )

        if assoc_idx not in parsed_scores:
            logger.warning(
                f"Missing score for association {assoc_idx}, defaulting to 0"
            )

        quality_score = float(score_info["score"])
        # Clamp to 0-1 in case LLM returned 0-100 scale
        if quality_score > 1.0:
            quality_score = quality_score / 100.0

        grounding_rate = gr["grounding_rate"]

        # Combined score: quality weighted by grounding rate
        # If none of the citations are real, the quality score is meaningless
        combined_score = quality_score * grounding_rate

        result.append(
            {
                "variant": assoc["variant"],
                "sentence": assoc.get("sentence", ""),
                "grounding_rate": round(grounding_rate, 3),
                "grounded": gr["grounded"],
                "total_citations": gr["total"],
                "ungrounded_citations": gr["ungrounded_citations"],
                "quality_score": round(quality_score, 3),
                "combined_score": round(combined_score, 3),
                "justification": score_info["justification"],
            }
        )

    return result


def evaluate_from_file(
    citations_path: str | Path,
    judge_model: str = "claude-sonnet-4-20250514",
    save_results: bool = True,
) -> dict[str, Any]:
    """Evaluate citations from a saved output file.

    Runs grounding check + LLM judge, produces combined scores on 0-1 scale.

    Args:
        citations_path: Path to a citations.json output file.
        judge_model: LLM model to use for judging.
        save_results: Whether to save evaluation results.

    Returns:
        Dictionary with evaluation summary.
    """
    citations_path = Path(citations_path)

    with open(citations_path) as f:
        data = json.load(f)

    citations_data = data.get("citations", data)
    run_name = data.get("run_name", "unknown")

    all_results: dict[str, list[dict[str, Any]]] = {}
    pmcid_summaries = []
    all_combined_scores = []
    all_grounding_rates = []

    for pmcid, pmcid_associations in citations_data.items():
        if not pmcid_associations:
            logger.warning(f"No associations found for {pmcid}, skipping evaluation")
            continue

        # Convert Citation model dicts to plain dicts if needed
        associations = []
        for a in pmcid_associations:
            if isinstance(a, dict):
                associations.append(a)
            else:
                associations.append(a.model_dump())

        scores = evaluate_pmcid(pmcid, associations, judge_model)
        all_results[pmcid] = scores

        combined_scores = [s["combined_score"] for s in scores]
        grounding_rates = [s["grounding_rate"] for s in scores]
        avg_combined = (
            sum(combined_scores) / len(combined_scores) if combined_scores else 0
        )
        avg_grounding = (
            sum(grounding_rates) / len(grounding_rates) if grounding_rates else 0
        )

        all_combined_scores.extend(combined_scores)
        all_grounding_rates.extend(grounding_rates)

        pmcid_summaries.append(
            {
                "pmcid": pmcid,
                "num_associations": len(scores),
                "avg_combined_score": round(avg_combined, 3),
                "avg_grounding_rate": round(avg_grounding, 3),
                "scores": scores,
            }
        )

        logger.info(
            f"  {pmcid}: combined={avg_combined:.3f} (grounding={avg_grounding:.3f})"
        )

    overall_combined = (
        sum(all_combined_scores) / len(all_combined_scores)
        if all_combined_scores
        else 0
    )
    overall_grounding = (
        sum(all_grounding_rates) / len(all_grounding_rates)
        if all_grounding_rates
        else 0
    )

    result = {
        "judge_model": judge_model,
        "overall_combined_score": round(overall_combined, 3),
        "overall_grounding_rate": round(overall_grounding, 3),
        "num_pmcids": len(pmcid_summaries),
        "num_total_associations": len(all_combined_scores),
        "per_pmcid": pmcid_summaries,
        "details": all_results,
    }

    if save_results:
        RESULTS_DIR.mkdir(parents=True, exist_ok=True)
        output_path = RESULTS_DIR / f"{run_name}_eval.json"
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(result, f, indent=2, ensure_ascii=False)
        logger.info(f"Saved evaluation results to {output_path}")

    return result
