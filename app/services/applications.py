# What: everything the Board and Job workspace pages do with jobs: add a job and run the fit
#       analysis (with progress messages), read where a job's workflow stands (including style
#       rules learned at acceptance), resume the three reviews (fit, claim check, drafts), draft
#       versions, ratings and export, change a job's board column, delete a job.
# Why: it implements the Streamlit + LangGraph interrupt pattern (PROJECT_PLAN.md §7) in one
#      testable place. Pages only draw widgets and call these functions; they never touch the
#      graph or the LLM directly. Every run is wrapped in `track_run(action)` for cost tracking
#      and LangSmith metadata.
# Used by: app/ui/pages/1_board.py, app/ui/pages/2_job_workspace.py, app/agent/deps.py
#          (save_analysis_to_job, save_draft_version), tests/test_applications_service.py.

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime, time
from pathlib import Path
from typing import Literal

from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Command

from app.agent.application_graph import get_application_graph, thread_config
from app.agent.interview_graph import interview_thread_id
from app.agent.nodes.evidence import MAX_REWRITES
from app.agent.state import (
    UNKNOWN_COMPANY,
    Claim,
    ClaimReviewDecision,
    CoverLetter,
    DraftReviewDecision,
    Drafts,
    FitReport,
    FitReviewDecision,
    JobPosting,
    Requirement,
    SalaryEstimate,
    UnsupportedStatement,
)
from app.config import PROJECT_ROOT, get_settings
from app.db import repo
from app.db.models import Draft, Job, JobStatus
from app.guards.input_guard import looks_like_url
from app.memory.store import get_profile
from app.services import export
from app.services.company import forget_research
from app.services.export import ExportFile, LetterBlock
from app.services.observability import track_run
from app.utils.errors import AppError, ConfigError, NotFoundError, friendly_message
from app.utils.logging import get_logger, log_exception

logger = get_logger(__name__)

ProgressFn = Callable[[str], None]
Stage = Literal[
    "not_started", "interrupted", "fit_review", "claim_check", "draft_review", "finished", "failed"
]
REVIEW_STAGES = ("fit_review", "claim_check", "draft_review")  # interrupt payload types

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
    claims: list[Claim] = field(default_factory=list)
    new_facts: list[str] = field(default_factory=list)
    drafts: Drafts | None = None
    user_edits: Drafts | None = None
    guard_issues: list[UnsupportedStatement] = field(default_factory=list)
    learned_rules: list[dict] = field(default_factory=list)  # [{"key", "rule"}] at acceptance


@dataclass
class DraftVersion:
    """One saved version of a job's drafts (a row of the Draft table)."""

    version: int
    drafts: Drafts
    is_user_edited: bool
    created_at: datetime


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
    notes: str = ""
    salary: SalaryEstimate | None = None  # the cached estimate, for the board badge


# ---------------------------------------------------------------- graph access


def get_graph() -> CompiledStateGraph:
    """The compiled application graph (built once per process). Tests replace this function."""
    return get_application_graph()


def job_config(job_id: int) -> dict:
    """The graph config (thread id) of one job's workflow run."""
    with repo.session_scope() as session:
        return thread_config(repo.get_job(session, job_id).thread_id)


# Shown in the progress box (app/ui/components.progress_status). A message ending in "…" says
# which step runs now; the others report a finished step. Steps without an entry are quick.
_RUNNING = {
    "input_guard": "Checking your input…",
    "ingest_job": "Reading the posting…",
    "extract_requirements": "Extracting the requirements…",
    "retrieve_evidence": "Searching your documents…",
    "assess_fit": "Scoring the fit…",
    "plan_claims": "Planning claims about you…",
    "remember_confirmed_facts": "Saving the facts you confirmed…",
    "draft_materials": "Writing the drafts…",
    "output_guard": "Fact-checking the drafts…",
    "learn_from_edits": "Learning from your edits…",
    "save_to_tracker": "Saving to your board…",
}
FIT_ANALYSIS_STEPS = 5  # input check, read posting, requirements, search documents, score
RECHECK_STEPS = 2  # search documents, score


def _running_message(node: str, state: dict) -> str | None:
    if node == "rewrite_queries":
        attempt = state.get("rag_iterations", 0) + 1
        # Same topic as the "Searched your documents" line: it's a retry, not a new step.
        return (
            "Searched your documents: nothing found for some requirements, trying other "
            f"search words ({attempt}/{MAX_REWRITES})…"
        )
    return _RUNNING.get(node)


def _progress_message(node: str, update: dict | None, retries: int = 0) -> str | None:
    """The finished-step line for a node's update (None = nothing worth a line).

    Lines share a topic (the text before ": ") when a step repeats, so the progress box
    updates the line instead of adding one (see components.step_topic).
    """
    update = update or {}
    if update.get("errors"):
        return None
    if node == "input_guard":
        return "Checked your input"
    if node == "ingest_job" and update.get("job"):
        job = update["job"]
        if job.company == UNKNOWN_COMPANY:
            return f"Read the posting: {job.title} (the company isn't named; add it with ✏️ later)"
        return f"Read the posting: {job.title} at {job.company}"
    if node == "extract_requirements":
        return f"Extracted {len(update.get('requirements', []))} requirements"
    if node == "retrieve_evidence":
        found = sum(1 for chunks in (update.get("evidence") or {}).values() if chunks)
        after = f" (after retry {retries}/{MAX_REWRITES})" if retries else ""
        return f"Searched your documents: evidence for {found} requirements{after}"
    if node == "assess_fit" and update.get("fit"):
        return f"Scored the fit: {update['fit'].score}/100"
    if node == "plan_claims":
        claims = update.get("claims", [])
        backed = sum(1 for c in claims if c.supported)
        return f"Planned {len(claims)} claims about you ({backed} backed by your documents)"
    if node == "remember_confirmed_facts" and update.get("new_facts"):
        return f"Remembered {len(update['new_facts'])} fact(s) you confirmed"
    if node == "draft_materials":
        return "Wrote the drafts"
    if node == "output_guard":
        issues = update.get("guard_issues", [])
        if not issues:
            return "Fact check: passed, every statement is backed by an approved claim"
        return f"Fact check: found {len(issues)} unsupported statement(s)"
    if node == "learn_from_edits" and update.get("learned_rules"):
        return f"Learned {len(update['learned_rules'])} style rule(s) from your edits"
    if node == "save_to_tracker":
        return "Saved to your board"
    return None


def _stream(
    payload: object, config: dict, on_progress: ProgressFn | None, action: str, job_id: int
) -> None:
    """Run the graph until it pauses or ends, reporting each step as it starts and finishes.

    `action` names the run for cost tracking and LangSmith (e.g. "analyse_fit", "draft").
    """
    retries = 0  # search rewrites so far, shown on the "Searched your documents" line
    with track_run(action, job_id=job_id):
        for mode, chunk in get_graph().stream(payload, config, stream_mode=["tasks", "updates"]):
            if mode == "tasks":
                # A task event with "input" is a step starting (the finish event has "result").
                starting = "input" in chunk
                message = starting and _running_message(chunk["name"], chunk["input"] or {})
                if message and on_progress:
                    on_progress(message)
                continue
            for node, values in chunk.items():
                if node == "__interrupt__":
                    continue
                if node == "rewrite_queries":
                    retries = (values or {}).get("rag_iterations", retries)
                message = _progress_message(node, values, retries)
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
        fit_review / claim_check / draft_review: paused, waiting for the user's review.
        interrupted: stopped mid-way (e.g. the AI service failed); can be retried.
        failed: stopped with a user-facing error (e.g. not a job posting).
        finished: ran to the end.
    """
    state = get_graph().get_state(job_config(job_id))
    values = state.values or {}
    pending = [i.value for task in state.tasks for i in task.interrupts]

    if not values:
        stage: Stage = "not_started"
    elif values.get("errors"):
        stage = "failed"
    elif review := next(
        (p["type"] for p in pending if isinstance(p, dict) and p.get("type") in REVIEW_STAGES),
        None,
    ):
        stage = review  # the interrupt payload's type says which review the UI must show
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
        claims=values.get("claims", []),
        new_facts=values.get("new_facts", []),
        drafts=values.get("drafts"),
        user_edits=values.get("user_edits"),
        guard_issues=values.get("guard_issues", []),
        learned_rules=values.get("learned_rules", []),
    )


def save_analysis_to_job(job_id: int, job: JobPosting, fit: FitReport | None) -> None:
    """Copy the posting and fit score onto the job's row, so the board card shows them.

    A title or company the user corrected (`rename_job`) differs from the one the last analysis
    saved; it is kept, so a later step of the workflow doesn't undo the correction.
    """
    with repo.session_scope() as session:
        row = repo.get_job(session, job_id)
        saved = JobPosting.model_validate_json(row.posting_json) if row.posting_json else None
        repo.update_job(
            session,
            job_id,
            title=row.title if saved and row.title != saved.title else job.title,
            company=row.company if saved and row.company != saved.company else job.company,
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
    workspace offers "🔁 Retry".

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
        _stream(
            {"raw_input": raw, "job_id": job.id},
            thread_config(job.thread_id),
            on_progress,
            "analyse_fit",
            job.id,
        )
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
        config = job_config(job_id)
        if get_graph().get_state(config).values:
            payload: object = None  # None = "continue from the last checkpoint"
        else:
            with repo.session_scope() as session:
                row = repo.get_job(session, job_id)
            payload = {"raw_input": row.url or row.raw_text, "job_id": job_id}
        _stream(payload, config, on_progress, "retry", job_id)
    except Exception as exc:
        log_exception(logger, "Retry failed", job_id=job_id)
        return RunResult(ok=False, job_id=job_id, message=friendly_message(exc))
    _sync_job(job_id)
    return RunResult(ok=True, job_id=job_id)


def _resume(
    job_id: int,
    stage: Stage,
    make_payload: Callable[[], dict],
    on_progress: ProgressFn | None,
    action: str,
) -> RunResult:
    """Resume a graph paused at `stage` with the user's answer (the interrupt pattern, §7).

    `make_payload` builds and validates the answer; a validation error becomes a failed result.
    `action` names the run for cost tracking, e.g. "draft".
    """
    try:
        _require_llm_key()
        payload = make_payload()
        if get_workflow_view(job_id).stage != stage:
            return RunResult(
                ok=False, job_id=job_id, message="This job isn't waiting for this review anymore."
            )
        _stream(Command(resume=payload), job_config(job_id), on_progress, action, job_id)
    except AppError as exc:
        return RunResult(ok=False, job_id=job_id, message=exc.user_message)
    except Exception as exc:
        log_exception(logger, "Resume failed", job_id=job_id, stage=stage)
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
        action: "continue" (go on to the claim check), "stop" or "add_context".
        text: the extra context, for "add_context".
    """
    return _resume(
        job_id,
        "fit_review",
        lambda: FitReviewDecision(action=action, text=text).model_dump(),
        on_progress,
        # Each choice leads to different LLM work: re-scoring, planning claims, or none.
        {"add_context": "analyse_fit", "continue": "plan_claims"}.get(action, "fit_review"),
    )


def submit_claim_review(
    job_id: int, decisions: list[dict], on_progress: ProgressFn | None = None
) -> RunResult:
    """Resume the claim check and write the drafts.

    Args:
        job_id: the job whose workflow is waiting at the claim check.
        decisions: one {"id": "C1", "action": "approve" | "edit" | "remove", "text": ...} per
            claim. Claims left out count as removed.
    """
    review = ClaimReviewDecision.model_validate({"decisions": decisions})
    if not any(d.action != "remove" for d in review.decisions):
        return RunResult(
            ok=False,
            job_id=job_id,
            message="Keep at least one claim, otherwise there is nothing true to write about.",
        )
    return _resume(job_id, "claim_check", review.model_dump, on_progress, "draft")


def submit_draft_review(
    job_id: int,
    action: str,
    *,
    edited: Drafts | None = None,
    section: str | None = None,
    instruction: str | None = None,
    on_progress: ProgressFn | None = None,
) -> RunResult:
    """Resume the draft review: accept the drafts (with the user's edits) or regenerate one
    section ("cover_letter" or "cv_bullets") with an optional instruction."""
    return _resume(
        job_id,
        "draft_review",
        lambda: DraftReviewDecision(
            action=action, edited=edited, section=section, instruction=instruction
        ).model_dump(mode="json"),
        on_progress,
        "redraft" if action == "regenerate" else "accept_drafts",
    )


# ---------------------------------------------------------------- drafts


def save_draft_version(job_id: int, drafts: Drafts, is_user_edited: bool = False) -> None:
    """Save drafts as the job's next version in the Draft table."""
    with repo.session_scope() as session:
        repo.add_draft(
            session,
            job_id,
            drafts.cv_bullets,
            drafts.cover_letter.model_dump_json(),
            is_user_edited=is_user_edited,
        )


def _version(row: Draft) -> DraftVersion:
    return DraftVersion(
        version=row.version,
        drafts=Drafts(
            cv_bullets=json.loads(row.cv_bullets_json),
            cover_letter=row.cover_letter,  # JSON, or plain text in versions saved before
            used_claim_ids=[],
        ),
        is_user_edited=row.is_user_edited,
        created_at=row.created_at,
    )


def list_draft_versions(job_id: int) -> list[DraftVersion]:
    """All saved versions of a job's drafts, newest first."""
    with repo.session_scope() as session:
        return [_version(row) for row in reversed(repo.list_drafts(session, job_id))]


def latest_drafts(job_id: int) -> Drafts | None:
    """The newest saved drafts of a job (what the Documents tab shows after acceptance)."""
    with repo.session_scope() as session:
        row = repo.latest_draft(session, job_id)
        return _version(row).drafts if row else None


def save_edited_drafts(job_id: int, drafts: Drafts) -> bool:
    """Save the user's edits as a new version. Returns False if nothing changed."""
    current = latest_drafts(job_id)
    if current is not None and (current.cv_bullets, current.cover_letter) == (
        drafts.cv_bullets,
        drafts.cover_letter,
    ):
        return False
    save_draft_version(job_id, drafts, is_user_edited=True)
    return True


def rate_drafts(job_id: int, target: str, rating: int, comment: str = "") -> None:
    """Save a 👍 (+1) / 👎 (-1) rating for the "letter" or the "bullets"."""
    with repo.session_scope() as session:
        repo.add_feedback(session, target, rating, comment, job_id=job_id)


def _complete_letter(job_id: int, letter: CoverLetter) -> CoverLetter:
    with repo.session_scope() as session:
        row = repo.get_job(session, job_id)
    return export.fill_missing(letter, row.title, row.company)


def cover_letter_blocks(job_id: int, letter: CoverLetter) -> list[LetterBlock]:
    """The letter's lines as the PDF shows them (for the preview on the Documents tab)."""
    return export.letter_blocks(_complete_letter(job_id, letter), get_profile())


def export_cover_letter(
    job_id: int, letter: CoverLetter, kind: Literal["pdf", "docx"]
) -> ExportFile:
    """The cover letter as a PDF or .docx download (or .txt if that export fails)."""
    return export.export_cover_letter(_complete_letter(job_id, letter), get_profile(), kind)


# ---------------------------------------------------------------- board


def list_board() -> dict[str, list[JobCard]]:
    """All jobs grouped by board column, in board order (Saved → … → Rejected)."""
    with repo.session_scope() as session:
        rows = repo.list_jobs(session)
        salaries = repo.salary_estimates(session)
    board: dict[str, list[JobCard]] = {status.value: [] for status in JobStatus}
    for row in rows:
        board[row.status].append(_card(row, salaries.get(row.id)))
    return board


def _salary(estimate_json: str | None) -> SalaryEstimate | None:
    try:
        return SalaryEstimate.model_validate_json(estimate_json) if estimate_json else None
    except ValueError:  # an older, unreadable cache entry: just no badge
        return None


def _card(row: Job, salary_json: str | None = None) -> JobCard:
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
        notes=row.notes,
        salary=_salary(salary_json),
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


def rename_job(job_id: int, title: str, company: str) -> bool:
    """Correct the job's title and company (e.g. when the posting didn't name the company).

    Empty values keep the current ones. Returns False if nothing changed. The new names show
    on the board, in company research, interview prep and the exported cover letter.
    """
    with repo.session_scope() as session:
        row = repo.get_job(session, job_id)
        title, company = title.strip() or row.title, company.strip() or row.company
        if (title, company) == (row.title, row.company):
            return False
        repo.update_job(session, job_id, title=title, company=company)
    return True


def save_notes(
    job_id: int, notes: str, next_action: str | None, next_action_date: date | None
) -> None:
    """Save the job's notes and next action (shown on its board card)."""
    due = datetime.combine(next_action_date, time()) if next_action_date else None
    with repo.session_scope() as session:
        repo.update_job(
            session,
            job_id,
            notes=notes.strip(),
            next_action=(next_action or "").strip() or None,
            next_action_date=due,
        )


def delete_job(job_id: int) -> None:
    """Delete a job, its drafts, its saved workflow state and mock interview, and its company
    research."""
    with repo.session_scope() as session:
        thread_id = repo.get_job(session, job_id).thread_id
        repo.delete_job(session, job_id)
    # Job runs and interviews share one checkpoint file, so one checkpointer deletes both.
    checkpointer = get_graph().checkpointer
    if checkpointer is not None and thread_id:
        checkpointer.delete_thread(thread_id)
        checkpointer.delete_thread(interview_thread_id(job_id))
    forget_research(job_id)


def sample_jobs() -> dict[str, str]:
    """The 3 sample postings as {label: text}, for the "try a sample job" picker."""
    labels = {"01": "Good fit", "02": "Partial fit", "03": "Poor fit"}
    jobs = {}
    for path in sorted(Path(SAMPLE_JOBS_DIR).glob("*.md")):
        title = path.read_text().splitlines()[0].lstrip("# ").strip()
        jobs[f"{labels.get(path.name[:2], 'Sample')}: {title}"] = path.read_text()
    return jobs
