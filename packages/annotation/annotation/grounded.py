"""Entity-table extraction exposed through the common extractor interface."""

from annotation.base import (
    AnnotationExtractor,
    DisplayAssociation,
    ExtractionOutput,
    ProgressCallback,
)
from annotation.extractor import DEFAULT_MODEL, annotate_text
from annotation.models import GroundedAssociation


def _explanation(association: GroundedAssociation) -> str:
    return "; ".join(f"{stat.name}: {stat.value}" for stat in association.statistics)


class GroundedExtractor(AnnotationExtractor):
    name = "grounded_v2"
    description = (
        "One LLM call over the article with prefilled variant and drug lookups; "
        "every record carries verbatim source quotes."
    )
    default_model = DEFAULT_MODEL

    def extract(
        self, markdown: str, identifier: str, progress: ProgressCallback
    ) -> ExtractionOutput:
        progress("generating_sentences", "Extracting grounded associations...")
        result = annotate_text(markdown, model=self.model)
        return ExtractionOutput(
            associations=[
                DisplayAssociation(
                    variant=", ".join(v.name for v in association.variants),
                    sentence=association.render_sentence(),
                    explanation=_explanation(association),
                    citations=[item.quote for item in association.evidence],
                )
                for association in result.associations
            ],
            structured_associations=[
                association.model_dump() for association in result.associations
            ],
        )
