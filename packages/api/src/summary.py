"""Article summary generation.

The summary is its own step with its own model. It runs after whichever
annotation method produced the associations and does not depend on that method.
"""

import os

from pipeline.modules.summary.summary_generator import SummaryGenerator

SUMMARY_MODEL: str = os.environ.get("SUMMARY_MODEL", "gpt-6-astra")
SUMMARY_METHOD = "basic_summary"
SUMMARY_PROMPT_VERSION = "v3"


def generate_summary(identifier: str, associations: list[dict]) -> str:
    """Summarise an article from its display associations.

    ``associations`` are ``{variant, sentence, explanation, citations}`` dicts.
    The generator reads the article text from the pipeline's
    ``data/articles/<identifier>.md``.
    """
    sentences_by_variant: dict[str, list[str]] = {}
    for association in associations:
        sentences_by_variant.setdefault(association["variant"], []).append(
            association["sentence"]
        )
    summary = SummaryGenerator(
        method=SUMMARY_METHOD, model=SUMMARY_MODEL, prompt_version=SUMMARY_PROMPT_VERSION
    ).generate(
        identifier,
        [
            {"variant": variant, "sentences": sentences}
            for variant, sentences in sentences_by_variant.items()
        ],
        {identifier: associations},
    )
    return summary.model_dump().get("summary", "")
