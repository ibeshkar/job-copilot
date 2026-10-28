# What: Pydantic models for data that flows through the agent (PROJECT_PLAN.md §5.1).
# Why: LLM answers must come back as typed objects (structured output), never as free text we
#      parse with regex. Shared models also give the UI, the graphs and the tests one vocabulary.
# Used by: app/rag/retriever.py (Evidence), app/memory/store.py (StyleRule); Phase 3 adds the
#          job, requirement and fit models plus the graph state.

from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, Field


class Evidence(BaseModel):
    """A piece of the user's own documents that supports (or doesn't) a requirement."""

    chunk_id: str = Field(description="Id of the knowledge-base chunk, e.g. 'cv.md#3'")
    doc_name: str = Field(description="File the chunk comes from, shown as the citation")
    snippet: str = Field(description="The chunk text, at most 300 characters")
    relevance: float = Field(ge=0.0, le=1.0, description="0 = unrelated, 1 = exact match")


class StyleRule(BaseModel):
    """A writing preference the app learned or the user stated, e.g. "Avoid 'passionate'"."""

    rule: str
    source: Literal["edit_diff", "explicit", "rating"]
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
