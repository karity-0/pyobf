"""Python source analysis and transformation infrastructure."""

from .pipeline import Pipeline, PipelineResult
from .source import SourceDocument

__version__ = "0.2.0"

__all__ = ["Pipeline", "PipelineResult", "SourceDocument"]
