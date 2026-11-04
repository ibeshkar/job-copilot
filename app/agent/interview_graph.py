# What: the mock interview subgraph (docs/ARCHITECTURE.md §3.3), a loop:
#       generate_question → ask (pause for the answer) → evaluate_answer → … (5 questions)
#       → summarise (strengths, weakest areas, next steps; saved to the InterviewSession table).
# Why: a third kind of agent next to the workflow and the ReAct chat: a loop with a human turn
#      inside every round. Python decides the mix of question kinds and computes the overall
#      score; the LLM writes questions and feedback. The checkpointer keeps the interview, so a
#      refresh or restart continues at the same question.
# Used by: app/services/interview.py, app/services/applications.py (thread id on delete),
#          tests/test_interview.py.

from collections.abc import Callable
from dataclasses import dataclass
from functools import lru_cache, partial

from langchain_core.language_models import BaseChatModel
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import interrupt

from app.agent.llm import invoke_structured
from app.agent.prompts import (
    answer_coach_messages,
    interview_question_messages,
    interview_summary_messages,
)
from app.agent.state import (
    AnswerFeedback,
    Evidence,
    InterviewAnswer,
    InterviewQuestion,
    InterviewSetup,
    InterviewState,
    InterviewSummary,
    InterviewTurn,
)
from app.memory.store import Preferences

NUM_QUESTIONS = 5  # §6.4
EVIDENCE_K = 4  # chunks from the user's documents the improved answer may use
# The mix of question kinds, in order. Decided in Python so every interview is balanced.
KIND_PLAN = ["behavioural", "technical", "gap", "technical", "behavioural"]
STAR_ORDER = ["situation", "task", "action", "result"]


@dataclass
class InterviewDeps:
    """What the interview graph needs from the outside world (fakes in tests)."""

    llm: BaseChatModel | None
    search: Callable[[str, int], list[Evidence]]  # (query, k) → the user's best chunks
    get_preferences: Callable[[], Preferences]  # the tone of the coach's feedback
    save_session: Callable[[int, list[dict], float | None], None]  # → InterviewSession table


# ---------------------------------------------------------------- helpers


def question_kind(index: int, has_gaps: bool) -> str:
    """The kind of question number `index` (0-based). No gaps → a technical question instead."""
    kind = KIND_PLAN[index % len(KIND_PLAN)]
    return "technical" if kind == "gap" and not has_gaps else kind


def overall_score(turns: list[InterviewTurn]) -> float | None:
    """Mean of the 1-5 scores, one decimal. Computed in Python, like the fit score."""
    scores = [t.feedback.score for t in turns if t.feedback is not None]
    return round(sum(scores) / len(scores), 1) if scores else None


def _job_block(setup: InterviewSetup) -> str:
    return f"{setup.job_title} at {setup.company}. {setup.job_summary}"


def _requirements_block(setup: InterviewSetup) -> str:
    return "\n".join(f"{r.id} ({r.importance}, {r.category}): {r.text}" for r in setup.requirements)


def _transcript(turns: list[InterviewTurn]) -> str:
    blocks = []
    for n, t in enumerate(turns, start=1):
        fb = t.feedback
        blocks.append(
            f"Q{n} ({t.question.kind}): {t.question.question}\nAnswer: {t.answer}\n"
            + (f"Score: {fb.score}/5. Improvement: {fb.improvement}" if fb else "Not assessed.")
        )
    return "\n\n".join(blocks)


# ---------------------------------------------------------------- nodes


def generate_question(state: InterviewState, deps: InterviewDeps) -> dict:
    """Write the next question, of the kind the plan says (behavioural / technical / gap)."""
    setup = state["setup"]
    turns = state.get("turns", [])
    kind = question_kind(len(turns), bool(setup.gaps))
    question = invoke_structured(
        deps.llm,
        InterviewQuestion,
        interview_question_messages(
            kind=kind,
            job_summary=_job_block(setup),
            requirements_block=_requirements_block(setup),
            gaps_block="\n".join(setup.gaps),
            company_notes=setup.company_notes,
            previous_questions=[t.question.question for t in turns],
        ),
    )
    known_ids = {r.id for r in setup.requirements}
    return {
        "current": question.model_copy(
            update={
                "kind": kind,  # the plan decides, whatever the model wrote
                "requirement_id": question.requirement_id
                if question.requirement_id in known_ids
                else "",
            }
        )
    }


def ask(state: InterviewState) -> dict:
    """Pause with the question; continue with the user's answer (or "end the interview now").

    Resume with `Command(resume={"action": "answer", "text": "…"})` or `{"action": "stop"}`.

    Raises:
        pydantic.ValidationError: the resume value is not a valid InterviewAnswer.
    """
    turns = state.get("turns", [])
    answer = interrupt(
        {
            "type": "interview_question",
            "number": len(turns) + 1,
            "total": state.get("num_questions", NUM_QUESTIONS),
            "question": state["current"].model_dump(mode="json"),
        }
    )
    decision = InterviewAnswer.model_validate(answer)
    if decision.action == "stop" or not (decision.text or "").strip():
        return {"ended_early": True, "current": None}
    turn = InterviewTurn(question=state["current"], answer=decision.text.strip())
    return {"turns": [*turns, turn], "current": None}


def evaluate_answer(state: InterviewState, deps: InterviewDeps) -> dict:
    """Score the last answer and write an improved version from the user's own evidence."""
    turns = list(state["turns"])
    turn = turns[-1]
    # Search with the question and the answer: finds the project the user talked about, and
    # others that would answer the question better.
    evidence = deps.search(f"{turn.question.question} {turn.answer}"[:1000], EVIDENCE_K)
    evidence_block = "\n".join(f"[{e.chunk_id}] ({e.doc_name}) {e.snippet}" for e in evidence)
    feedback = invoke_structured(
        deps.llm,
        AnswerFeedback,
        answer_coach_messages(
            job_summary=_job_block(state["setup"]),
            question=turn.question.question,
            answer=turn.answer,
            evidence_block=evidence_block,
            tone=deps.get_preferences().tone,
        ),
    )
    known_ids = {e.chunk_id for e in evidence}
    feedback = feedback.model_copy(
        update={
            "star_parts": [p for p in STAR_ORDER if p in feedback.star_parts],
            "evidence_ids_used": [i for i in feedback.evidence_ids_used if i in known_ids],
        }
    )
    turns[-1] = turn.model_copy(update={"feedback": feedback, "evidence": evidence})
    return {"turns": turns}


def summarise(state: InterviewState, deps: InterviewDeps) -> dict:
    """Name strengths, weakest areas and next steps; save the session. Nothing if no answers."""
    turns = [t for t in state.get("turns", []) if t.feedback is not None]
    if not turns:
        return {"summary": None, "overall_score": None}
    summary = invoke_structured(
        deps.llm,
        InterviewSummary,
        interview_summary_messages(
            _job_block(state["setup"]), _transcript(turns), deps.get_preferences().tone
        ),
    )
    score = overall_score(turns)
    if state.get("job_id") is not None:
        deps.save_session(state["job_id"], [t.model_dump(mode="json") for t in turns], score)
    return {"summary": summary, "overall_score": score}


# ---------------------------------------------------------------- wiring


def _after_ask(state: InterviewState) -> str:
    return "summarise" if state.get("ended_early") else "evaluate_answer"


def _after_evaluate(state: InterviewState) -> str:
    # The loop: ask again until the planned number of questions is answered.
    done = len(state.get("turns", [])) >= state.get("num_questions", NUM_QUESTIONS)
    return "summarise" if done else "generate_question"


def build_interview_graph(
    deps: InterviewDeps, checkpointer: BaseCheckpointSaver | None
) -> CompiledStateGraph:
    """Build and compile the interview loop. Run it with `interview_config(job_id)`."""
    graph = StateGraph(InterviewState)
    graph.add_node("generate_question", partial(generate_question, deps=deps))
    graph.add_node("ask", ask)
    graph.add_node("evaluate_answer", partial(evaluate_answer, deps=deps))
    graph.add_node("summarise", partial(summarise, deps=deps))

    graph.add_edge(START, "generate_question")
    graph.add_edge("generate_question", "ask")
    graph.add_conditional_edges("ask", _after_ask, ["evaluate_answer", "summarise"])
    graph.add_conditional_edges(
        "evaluate_answer", _after_evaluate, ["generate_question", "summarise"]
    )
    graph.add_edge("summarise", END)
    return graph.compile(checkpointer=checkpointer)


def default_interview_deps() -> InterviewDeps:
    """The real services: OpenRouter LLM, Chroma search, preferences, the app database."""
    from app.agent.llm import get_llm
    from app.config import get_settings
    from app.db import repo
    from app.memory.store import get_preferences
    from app.rag.retriever import search_evidence

    def save_session(job_id: int, qa: list[dict], score: float | None) -> None:
        with repo.session_scope() as session:
            repo.add_interview_session(session, job_id, qa, score)

    return InterviewDeps(
        llm=get_llm() if get_settings().has_llm_key else None,
        search=lambda query, k: search_evidence(query, k=k),
        get_preferences=get_preferences,
        save_session=save_session,
    )


@lru_cache
def get_interview_graph() -> CompiledStateGraph:
    """The app's interview graph. It shares data/runtime/checkpoints.db with the job runs and
    chats, so "Delete all my data" and deleting a job remove interviews too."""
    from app.agent.application_graph import make_checkpointer
    from app.config import get_settings

    return build_interview_graph(
        default_interview_deps(), make_checkpointer(get_settings().runtime_dir / "checkpoints.db")
    )


def interview_thread_id(job_id: int) -> str:
    """One interview thread per job, e.g. "interview-3". "Start again" deletes and reuses it."""
    return f"interview-{job_id}"


def interview_config(job_id: int) -> dict:
    """The config that selects a job's interview thread."""
    return {"configurable": {"thread_id": interview_thread_id(job_id)}, "recursion_limit": 25}
