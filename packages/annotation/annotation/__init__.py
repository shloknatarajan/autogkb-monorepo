"""Evidence-grounded pharmacogenomic annotation package."""

from annotation.base import (
    AnnotationExtractor,
    AnnotationRun,
    DisplayAssociation,
    ExtractionOutput,
    available_methods,
    get_extractor,
)
from annotation.methods import GroundedExtractor, SentencePipelineExtractor
from annotation.methods.grounded_v2 import (
    AnnotationResult,
    GroundedAssociation,
    annotate_text,
)

__all__ = [
    "AnnotationExtractor",
    "AnnotationResult",
    "AnnotationRun",
    "DisplayAssociation",
    "ExtractionOutput",
    "GroundedAssociation",
    "GroundedExtractor",
    "SentencePipelineExtractor",
    "annotate_text",
    "available_methods",
    "get_extractor",
]
