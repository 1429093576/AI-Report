"""Error hook for the pipeline."""

from __future__ import annotations

from src.harness import PipelineContext


def run(context: PipelineContext, error: Exception) -> PipelineContext:
    """Store error details for downstream logging or inspection."""

    context.set(
        "last_error",
        {
            "type": error.__class__.__name__,
            "message": str(error),
        },
    )
    return context
