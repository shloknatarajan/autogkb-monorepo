"""Evidence-grounded pharmacogenomic annotation package."""

from annotation.extractor import annotate_text
from annotation.models import AnnotationResult, GroundedAssociation

__all__ = ["AnnotationResult", "GroundedAssociation", "annotate_text"]
