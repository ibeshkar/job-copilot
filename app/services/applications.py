# What: everything the Board and Job workspace pages do with jobs: add a job and run the fit
#       analysis (with progress messages), read where a job's workflow stands, resume the fit
#       review, change a job's board column, delete a job.
# Why: it implements the Streamlit + LangGraph interrupt pattern (PROJECT_PLAN.md §7) in one
#      testable place. Pages only draw widgets and call these functions; they never touch the
#      graph or the LLM directly.
# Used by: app/ui/pages/1_board.py, app/ui/pages/2_job_workspace.py, app/agent/deps.py
#          (save_analysis_to_job), tests/test_applications_service.py.

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Literal

from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Command

from app.agent.application_graph import get_application_graph, thread_config
from app.agent.state import FitReport, FitReviewDecision, JobPosting, Requirement
from app.config import PROJECT_ROOT, get_settings
from app.db import repo
from app.db.models import Job, JobStatus
from app.guards.input_guard import looks_like_url
from app.utils.errors import AppError, ConfigError, NotFoundError, friendly_message
from app.utils.logging import get_logger, log_exception

logger = get_logger(__name__)

ProgressFn = Callable[[str], None]
Stage = Literal["not_started", "interrupted", "fit_review", "finished", "failed"]

SAMPLE_JOBS_DIR = PROJECT_ROOT / "data" / "samples" / "jobs"
PLACEHOLDER_TITLE = "New job"
PLACEHOLDER_COMPANY = "Analysis not finished"


# ---------------------------------------------------------------- data for the pages


@dataclass
class RunResult:
    """Outcome of starting or resuming a run. `message` is safe to show the user."""

    ok: bool
    job_id: int | None = None
    message: str = ""


@dataclass
class WorkflowView:
    """Where one job's workflow stands, read from the checkpointer on every page load."""

    job_id: int
    stage: Stage
    job: JobPosting | None = None
    requirements: list[Requirement] = field(default_factory=list)
    fit: FitReport | None = None
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    review_action: str | None = None
    extra_context: str | None = None
    rag_iterations: int = 0


@dataclass
class JobCard:
    """What a board card shows."""

    id: int
    title: str
    company: str
    status: str
    fit_score: int | None
    next_action: str | None
    next_action_date: datetime | None
    stage: Stage
    review_action: str | None


# ---------------------------------------------------------------- graph access


def get_graph() -> CompiledStateGraph:
    """The compiled application graph (built once per process). Tests replace this function."""
    return get_application_graph()


def _config_for(job_id: int) -> dict:
    with repo.session_scope() as session:
        return thread_config(repo.get_job(session, job_id).thread_id)


# Shown in the st.status box while each step runs (§7: "Reading posting… Scoring fit…").
def _progress_message(node: str, update: dict | None) -> str | None:
    update = update or {}
    if update.get("errors"):
        return None
    if node == "input_guard":
        return "Checked your input"
    if node == "ingest_job" and update.get("job"):
        job = update["job"]
        return f"Read the posting: {job.title} at {job.company}"
    if node == "extract_requirements":
        return f"Extracted {len(update.get('requirements', []))} requirements"
    if node == "retrieve_evidence":
        found = sum(1 for chunks in (update.get("evidence") or {}).values() if chunks)
        return f"Searched your documents: evidence for {found} requirements so far"
    if node == "rewrite_queries":
        return "Nothing found for some requirements, trying other search words…"
    if node == "assess_fit" and update.get("fit"):
        return f"Scored the fit: {update['fit'].score}/100"
    if node == "save_to_tracker":
        return "Saved to your board"
    return None


def _stream(payload: object, config: dict, on_progress: ProgressFn | None) -> None:
    """Run the graph until it pauses or ends, reporting each finished step."""
    for update in get_graph().stream(payload, config, stream_mode="updates"):
        for node, values in update.items():
            if node == "__interrupt__":
                continue
            message = _progress_message(node, values)
            if message and on_progress:
                on_progress(message)


def _require_llm_key() -> None:
    if not get_settings().has_llm_key:
        raise ConfigError("OPENROUTER_API_KEY is not set")


# ---------------------------------------------------------------- reading state


def get_workflow_view(job_id: int) -> WorkflowView:
    """Read where a job's workflow stands. This is what makes a page refresh safe.

    Stages:
        not_started: no saved state (the run failed before its first step finished).
        fit_review: paused at the fit review, waiting for the user.
        interrupted: stopped mid-way (e.g. the AI service failed); can be retried.
        failed: stopped with a user-facing error (e.g. not a job posting).
        finished: ran to the end.
    """
    state = get_graph().get_state(_config_for(job_id))
    values = state.values or {}
    pending = [i.value for task in state.tasks for i in task.interrupts]

    if not values:
        stage: Stage = "not_started"
    elif values.get("errors"):
        stage = "failed"
    elif any(isinstance(p, dict) and p.get("type") == "fit_review" for p in pending):
        stage = "fit_review"
    elif state.next:
        stage = "interrupted"
    else:
        stage = "finished"

    return WorkflowView(
        job_id=job_id,
        stage=stage,
        job=values.get("job"),
        requirements=values.get("requirements", []),
        fit=values.get("fit"),
        warnings=values.get("warnings", []),
        errors=values.get("errors", []),
        review_action=values.get("review_action"),
        extra_context=values.get("extra_context"),
        rag_iterations=values.get("rag_iterations", 0),
    )


def save_analysis_to_job(job_id: int, job: JobPosting, fit: FitReport | None) -> None:
    """Copy the posting and fit score onto the job's row, so the board card shows them."""
    with repo.session_scope() as session:
        repo.update_job(
            session,
            job_id,
            title=job.title,
            company=job.company,
            url=job.source_url,
            raw_text=job.raw_text,
            posting_json=job.model_dump_json(),
            fit_score=fit.score if fit else None,
        )


def _sync_job(job_id: int) -> WorkflowView:
    view = get_workflow_view(job_id)
    if view.job is not None:
        save_analysis_to_job(job_id, view.job, view.fit)
    return view


# ---------------------------------------------------------------- actions


def add_job(raw_input: str, on_progress: ProgressFn | None = None) -> RunResult:
    """Create a job card and run the fit analysis until it pauses at the fit review.

    If the input is rejected (too short, not a job posting, unreadable link), the card is
    deleted again and the reason is returned, so the user can fix the input. If the run
    breaks for a technical reason (e.g. the AI service is down), the card stays and the
    workspace offers "Retry analysis".

    Args:
        raw_input: a job URL or the pasted job text.
        on_progress: called with a short message after each step (for `st.status`).
    """
    try:
        _require_llm_key()
    except ConfigError as exc:
        return RunResult(ok=False, message=exc.user_message)

    raw = raw_input.strip()
    is_url = looks_like_url(raw)
    with repo.session_scope() as session:
        job = repo.create_job(
            session,
            title=PLACEHOLDER_TITLE,
            company=PLACEHOLDER_COMPANY,
            url=raw if is_url else None,
            raw_text="" if is_url else raw,
        )

    try:
        _stream({"raw_input": raw, "job_id": job.id}, thread_config(job.thread_id), on_progress)
    except Exception as exc:
        log_exception(logger, "Analysis failed", job_id=job.id, thread_id=job.thread_id)
        return RunResult(ok=False, job_id=job.id, message=friendly_message(exc))

    view = get_workflow_view(job.id)
    if view.errors:
        delete_job(job.id)
        return RunResult(ok=False, message=view.errors[0])
    _sync_job(job.id)
    return RunResult(ok=True, job_id=job.id)


def retry_analysis(job_id: int, on_progress: ProgressFn | None = None) -> RunResult:
    """Continue a run that broke mid-way, from the last saved step (or from the start)."""
    try:
        _require_llm_key()
        config = _config_for(job_id)
        if get_graph().get_state(config).values:
            payload: object = None  # None = "continue from the last checkpoint"
        else:
            with repo.session_scope() as session:
                row = repo.get_job(session, job_id)
            payload = {"raw_input": row.url or row.raw_text, "job_id": job_id}
        _stream(payload, config, on_progress)
    except Exception as exc:
        log_exception(logger, "Retry failed", job_id=job_id)
        return RunResult(ok=False, job_id=job_id, message=friendly_message(exc))
    _sync_job(job_id)
    return RunResult(ok=True, job_id=job_id)


def submit_fit_review(
    job_id: int,
    action: str,
    text: str | None = None,
    on_progress: ProgressFn | None = None,
) -> RunResult:
    """Resume the paused graph with the user's fit-review decision.

    Args:
        job_id: the job whose workflow is waiting at the fit review.
        action: "continue", "stop" or "add_context".
        text: the extra context, for "add_context".
    """
    try:
        _require_llm_key()
        decision = FitReviewDecision(action=action, text=text)
        if get_workflow_view(job_id).stage != "fit_review":
            return RunResult(
                ok=False, job_id=job_id, message="This job isn't waiting for a review anymore."
            )
        _stream(Command(resume=decision.model_dump()), _config_for(job_id), on_progress)
    except AppError as exc:
        return RunResult(ok=False, job_id=job_id, message=exc.user_message)
    except Exception as exc:
        log_exception(logger, "Fit review failed", job_id=job_id)
        return RunResult(ok=False, job_id=job_id, message=friendly_message(exc))
    _sync_job(job_id)
    return RunResult(ok=True, job_id=job_id)


# ---------------------------------------------------------------- board


def list_board() -> dict[str, list[JobCard]]:
    """All jobs grouped by board column, in board order (Saved → … → Rejected)."""
    with repo.session_scope() as session:
        rows = repo.list_jobs(session)
    board: dict[str, list[JobCard]] = {status.value: [] for status in JobStatus}
    for row in rows:
        board[row.status].append(_card(row))
    return board


def _card(row: Job) -> JobCard:
    view = get_workflow_view(row.id)
    return JobCard(
        id=row.id,
        title=row.title,
        company=row.company,
        status=row.status,
        fit_score=row.fit_score,
        next_action=row.next_action,
        next_action_date=row.next_action_date,
        stage=view.stage,
        review_action=view.review_action,
    )


def list_job_choices() -> list[tuple[int, str]]:
    """(id, "Title · Company") for every job, newest first, for a job picker."""
    with repo.session_scope() as session:
        return [(j.id, f"{j.title} · {j.company}") for j in repo.list_jobs(session)]


def get_job_summary(job_id: int) -> JobCard | None:
    """The board card data for one job, or None if it was deleted."""
    with repo.session_scope() as session:
        try:
            row = repo.get_job(session, job_id)
        except NotFoundError:
            return None
    return _card(row)


def change_status(job_id: int, status: str) -> None:
    """Move a job to another board column."""
    with repo.session_scope() as session:
        repo.update_job(session, job_id, status=status)


def delete_job(job_id: int) -> None:
    """Delete a job, its drafts and its saved workflow state."""
    with repo.session_scope() as session:
        thread_id = repo.get_job(session, job_id).thread_id
        repo.delete_job(session, job_id)
    checkpointer = get_graph().checkpointer
    if checkpointer is not None and thread_id:
        checkpointer.delete_thread(thread_id)


def sample_jobs() -> dict[str, str]:
    """The 3 sample postings as {label: text}, for the "try a sample job" picker."""
    labels = {"01": "Good fit", "02": "Partial fit", "03": "Poor fit"}
    jobs = {}
    for path in sorted(Path(SAMPLE_JOBS_DIR).glob("*.md")):
        title = path.read_text().splitlines()[0].lstrip("# ").strip()
        jobs[f"{labels.get(path.name[:2], 'Sample')}: {title}"] = path.read_text()
    return jobs
