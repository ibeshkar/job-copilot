# What: the four evaluations. Each one runs a real part of the app on a dataset and scores it:
#       1. requirements extraction → precision/recall vs. labels (plain Python)
#       2. drafting → DeepEval faithfulness + G-Eval "tailored, no filler" + removed-claim check
#       3. copilot chat → DeepEval answer relevancy
#       4. fit analysis → the same job run 3 times, spread of the fit score
# Why: they call the production code (the same nodes and graphs the UI uses), so the numbers
#      describe the app as it really is, not a copy of its prompts.
# Used by: evals/run_evals.py, evals/test_faithfulness.py, evals/test_requirements_extraction.py.

import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from langchain_core.language_models import BaseChatModel

from app.agent.nodes.drafting import basics_block, draft_materials, find_rejected_claims
from app.agent.nodes.requirements import extract_requirements
from app.agent.state import Claim, FitReport, JobPosting, Requirement, RequirementMatch
from app.memory.store import Preferences, Profile
from evals.data import DraftingCase, LabelledPosting
from evals.scoring import ExtractionScore, ScoreSpread, score_extraction

Progress = Callable[[str], None]


def _quiet(_: str) -> None:
    pass


@dataclass
class MetricResult:
    """One DeepEval metric on one case: score 0..1, pass/fail and the judge's reason."""

    score: float | None
    passed: bool
    reason: str = ""
    error: str = ""
    # Statements the judge did not accept ("no" or "idk" verdicts), for the failure analysis.
    flagged: list[str] = field(default_factory=list)


def _flagged(metric: Any) -> list[str]:
    """The judge's rejected statements with its reasons, for metrics that keep verdicts.

    Faithfulness pairs `metric.claims[i]` (a statement from the letter) with
    `metric.verdicts[i]` ("yes" = backed by the context, "no" = contradicted, "idk" = not
    in the context).
    """
    claims = getattr(metric, "claims", None) or []
    verdicts = getattr(metric, "verdicts", None) or []
    flagged = []
    for claim, verdict in zip(claims, verdicts, strict=False):
        if getattr(verdict, "verdict", "yes").strip().lower() != "yes":
            reason = getattr(verdict, "reason", None) or ""
            flagged.append(f"[{verdict.verdict}] {claim} {f'({reason})' if reason else ''}".strip())
    return flagged


def measure(metric: Any, test_case: Any) -> MetricResult:
    """Run one DeepEval metric; a judge failure becomes an error row, not a crash."""
    try:
        metric.measure(test_case)
    except Exception as exc:  # the judge may time out or answer badly; keep the other cases
        return MetricResult(score=None, passed=False, error=f"{type(exc).__name__}: {exc}")
    return MetricResult(
        score=float(metric.score),
        passed=bool(metric.success),
        reason=metric.reason or "",
        flagged=_flagged(metric),
    )


# ---------------------------------------------------------------- 1. requirements extraction


def run_extraction(
    llm: BaseChatModel, postings: list[LabelledPosting], progress: Progress = _quiet
) -> list[ExtractionScore]:
    """Extract requirements from each labelled posting with the real node, then score them."""
    scores = []
    for posting in postings:
        progress(f"extraction: {posting.id}")
        # The node only reads `state["job"].raw_text` and `deps.llm`, so light stand-ins do.
        state = {"job": SimpleNamespace(raw_text=posting.text)}
        result = extract_requirements(state, SimpleNamespace(llm=llm))
        requirements: list[Requirement] = result.get("requirements", [])
        scores.append(
            score_extraction(
                posting.id,
                posting.must,
                [r.text for r in requirements if r.importance == "must"],
                [r.text for r in requirements if r.importance == "nice"],
            )
        )
    return scores


# ---------------------------------------------------------------- 2. drafting


@dataclass
class DraftResult:
    """The drafts written for one case and how they scored."""

    case_id: str
    cover_letter: str = ""
    cv_bullets: list[str] = field(default_factory=list)
    words: int = 0
    removed_claim_leaked: bool = False
    faithfulness: MetricResult | None = None
    tailoring: MetricResult | None = None
    error: str = ""


def drafting_state(case: DraftingCase) -> dict:
    """The graph state `draft_materials` expects after a claim check, built from a case."""
    job = JobPosting(
        **case.job.model_dump(),
        language_requirements=[],
        visa_sponsorship="unknown",
        salary_range=None,
        is_job_posting=True,
        raw_text=case.posting_text,
    )
    claims = [
        Claim(
            id=f"C{i}",
            text=text,
            evidence_chunk_ids=["eval"],
            supported=True,
            user_decision="approved",
        )
        for i, text in enumerate(case.approved, start=1)
    ]
    claims += [
        Claim(
            id=f"X{i}", text=text, evidence_chunk_ids=[], supported=False, user_decision="removed"
        )
        for i, text in enumerate(case.removed, start=1)
    ]
    requirements = [
        Requirement(id=f"G{i}", text=gap.requirement, category="other", importance="must")
        for i, gap in enumerate(case.gaps, start=1)
    ]
    matches = [
        RequirementMatch(
            requirement_id=f"G{i}",
            status=gap.status,
            evidence=[],
            explanation="",
            honest_positioning=gap.honest_positioning,
        )
        for i, gap in enumerate(case.gaps, start=1)
    ]
    fit = FitReport(
        matches=matches, score=0, red_flags=[], strengths_summary="", questions_to_ask_employer=[]
    )
    return {
        "job_id": None,
        "job": job,
        "claims": claims,
        "requirements": requirements,
        "fit": fit,
        "draft_attempts": 0,
    }


def faithfulness_context(case: DraftingCase, profile: Profile) -> list[str]:
    """Everything the writer was allowed to use, as the "truths" the judge checks against.

    That is: the fact that the candidate applies, the approved claims, the profile basics, the
    honest notes on the gaps, and the posting. The removed claims are left out on purpose: if
    one leaks into the letter, the judge can't find it here and the score drops.
    """
    applying = (
        f"{profile.name} is applying for the {case.job.title} role at {case.job.company} and is "
        "interested in it."
    )
    gaps = [f"Gap: {g.requirement} ({g.status}). {g.honest_positioning}" for g in case.gaps]
    return [applying, *case.approved, basics_block(profile), *gaps, case.posting_text]


def write_drafts(
    llm: BaseChatModel, case: DraftingCase, profile: Profile, preferences: Preferences
) -> DraftResult:
    """Write the drafts for one case with the real `draft_materials` node (no judging)."""
    deps = SimpleNamespace(
        llm=llm,
        get_profile=lambda: profile,
        get_preferences=lambda: preferences,
        list_style_rules=lambda: [],
    )
    state = drafting_state(case)
    try:
        drafts = draft_materials(state, deps)["drafts"]
    except Exception as exc:
        return DraftResult(case.id, error=f"{type(exc).__name__}: {exc}")
    letter = drafts.cover_letter.text
    text = f"{letter}\n" + "\n".join(drafts.cv_bullets)
    return DraftResult(
        case_id=case.id,
        cover_letter=letter,
        cv_bullets=drafts.cv_bullets,
        words=len(" ".join(drafts.cover_letter.body_paragraphs).split()),  # the word limit's scope
        removed_claim_leaked=bool(find_rejected_claims(text, state["claims"])),
    )


def judge_drafts(
    result: DraftResult, case: DraftingCase, profile: Profile, metrics: dict[str, Any]
) -> None:
    """Score one written letter with faithfulness and the tailoring rubric (fills `result`)."""
    from deepeval.test_case import LLMTestCase

    if result.error:
        return
    task = f"Write a cover letter for this job:\n\n{case.posting_text}"
    result.faithfulness = measure(
        metrics["faithfulness"],
        LLMTestCase(
            input=task,
            actual_output=result.cover_letter,
            retrieval_context=faithfulness_context(case, profile),
        ),
    )
    result.tailoring = measure(
        metrics["tailoring"],
        LLMTestCase(input=case.posting_text, actual_output=result.cover_letter),
    )


# ---------------------------------------------------------------- 3. copilot chat


@dataclass
class ChatResult:
    """The copilot's answer to one question and its relevancy score."""

    question: str
    answer: str = ""
    tools_used: list[str] = field(default_factory=list)
    relevancy: MetricResult | None = None
    error: str = ""


def ask_copilot(question: str, toggles: list[str]) -> ChatResult:
    """Ask the real copilot graph one question in a fresh thread; return its final answer."""
    from langchain_core.messages import AIMessage, HumanMessage

    from app.agent.copilot_graph import chat_config, get_copilot_graph
    from app.tools.registry import enabled_tool_names

    graph = get_copilot_graph()
    config = chat_config(f"eval-{uuid.uuid4().hex[:8]}")
    payload = {"messages": [HumanMessage(question)], "enabled_tools": enabled_tool_names(toggles)}
    try:
        values = graph.invoke(payload, config)
    except Exception as exc:
        return ChatResult(question, error=f"{type(exc).__name__}: {exc}")
    messages = values.get("messages", [])
    tools = [c["name"] for m in messages if isinstance(m, AIMessage) for c in m.tool_calls]
    answer = next(
        (str(m.content) for m in reversed(messages) if isinstance(m, AIMessage) and m.content), ""
    )
    if graph.get_state(config).next:
        return ChatResult(question, answer, tools, error="stopped at an approval pause")
    return ChatResult(question, answer, tools)


def judge_chat(result: ChatResult, metrics: dict[str, Any]) -> None:
    """Score one chat answer with answer relevancy (fills `result`)."""
    from deepeval.test_case import LLMTestCase

    if result.error or not result.answer:
        return
    result.relevancy = measure(
        metrics["answer_relevancy"],
        LLMTestCase(input=result.question, actual_output=result.answer),
    )


# ---------------------------------------------------------------- 4. fit-score consistency


def run_fit_consistency(
    job_files: list[Path], runs: int, progress: Progress = _quiet
) -> list[ScoreSpread]:
    """Run the real analysis graph `runs` times per job and collect the fit scores.

    Every run uses a new thread, so nothing is reused between runs: extraction, retrieval,
    grading and classification all happen again, like a user adding the same job twice.
    """
    from app.agent.application_graph import get_application_graph, thread_config

    graph = get_application_graph()
    spreads = []
    for path in job_files:
        scores = []
        for run in range(1, runs + 1):
            progress(f"fit consistency: {path.name} run {run}/{runs}")
            config = thread_config(f"eval-{uuid.uuid4().hex[:8]}")
            values = graph.invoke({"raw_input": path.read_text()}, config)
            if values.get("fit") is not None:
                scores.append(values["fit"].score)
        spreads.append(ScoreSpread(path.stem, scores))
    return spreads
