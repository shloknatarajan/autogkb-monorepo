"""
Hybrid regex extraction + LLM filtering.

Uses v5 regex extraction for high-recall candidate generation, then filters
with an LLM to remove false positives.
"""

from pathlib import Path

from pipeline.modules.variant_finding.utils import (
    extract_all_variants,
    extract_json_array,
    get_combined_text,
    load_prompts,
)
from shared.utils import call_llm

PROMPTS_FILE = Path(__file__).parent / "prompts" / "regex_llm_filter.yaml"


def regex_llm_filter_extract(
    pmcid: str,
    model: str = "claude-3-5-sonnet-20241022",
    prompt_version: str = "v1",
) -> list[str]:
    combined_text, _ = get_combined_text(pmcid)
    if not combined_text:
        return []

    # Step 1: Regex extraction
    regex_variants = extract_all_variants(combined_text)
    if not regex_variants:
        return []

    # Step 2: LLM filtering
    prompts = load_prompts(PROMPTS_FILE)
    prompt_config = prompts[prompt_version]
    variants_list = "\n".join([f"- {v}" for v in regex_variants])

    system_prompt = prompt_config["system"]
    user_prompt = prompt_config["user"].format(
        variants_list=variants_list, article_text=combined_text
    )

    response = call_llm(model, system_prompt, user_prompt)
    return extract_json_array(response)
