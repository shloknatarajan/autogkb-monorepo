import re
from pathlib import Path
from typing import Optional

from litellm import completion
from loguru import logger

# Calculate repository root (shared/utils.py → repo root)
ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"


def get_markdown_text(pmcid: str) -> str:
    """
    Retrieves the markdown text in string format of an article given its PubMed Central ID (PMCID).

    Args:
        pmcid: PMCID of the article

    Returns:
        The text content of the markdown file as a string
    """
    markdown_path = ROOT / "data" / "articles" / f"{pmcid}.md"
    try:
        with open(markdown_path, "r") as f:
            return f.read()
    except FileNotFoundError:
        logger.error(f"Article {pmcid} not found at {markdown_path}")
        return ""


def _extract_section(markdown_text: str, section_patterns: list[str]) -> str:
    """
    Extract a section from markdown text based on header patterns.

    Args:
        markdown_text: The full markdown text
        section_patterns: List of regex patterns to match section headers

    Returns:
        The extracted section text, or empty string if not found
    """
    combined_pattern = "|".join(section_patterns)
    # Match section headers (## or ### followed by optional numbering and the section name)
    header_pattern = rf"^(#{{2,3}})\s*(?:\d+\.?\s*)?({combined_pattern})\s*$"

    matches = list(
        re.finditer(header_pattern, markdown_text, re.MULTILINE | re.IGNORECASE)
    )

    if not matches:
        return ""

    sections = []
    for match in matches:
        start = match.end()
        # Find the next section header of same or higher level
        header_level = len(match.group(1))
        next_header_pattern = rf"^#{{1,{header_level}}}\s+\S"
        next_match = re.search(next_header_pattern, markdown_text[start:], re.MULTILINE)

        if next_match:
            end = start + next_match.start()
        else:
            end = len(markdown_text)

        section_text = markdown_text[start:end].strip()
        if section_text:
            sections.append(f"## {match.group(2).title()}\n\n{section_text}")

    return "\n\n".join(sections)


def get_methods_and_results_text(pmcid: str) -> str:
    """
    Retrieves only the methods and results sections from an article's markdown.
    Excludes Discussion/Conclusions to avoid speculative or interpretive content.

    Args:
        pmcid: PMCID of the article

    Returns:
        The methods and results sections concatenated as a string
    """
    markdown_text = get_markdown_text(pmcid)
    if not markdown_text:
        return ""

    methods_patterns = [
        r"materials?\s+and\s+methods?",
        r"methods?",
        r"patients?\s+and\s+methods?",
        r"study\s+design",
        r"experimental\s+procedures?",
    ]

    results_patterns = [
        r"results?",
        r"findings?",
    ]

    methods_text = _extract_section(markdown_text, methods_patterns)
    results_text = _extract_section(markdown_text, results_patterns)

    result_parts = []
    if methods_text:
        result_parts.append(methods_text)
    if results_text:
        result_parts.append(results_text)

    return "\n\n".join(result_parts)


def get_methods_and_conclusions_text(pmcid: str) -> str:
    """
    Retrieves the methods, results, and conclusions sections from an article's markdown.

    Args:
        pmcid: PMCID of the article

    Returns:
        The methods, results, and conclusions sections concatenated as a string
    """
    markdown_text = get_markdown_text(pmcid)
    if not markdown_text:
        return ""

    # Patterns for methods section (various naming conventions)
    methods_patterns = [
        r"materials?\s+and\s+methods?",
        r"methods?",
        r"patients?\s+and\s+methods?",
        r"study\s+design",
        r"experimental\s+procedures?",
    ]

    # Patterns for results section (various naming conventions)
    results_patterns = [
        r"results?",
        r"findings?",
        r"results?\s+and\s+discussion",
    ]

    # Patterns for conclusions section
    conclusions_patterns = [
        r"conclusions?",
        r"discussion",
        r"discussion\s+and\s+conclusions?",
        r"summary",
    ]

    methods_text = _extract_section(markdown_text, methods_patterns)
    results_text = _extract_section(markdown_text, results_patterns)
    conclusions_text = _extract_section(markdown_text, conclusions_patterns)

    result_parts = []
    if methods_text:
        result_parts.append(methods_text)
    if results_text:
        result_parts.append(results_text)
    if conclusions_text:
        result_parts.append(conclusions_text)

    return "\n\n".join(result_parts)


def normalize_model_name(model: str) -> str:
    """
    Normalize model name to include provider prefix for litellm.

    Args:
        model: Model identifier (e.g., "claude-3-5-sonnet", "gpt-4o", "gemini-2.0-flash")

    Returns:
        Model name with provider prefix if needed (e.g., "anthropic/claude-3-5-sonnet")
    """
    # If already has provider prefix, return as-is
    if "/" in model:
        return model

    # Add provider prefix based on model name pattern
    if model.startswith("claude"):
        return f"anthropic/{model}"
    elif model.startswith("gpt") or model.startswith("o1") or model.startswith("o3"):
        return f"openai/{model}"
    elif model.startswith("gemini"):
        return f"google/{model}"
    else:
        # Return as-is and let litellm handle it
        return model


def call_llm(
    model: str,
    system_prompt: str,
    user_prompt: str,
    temperature: Optional[float] = 0,
    **kwargs,
) -> str:
    """
    Call LLM using litellm with proper error handling and model-specific configurations.

    This centralized function handles:
    - Model name normalization (adding provider prefixes)
    - Temperature support (some models like gpt-5, o1, o3 don't support temperature=0)
    - Consistent error handling across experiments

    Args:
        model: Model identifier (e.g., "claude-3-5-sonnet-20241022", "gpt-4o", "gpt-5")
        system_prompt: System message
        user_prompt: User message
        temperature: Temperature for sampling (default: 0). Set to None to omit.
                    Will be automatically omitted for models that don't support it.
        **kwargs: Additional arguments to pass to litellm.completion()

    Returns:
        LLM response text

    Raises:
        Exception: Re-raises any litellm exceptions for caller to handle

    Examples:
        >>> response = call_llm("gpt-4o", "You are helpful", "Hello")
        >>> response = call_llm("claude-3-5-sonnet", "System", "User", temperature=0.7)
        >>> response = call_llm("gpt-5", "System", "User")  # Automatically handles no-temp
    """
    # Normalize model name to include provider prefix
    normalized_model = normalize_model_name(model)

    # Some models don't support temperature parameter or only support temperature=1
    # These include: o1-*, o3-*, gpt-5-*
    no_temp_models = (
        normalized_model.startswith("openai/o1")
        or normalized_model.startswith("openai/o3")
        or normalized_model.startswith("openai/gpt-5")
    )

    # Build completion kwargs
    completion_kwargs = {
        "model": normalized_model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        **kwargs,
    }

    # Add temperature only if supported and requested
    if not no_temp_models and temperature is not None:
        completion_kwargs["temperature"] = temperature

    logger.debug(
        f"Calling LLM: {normalized_model} (temp={temperature if not no_temp_models else 'N/A'})"
    )

    try:
        response = completion(**completion_kwargs)
        content = response.choices[0].message.content
        logger.debug(f"LLM response received ({len(content)} chars)")
        return content
    except Exception as e:
        logger.error(f"LLM call failed for {normalized_model}: {e}")
        raise
