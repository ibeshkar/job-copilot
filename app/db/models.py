# What: the SQLite tables of the app (jobs, drafts, documents, usage, feedback, interviews).
# Why: the tracker board, draft history and cost tracking need data that survives restarts.
#      SQLModel gives us typed Python classes and safe, parameterised SQL (no SQL injection).
# Used by: app/db/repo.py (all reads and writes go through the repo, not these classes directly).

from datetime import UTC, datetime
from enum import StrEnum

from sqlmodel import Field, SQLModel


def utc_now() -> datetime:
    """Current time in UTC. Used as the default for timestamp columns."""
    return datetime.now(UTC)


class JobStatus(StrEnum):
    """The columns of the tracker board, in order."""

    SAVED = "saved"
    APPLIED = "applied"
    INTERVIEW = "interview"
    OFFER = "offer"
    REJECTED = "rejected"


class FeedbackTarget(StrEnum):
    """What a thumbs up/down rating is about."""

    FIT = "fit"
    LETTER = "letter"
    BULLETS = "bullets"
    INTERVIEW = "interview"


class Job(SQLModel, table=True):
    """One job the user is looking at or has applied to (one card on the board)."""

    id: int | None = Field(default=None, primary_key=True)
    title: str
    company: str
    url: str | None = None
    raw_text: str = ""
    posting_json: str | None = None  # JobPosting from the LLM, stored as JSON text
    # Stored as plain text (not a DB enum) so the value reads "applied" in any SQLite viewer.
    status: str = Field(default=JobStatus.SAVED.value, index=True)
    fit_score: int | None = None
    # The LangGraph thread for this job's workflow: f"job-{id}". Lets us resume after a restart.
    thread_id: str | None = Field(default=None, index=True)
    notes: str = ""
    next_action: str | None = None
    next_action_date: datetime | None = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class Draft(SQLModel, table=True):
    """One version of the CV bullets + cover letter for a job."""

    id: int | None = Field(default=None, primary_key=True)
    job_id: int = Field(foreign_key="job.id", index=True)
    version: int
    cv_bullets_json: str = "[]"  # list[str] stored as JSON text
    cover_letter: str = ""
    is_user_edited: bool = False
    created_at: datetime = Field(default_factory=utc_now)


class Document(SQLModel, table=True):
    """A file the user uploaded to the knowledge base (the chunks themselves live in Chroma)."""

    id: int | None = Field(default=None, primary_key=True)
    filename: str = Field(index=True, unique=True)
    doc_type: str  # e.g. "cv", "project", "brag_doc", "letter", "other"
    chunk_count: int = 0
    uploaded_at: datetime = Field(default_factory=utc_now)


class UsageLog(SQLModel, table=True):
    """Tokens and cost of one user action (e.g. "analyse_fit", "draft", "chat")."""

    id: int | None = Field(default=None, primary_key=True)
    action: str = Field(index=True)
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    created_at: datetime = Field(default_factory=utc_now)


class Feedback(SQLModel, table=True):
    """A thumbs up/down rating with an optional comment."""

    id: int | None = Field(default=None, primary_key=True)
    job_id: int | None = Field(default=None, foreign_key="job.id", index=True)
    target: str  # one of FeedbackTarget
    rating: int  # +1 = thumbs up, -1 = thumbs down
    comment: str = ""
    created_at: datetime = Field(default_factory=utc_now)


class InterviewSession(SQLModel, table=True):
    """One finished mock interview: questions, answers and scores."""

    id: int | None = Field(default=None, primary_key=True)
    job_id: int = Field(foreign_key="job.id", index=True)
    qa_json: str = "[]"
    overall_score: float | None = None
    created_at: datetime = Field(default_factory=utc_now)
