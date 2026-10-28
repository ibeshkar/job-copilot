# What: every read and write to the app database (create/read/update/delete for each table).
# Why: UI pages, tools and graph nodes call these small functions instead of writing SQL.
#      This keeps database logic in one tested place and out of the Streamlit files.
# Used by: app/main.py (init_db), and later the board page, tracker tools, usage service, etc.

import json
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from functools import lru_cache
from typing import Any

from sqlalchemy import Engine
from sqlmodel import Session, SQLModel, create_engine, func, select

from app.config import get_settings
from app.db.models import (
    Document,
    Draft,
    Feedback,
    FeedbackTarget,
    InterviewSession,
    Job,
    JobStatus,
    UsageLog,
    utc_now,
)
from app.utils.errors import NotFoundError

# Fields the user (or the copilot's update_tracker tool) may change on a job.
_EDITABLE_JOB_FIELDS = {
    "title",
    "company",
    "url",
    "raw_text",
    "posting_json",
    "status",
    "fit_score",
    "notes",
    "next_action",
    "next_action_date",
}


# ---------------------------------------------------------------- engine and sessions


@lru_cache
def get_engine() -> Engine:
    """Return the app's SQLite engine (created once per process).

    `check_same_thread=False` is needed because Streamlit may run code in different threads.
    """
    return create_engine(get_settings().app_db_url, connect_args={"check_same_thread": False})


def init_db(engine: Engine | None = None) -> None:
    """Create all tables that don't exist yet. Safe to call on every app start."""
    SQLModel.metadata.create_all(engine or get_engine())


@contextmanager
def session_scope(engine: Engine | None = None) -> Iterator[Session]:
    """Open a database session, commit on success and roll back on error.

    Example:
        with session_scope() as s:
            create_job(s, title="AI Engineer", company="Acme")
    """
    with Session(engine or get_engine(), expire_on_commit=False) as session:
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise


# ---------------------------------------------------------------- jobs


def _validate_status(status: str) -> str:
    """Raise ValueError if `status` is not a board column."""
    try:
        return JobStatus(status).value
    except ValueError as exc:
        allowed = ", ".join(s.value for s in JobStatus)
        raise ValueError(f"Unknown status {status!r}. Allowed: {allowed}") from exc


def create_job(
    session: Session,
    title: str,
    company: str,
    url: str | None = None,
    raw_text: str = "",
    status: str = JobStatus.SAVED,
) -> Job:
    """Insert a job and give it its LangGraph thread id (`job-<id>`).

    Returns:
        The saved job, with `id` and `thread_id` filled in.
    """
    job = Job(
        title=title,
        company=company,
        url=url,
        raw_text=raw_text,
        status=_validate_status(status),
    )
    session.add(job)
    session.flush()  # assigns job.id without ending the transaction
    job.thread_id = f"job-{job.id}"
    session.commit()
    session.refresh(job)
    return job


def get_job(session: Session, job_id: int) -> Job:
    """Return one job. Raises NotFoundError if it doesn't exist."""
    job = session.get(Job, job_id)
    if job is None:
        raise NotFoundError(f"Job {job_id} not found")
    return job


def list_jobs(session: Session, status: str | None = None) -> list[Job]:
    """Return jobs, newest first, optionally only those with the given status."""
    query = select(Job).order_by(Job.created_at.desc(), Job.id.desc())
    if status is not None:
        query = query.where(Job.status == _validate_status(status))
    return list(session.exec(query).all())


def update_job(session: Session, job_id: int, **fields: Any) -> Job:
    """Change some fields of a job, e.g. `update_job(s, 3, status="applied", notes="...")`.

    Raises:
        NotFoundError: the job doesn't exist.
        ValueError: an unknown field or an invalid status was given.
    """
    unknown = set(fields) - _EDITABLE_JOB_FIELDS
    if unknown:
        raise ValueError(f"Cannot update fields: {sorted(unknown)}")
    if "status" in fields:
        fields["status"] = _validate_status(fields["status"])

    job = get_job(session, job_id)
    for key, value in fields.items():
        setattr(job, key, value)
    job.updated_at = utc_now()
    session.add(job)
    session.commit()
    session.refresh(job)
    return job


def delete_job(session: Session, job_id: int) -> None:
    """Delete a job and everything that belongs to it (drafts, feedback, interviews)."""
    job = get_job(session, job_id)
    for model in (Draft, Feedback, InterviewSession):
        for row in session.exec(select(model).where(model.job_id == job_id)).all():
            session.delete(row)
    session.delete(job)
    session.commit()


# ---------------------------------------------------------------- drafts


def add_draft(
    session: Session,
    job_id: int,
    cv_bullets: list[str],
    cover_letter: str,
    is_user_edited: bool = False,
) -> Draft:
    """Save a new draft version for a job. Versions count up from 1 per job."""
    get_job(session, job_id)  # fail early with NotFoundError
    current_max = session.exec(select(func.max(Draft.version)).where(Draft.job_id == job_id)).one()
    draft = Draft(
        job_id=job_id,
        version=(current_max or 0) + 1,
        cv_bullets_json=json.dumps(cv_bullets, ensure_ascii=False),
        cover_letter=cover_letter,
        is_user_edited=is_user_edited,
    )
    session.add(draft)
    session.commit()
    session.refresh(draft)
    return draft


def list_drafts(session: Session, job_id: int) -> list[Draft]:
    """Return all drafts of a job, oldest version first."""
    query = select(Draft).where(Draft.job_id == job_id).order_by(Draft.version)
    return list(session.exec(query).all())


def latest_draft(session: Session, job_id: int) -> Draft | None:
    """Return the newest draft of a job, or None if there is none yet."""
    query = select(Draft).where(Draft.job_id == job_id).order_by(Draft.version.desc())
    return session.exec(query).first()


# ---------------------------------------------------------------- documents


def upsert_document(session: Session, filename: str, doc_type: str, chunk_count: int) -> Document:
    """Record an uploaded file. Re-uploading the same filename replaces the old record."""
    doc = session.exec(select(Document).where(Document.filename == filename)).first()
    if doc is None:
        doc = Document(filename=filename, doc_type=doc_type, chunk_count=chunk_count)
    else:
        doc.doc_type = doc_type
        doc.chunk_count = chunk_count
        doc.uploaded_at = utc_now()
    session.add(doc)
    session.commit()
    session.refresh(doc)
    return doc


def list_documents(session: Session) -> list[Document]:
    """Return all knowledge-base documents, sorted by filename."""
    return list(session.exec(select(Document).order_by(Document.filename)).all())


def delete_document(session: Session, filename: str) -> None:
    """Remove a document record. Raises NotFoundError if it doesn't exist."""
    doc = session.exec(select(Document).where(Document.filename == filename)).first()
    if doc is None:
        raise NotFoundError(f"Document {filename!r} not found")
    session.delete(doc)
    session.commit()


# ---------------------------------------------------------------- usage


def log_usage(
    session: Session,
    action: str,
    model: str,
    input_tokens: int,
    output_tokens: int,
    cost_usd: float,
) -> UsageLog:
    """Save the token count and cost of one action."""
    row = UsageLog(
        action=action,
        model=model,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cost_usd=cost_usd,
    )
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


def usage_by_action(session: Session, since: datetime | None = None) -> dict[str, dict[str, float]]:
    """Sum tokens and cost per action, e.g. `{"draft": {"tokens": 1200, "cost_usd": 0.001}}`."""
    query = select(
        UsageLog.action,
        func.sum(UsageLog.input_tokens + UsageLog.output_tokens),
        func.sum(UsageLog.cost_usd),
    ).group_by(UsageLog.action)
    if since is not None:
        query = query.where(UsageLog.created_at >= since)
    return {
        action: {"tokens": int(tokens or 0), "cost_usd": float(cost or 0.0)}
        for action, tokens, cost in session.exec(query).all()
    }


# ---------------------------------------------------------------- feedback and interviews


def add_feedback(
    session: Session,
    target: str,
    rating: int,
    comment: str = "",
    job_id: int | None = None,
) -> Feedback:
    """Save a thumbs up (+1) or thumbs down (-1) rating."""
    target = FeedbackTarget(target).value  # raises ValueError for unknown targets
    if rating not in (1, -1):
        raise ValueError("rating must be +1 or -1")
    row = Feedback(job_id=job_id, target=target, rating=rating, comment=comment)
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


def list_feedback(session: Session, target: str | None = None) -> list[Feedback]:
    """Return feedback, newest first, optionally for one target only."""
    query = select(Feedback).order_by(Feedback.created_at.desc(), Feedback.id.desc())
    if target is not None:
        query = query.where(Feedback.target == FeedbackTarget(target).value)
    return list(session.exec(query).all())


def add_interview_session(
    session: Session, job_id: int, qa: list[dict[str, Any]], overall_score: float | None
) -> InterviewSession:
    """Save a finished mock interview."""
    get_job(session, job_id)
    row = InterviewSession(
        job_id=job_id,
        qa_json=json.dumps(qa, ensure_ascii=False),
        overall_score=overall_score,
    )
    session.add(row)
    session.commit()
    session.refresh(row)
    return row
