# What: everything the "Interview prep" tab does: build the interview from the job's analysis
#       (requirements, gaps, company research), start it, send answers, end it early, retry a
#       step that broke off, read where it stands, and list past interviews.
# Why: the same interrupt pattern as the application workflow (PROJECT_PLAN.md §7): the page
#      reads the saved state on every rerun, so a refresh shows the same question again. Runs
#      are logged as the "interview" action for cost tracking and LangSmith.
# Used by: app/ui/pages/2_job_workspace.py, tests/test_interview.py.

import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal

from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Command

from app.agent.interview_graph import (
    NUM_QUESTIONS,
    get_interview_graph,
    interview_config,
    interview_thread_id,
)
from app.agent.state import InterviewQuestion, InterviewSetup, InterviewSummary, InterviewTurn
from app.config import get_settings
from app.db import repo
from app.guards.input_guard import check_interview_answer
from app.services import applications, company
from app.services.applications import ProgressFn, RunResult
from app.services.observability import track_run
from app.utils.errors import AppError, ConfigError, friendly_message
from app.utils.logging import get_logger, log_exception
from app.validation.validators import clean_multiline

logger = get_logger(__name__)

InterviewStage = Literal["unavailable", "not_started", "question", "interrupted", "finished"]
NEEDS_ANALYSIS = (
    "The mock interview is built from the fit analysis. Finish it on the Fit tab first."
)


@dataclass
class InterviewView:
    """Where a job's mock interview stands, read from the checkpointer on every page load."""

    job_id: int
    stage: InterviewStage
    turns: list[InterviewTurn] = field(default_factory=list)
    current: InterviewQuestion | None = None  # the question waiting for an answer
    number: int = 0  # the current question's number, 1-based
    total: int = NUM_QUESTIONS
    summary: InterviewSummary | None = None
    overall_score: float | None = None
    ended_early: bool = False


@dataclass
class PastInterview:
    """One finished interview from the InterviewSession table."""

    created_at: datetime
    overall_score: float | None
    answered: int


def get_graph() -> CompiledStateGraph:
    """The compiled interview graph (built once per process). Tests replace this function."""
    return get_interview_graph()


def build_setup(job_id: int) -> InterviewSetup | None:
    """The interview's material: the job, its requirements, the user's gaps (with the honest
    positioning from the fit analysis) and the company research, if any. None if the fit
    analysis isn't done yet."""
    view = applications.get_workflow_view(job_id)
    if view.job is None or view.fit is None:
        return None
    texts = {r.id: r.text for r in view.requirements}
    gaps = [
        f"{texts[m.requirement_id]} ({m.status})."
        + (f" Honest note: {m.honest_positioning}" if m.honest_positioning else "")
        for m in view.fit.matches
        if m.status != "met" and m.requirement_id in texts
    ]
    research = company.saved_research(job_id)
    notes = ""
    if research is not None:
        notes = "\n".join(
            [research.brief.summary, *research.brief.tech_stack, *research.brief.culture]
        )
    card = applications.get_job_summary(job_id)  # the names the user may have corrected
    return InterviewSetup(
        job_title=card.title,
        company=card.company,
        job_summary=view.job.summary,
        requirements=view.requirements,
        gaps=gaps,
        company_notes=notes,
    )


def get_view(job_id: int) -> InterviewView:
    """Read where the job's interview stands.

    Stages:
        unavailable: the fit analysis isn't finished, so there is nothing to ask about.
        not_started: no interview yet.
        question: waiting for the answer to `current`.
        interrupted: a step broke off (e.g. no internet); can be retried.
        finished: summary ready (or ended before any answer).
    """
    state = get_graph().get_state(interview_config(job_id))
    values = state.values or {}
    pending = [i.value for task in state.tasks for i in task.interrupts]
    question = next(
        (p for p in pending if isinstance(p, dict) and p.get("type") == "interview_question"),
        None,
    )
    view = InterviewView(
        job_id=job_id,
        stage="not_started",
        turns=values.get("turns", []),
        total=values.get("num_questions", NUM_QUESTIONS),
        summary=values.get("summary"),
        overall_score=values.get("overall_score"),
        ended_early=values.get("ended_early", False),
    )
    if not values:
        view.stage = "not_started" if build_setup(job_id) is not None else "unavailable"
    elif question is not None:
        view.stage = "question"
        view.current = values.get("current")
        view.number = question["number"]
    elif state.next:
        view.stage = "interrupted"
    else:
        view.stage = "finished"
    return view


# A message ending in "…" says which step runs now (see components.progress_status).
_RUNNING = {
    "generate_question": "Writing the next question…",
    "evaluate_answer": "Scoring your answer…",
    "summarise": "Writing your summary…",
}


def _progress_message(node: str, update: dict | None) -> str | None:
    update = update or {}
    if node == "generate_question" and update.get("current"):
        return "Wrote the next question"
    if node == "evaluate_answer" and update.get("turns"):
        feedback = update["turns"][-1].feedback
        return f"Scored your answer: {feedback.score}/5" if feedback else None
    if node == "summarise" and update.get("summary"):
        return "Wrote your summary"
    return None


def _run(job_id: int, payload: object, on_progress: ProgressFn | None) -> RunResult:
    """Run the interview graph until it pauses or ends; any failure becomes a message."""
    try:
        if not get_settings().has_llm_key:
            raise ConfigError("OPENROUTER_API_KEY is not set")
        with track_run("interview", job_id=job_id):
            for mode, chunk in get_graph().stream(
                payload, interview_config(job_id), stream_mode=["tasks", "updates"]
            ):
                if mode == "tasks":  # a step starting has "input"; a finished one has "result"
                    messages = [_RUNNING.get(chunk["name"])] if "input" in chunk else []
                else:
                    messages = [_progress_message(node, v) for node, v in chunk.items()]
                for message in messages:
                    if message and on_progress:
                        on_progress(message)
    except AppError as exc:
        return RunResult(ok=False, job_id=job_id, message=exc.user_message)
    except Exception as exc:
        log_exception(logger, "Interview step failed", job_id=job_id)
        return RunResult(ok=False, job_id=job_id, message=friendly_message(exc))
    return RunResult(ok=True, job_id=job_id)


def forget(job_id: int) -> None:
    """Delete the job's current interview (finished ones stay in the InterviewSession table)."""
    checkpointer = get_graph().checkpointer
    if checkpointer is not None:
        checkpointer.delete_thread(interview_thread_id(job_id))


def start(job_id: int, on_progress: ProgressFn | None = None) -> RunResult:
    """Start a new mock interview (replacing an unfinished one) and stop at question 1."""
    setup = build_setup(job_id)
    if setup is None:
        return RunResult(ok=False, job_id=job_id, message=NEEDS_ANALYSIS)
    forget(job_id)
    payload = {"job_id": job_id, "setup": setup, "num_questions": NUM_QUESTIONS, "turns": []}
    return _run(job_id, payload, on_progress)


def _resume(job_id: int, payload: dict, on_progress: ProgressFn | None) -> RunResult:
    if get_view(job_id).stage != "question":
        return RunResult(ok=False, job_id=job_id, message="No question is waiting for an answer.")
    return _run(job_id, Command(resume=payload), on_progress)


def answer(job_id: int, text: str, on_progress: ProgressFn | None = None) -> RunResult:
    """Send the answer to the current question; get feedback and the next question."""
    check = check_interview_answer(text)
    if not check.allowed:
        return RunResult(ok=False, job_id=job_id, message=check.reason)
    return _resume(job_id, {"action": "answer", "text": clean_multiline(text)}, on_progress)


def end_early(job_id: int, on_progress: ProgressFn | None = None) -> RunResult:
    """Stop now and summarise the answers so far."""
    return _resume(job_id, {"action": "stop"}, on_progress)


def retry(job_id: int, on_progress: ProgressFn | None = None) -> RunResult:
    """Continue an interview step that broke off, from the last saved step."""
    return _run(job_id, None, on_progress)


def rate(job_id: int, rating: int) -> None:
    """Save a 👍 (+1) / 👎 (-1) rating of the interview feedback."""
    with repo.session_scope() as session:
        repo.add_feedback(session, "interview", rating, job_id=job_id)


def past_interviews(job_id: int) -> list[PastInterview]:
    """The job's finished interviews, newest first."""
    with repo.session_scope() as session:
        rows = repo.list_interview_sessions(session, job_id)
    return [PastInterview(r.created_at, r.overall_score, len(json.loads(r.qa_json))) for r in rows]
