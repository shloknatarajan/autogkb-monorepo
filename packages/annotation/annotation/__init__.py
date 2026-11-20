"""Evidence-grounded pharmacogenomic annotation package."""

from annotation.base import (
    AnnotationExtractor,
    AnnotationRun,
    DisplayAssociation,
    ExtractionOutput,
    available_methods,
    get_extractor,
)
from annotation.extractor import annotate_text
from annotation.grounded import GroundedExtractor
from annotation.models import AnnotationResult, GroundedAssociation

__all__ = [
    "AnnotationExtractor",
    "AnnotationResult",
    "AnnotationRun",
    "DisplayAssociation",
    "ExtractionOutput",
    "GroundedAssociation",
    "GroundedExtractor",
    "annotate_text",
    "available_methods",
    "get_extractor",
]
