"""Annotation methods available to the API.

A method is an ``AnnotationExtractor`` subclass. Importing this module registers
every method the API can run; ``ANNOTATION_METHOD`` selects the default and a
request may name another one.
"""

import os

from annotation import (  # noqa: F401  (GroundedExtractor registers on import)
    AnnotationExtractor,
    DisplayAssociation,
    ExtractionOutput,
    GroundedExtractor,
    available_methods,
    get_extractor,
)
from annotation.base import ProgressCallback
from pipeline.modules.citations.citation_finder import CitationFinder
from pipeline.modules.sentence_generation.sentence_generator import SentenceGenerator
from pipeline.modules.variant_finding.utils import extract_all_variants

PIPELINE_MODEL: str = os.environ.get("PIPELINE_MODEL", "gpt-4o")
DEFAULT_ANNOTATION_METHOD: str = os.environ.get("ANNOTATION_METHOD", "grounded_v2")


class SentencePipelineExtractor(AnnotationExtractor):
    """Regex variant finding, then sentence generation, then citation finding.

    The sentence and citation modules read the article from the pipeline's
    ``data/articles/<identifier>.md``; the job writes it there before extraction.
    """

    name = "sentences_v5"
    description = (
        "Regex variant extraction, then per-variant sentence generation and a "
        "separate citation-finding call."
    )
    default_model = PIPELINE_MODEL

    def extract(
        self, markdown: str, identifier: str, progress: ProgressCallback
    ) -> ExtractionOutput:
        progress("extracting_variants", "Extracting genetic variants...")
        variants = extract_all_variants(markdown)

        progress("generating_sentences", "Generating association sentences...")
        sentences = SentenceGenerator(
            method="batch_judge_ask", model=self.model, prompt_version="v5"
        ).generate(identifier, variants)

        progress("finding_citations", "Finding supporting citations...")
        citations = CitationFinder(
            method="one_shot_citations", model=self.model, prompt_version="v2"
        ).find_citations(
            identifier,
            [
                {"variant": v, "sentence": s.sentence, "explanation": s.explanation}
                for v, sents in sentences.items()
                for s in sents
            ],
        )
        return ExtractionOutput(
            variants=list(sentences),
            associations=[
                DisplayAssociation(
                    variant=c.variant,
                    sentence=c.sentence,
                    explanation=c.explanation,
                    citations=c.citations,
                )
                for c in citations
            ]
        )


if DEFAULT_ANNOTATION_METHOD not in available_methods():
    # Fail at startup rather than on every request that omits a method.
    raise RuntimeError(
        f"ANNOTATION_METHOD={DEFAULT_ANNOTATION_METHOD!r} is not a registered "
        f"annotation method. Available: {', '.join(sorted(available_methods()))}"
    )


def resolve_method(name: str | None) -> str:
    """Return a registered method name, defaulting to the configured one."""
    method = name or DEFAULT_ANNOTATION_METHOD
    if method not in available_methods():
        known = ", ".join(sorted(available_methods()))
        raise ValueError(f"Unknown annotation method {method!r}. Available: {known}")
    return method


def describe_methods() -> list[dict]:
    return [
        {
            "name": name,
            "description": cls.description,
            "model": cls.default_model,
            "default": name == DEFAULT_ANNOTATION_METHOD,
        }
        for name, cls in sorted(available_methods().items())
    ]


__all__ = [
    "DEFAULT_ANNOTATION_METHOD",
    "SentencePipelineExtractor",
    "describe_methods",
    "get_extractor",
    "resolve_method",
]
