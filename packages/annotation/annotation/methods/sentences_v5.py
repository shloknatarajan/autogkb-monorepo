"""``sentences_v5``: the earlier multi-step pipeline behind the common interface.

The steps themselves live in the ``pipeline`` package; this module only runs
them in order and maps the result to the common display shape.
"""

import os

from annotation.base import (
    AnnotationExtractor,
    DisplayAssociation,
    ExtractionOutput,
    ProgressCallback,
)
from pipeline.modules.citations.citation_finder import CitationFinder
from pipeline.modules.sentence_generation.sentence_generator import SentenceGenerator
from pipeline.modules.variant_finding.utils import extract_all_variants

PIPELINE_MODEL: str = os.environ.get("PIPELINE_MODEL", "gpt-4o")


class SentencePipelineExtractor(AnnotationExtractor):
    """Regex variant finding, then sentence generation, then citation finding.

    The sentence and citation modules read the article from the pipeline's
    ``data/articles/<identifier>.md``; the caller writes it there before extraction.
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
            ],
        )
