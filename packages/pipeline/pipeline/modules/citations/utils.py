"""Shared utilities for citation finding experiments."""

import re

import yaml
from loguru import logger


def parse_citation_output(output: str) -> dict[int, list[str]]:
    """Parse LLM output into a dict mapping association index -> list of citations.

    Expected format:
        ASSOCIATION: 1
        CITATIONS:
        1. First citation sentence
        2. Second citation sentence
        3. Third citation sentence

        ASSOCIATION: 2
        CITATIONS:
        1. First citation sentence
        2. Second citation sentence
    """
    result: dict[int, list[str]] = {}

    # Split by ASSOCIATION: markers
    assoc_blocks = re.split(r"\n\s*ASSOCIATION:\s*", output)

    for block in assoc_blocks:
        if not block.strip():
            continue

        # First line should be association index, rest is citations
        lines = block.strip().split("\n")
        if not lines:
            continue

        assoc_line = lines[0].strip()
        # Remove "ASSOCIATION:" prefix if present (happens for first association in output)
        if assoc_line.upper().startswith("ASSOCIATION:"):
            assoc_line = assoc_line[12:].strip()

        # Parse the association index
        try:
            assoc_idx = int(assoc_line)
        except ValueError:
            logger.warning(f"Could not parse association index: {assoc_line}")
            continue

        # Find CITATIONS: section
        citations = []
        in_citations = False

        for line in lines[1:]:
            line = line.strip()
            if line.upper().startswith("CITATIONS:"):
                in_citations = True
                continue

            if in_citations and line:
                # Remove leading numbers like "1. " or "1) "
                citation = re.sub(r"^\d+[\.)]\s*", "", line)
                # Clean up common artifacts from LLM output
                citation = citation.strip().strip('"').strip("'").strip("\\")
                # Remove escaped quotes that may appear at start/end
                citation = re.sub(r"^[\\\"\']+|[\\\"\']+$", "", citation)
                if citation:
                    citations.append(citation)

        if citations:
            result[assoc_idx] = citations
            logger.debug(
                f"Parsed {len(citations)} citation(s) for association {assoc_idx}"
            )

    if not result:
        logger.warning("Failed to parse any citations from output")
        logger.debug(f"Output was: {output[:500]}...")

    return result


def parse_judge_output(output: str) -> dict[int, dict]:
    """Parse judge LLM output into association scores.

    Expected format:
        ASSOCIATION: 1
        SCORE: 85
        JUSTIFICATION: Citations provide strong evidence...

        ASSOCIATION: 2
        SCORE: 72
        JUSTIFICATION: Citations support the general association...
    """
    result: dict[int, dict] = {}

    # Split by ASSOCIATION: markers
    assoc_blocks = re.split(r"\n\s*ASSOCIATION:\s*", output)

    for block in assoc_blocks:
        if not block.strip():
            continue

        lines = block.strip().split("\n")
        if not lines:
            continue

        assoc_line = lines[0].strip()
        if assoc_line.upper().startswith("ASSOCIATION:"):
            assoc_line = assoc_line[12:].strip()

        try:
            assoc_idx = int(assoc_line)
        except ValueError:
            logger.warning(f"Could not parse association index: {assoc_line}")
            continue

        score = None
        justification = ""

        for line in lines[1:]:
            line = line.strip()
            if line.upper().startswith("SCORE:"):
                score_text = line.split(":", 1)[1].strip()
                try:
                    score = float(score_text)
                except ValueError:
                    logger.warning(f"Could not parse score: {score_text}")
            elif line.upper().startswith("JUSTIFICATION:"):
                justification = line.split(":", 1)[1].strip()
            elif justification:
                justification += " " + line

        if score is not None:
            result[assoc_idx] = {"score": score, "justification": justification.strip()}
            logger.debug(f"Parsed score {score} for association {assoc_idx}")

    if not result:
        logger.warning("Failed to parse any scores from judge output")
        logger.debug(f"Output was: {output[:500]}...")

    return result


def load_prompts(prompts_file) -> dict:
    """Load prompts from a YAML file."""
    with open(prompts_file) as f:
        return yaml.safe_load(f)
