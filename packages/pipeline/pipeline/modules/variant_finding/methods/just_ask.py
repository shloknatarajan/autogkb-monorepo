"""
LLM-based variant extraction.

Asks an LLM to directly extract pharmacogenetic variants from article text.
"""

from pathlib import Path

from generation.modules.variant_finding.utils import extract_json_array, load_prompts
from shared.utils import call_llm, get_methods_and_conclusions_text

PROMPTS_FILE = Path(__file__).parent / "prompts" / "just_ask.yaml"


def just_ask_extract(
    pmcid: str,
    model: str = "claude-3-5-sonnet-20241022",
    prompt_version: str = "v1",
) -> list[str]:
    text = get_methods_and_conclusions_text(pmcid)
    if not text:
        return []

    prompts = load_prompts(PROMPTS_FILE)
    prompt_config = prompts[prompt_version]
    user_prompt = prompt_config["user"].format(article_text=text)
    system_prompt = prompt_config["system"]

    response = call_llm(model, system_prompt, user_prompt)
    return extract_json_array(response)
