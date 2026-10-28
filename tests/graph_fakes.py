# What: a ready-made fake application graph setup: scripted LLM answers, a tiny document
#       search, and helpers that build GraphDeps around them.
# Why: the graph tests, service tests and UI tests all need the same offline "world" in which
#      a sample job scores exactly 86 and pauses at the fit review.
# Used by: tests/test_application_graph.py, tests/test_applications_service.py,
#          tests/test_ui_pages.py.

import re

from langgraph.checkpoint.memory import InMemorySaver

from app.agent.application_graph import build_application_graph, thread_config
from app.agent.deps import GraphDeps
from app.agent.state import (
    ChunkGrade,
    Evidence,
    ExtractedRequirement,
    FitAssessment,
    GradeList,
    JobPostingExtraction,
    MatchClassification,
    RequirementList,
    SearchQueries,
    SearchQuery,
)
from app.memory.store import Preferences, Profile
from tests.fakes import FakeStructuredLLM

POSTING = (
    "Junior AI Engineer at Lumen Health, Berlin (hybrid). You know Python, have built a RAG "
    "chatbot, and Kubernetes is a plus. English is our working language. " * 3
)

CORPUS = [
    Evidence(
        chunk_id="cv.md#0", doc_name="cv.md", snippet="Two years of Python and SQL.", relevance=0.8
    ),
    Evidence(
        chunk_id="documind.md#0",
        doc_name="documind.md",
        snippet="Built a RAG chatbot with LangChain and ChromaDB.",
        relevance=0.9,
    ),
]


def _words(text: str) -> set[str]:
    return set(re.findall(r"[a-z]+", text.lower())) - {"a", "and", "with", "of", "the"}


def fake_search(query: str, k: int) -> list[Evidence]:
    """Return corpus chunks that share a word with the query."""
    return [e for e in CORPUS if _words(e.snippet) & _words(query)][:k]


# ---------------------------------------------------------------- scripted LLM answers


def answer_queries(messages) -> SearchQueries:
    # Works for the planner ("R1: text") and the rewriter ("R1: text | previous query: …").
    lines = re.findall(r"^(R\d+): ([^|\n]+)", messages[-1].content, re.MULTILINE)
    return SearchQueries(queries=[SearchQuery(requirement_id=r, query=t) for r, t in lines])


def answer_grades(messages) -> GradeList:
    grades, current, req_text = [], None, ""
    for line in messages[-1].content.splitlines():
        if m := re.match(r"Requirement (R\d+): (.*)", line):
            current, req_text = m.group(1), m.group(2)
        elif m := re.search(r"\[chunk_id=([^\]]+)\] \([^)]*\) (.*)", line):
            relevant = bool(_words(m.group(2)) & _words(req_text))
            grades.append(
                ChunkGrade(
                    requirement_id=current, chunk_id=m.group(1), relevant=relevant, score=0.9
                )
            )
    return GradeList(grades=grades)


def answer_fit(messages) -> FitAssessment:
    evidence_block = (
        messages[-1].content.split("<candidate_evidence>")[1].split("</candidate_evidence>")[0]
    )
    cited: dict[str, list[str]] = {}
    current = None
    for line in evidence_block.splitlines():
        if m := re.match(r"(R\d+) \(", line):
            current = m.group(1)
            cited[current] = []
        elif m := re.match(r"\s+\[([^\]]+)\]", line):
            cited[current].append(m.group(1))
    return FitAssessment(
        matches=[
            MatchClassification(
                requirement_id=rid,
                status="met" if ids else "missing",
                supporting_chunk_ids=ids,
                explanation="ok",
                honest_positioning=None if ids else "Say you are learning it.",
            )
            for rid, ids in cited.items()
        ],
        strengths_summary="Strong Python and RAG.",
        questions_to_ask_employer=["What does onboarding look like?"],
        deal_breaker_hits=[],
    )


EXTRACTION = JobPostingExtraction(
    title="Junior AI Engineer",
    company="Lumen Health",
    location="Berlin",
    remote_policy="hybrid",
    seniority="junior",
    language_requirements=[],
    visa_sponsorship="unknown",
    salary_range=None,
    summary="Builds LLM features.",
    is_job_posting=True,
)
REQUIREMENTS = RequirementList(
    requirements=[
        ExtractedRequirement(text="Python", category="skill", importance="must"),
        ExtractedRequirement(text="Built a RAG chatbot", category="experience", importance="must"),
        ExtractedRequirement(text="Kubernetes", category="skill", importance="nice"),
    ]
)


def make_llm(overrides: dict | None = None) -> FakeStructuredLLM:
    responders = {
        JobPostingExtraction: EXTRACTION,
        RequirementList: REQUIREMENTS,
        SearchQueries: answer_queries,
        GradeList: answer_grades,
        FitAssessment: answer_fit,
    }
    responders.update(overrides or {})
    return FakeStructuredLLM(responders)


class Saved:
    def __init__(self) -> None:
        self.calls: list[tuple[int, str, int | None]] = []

    def __call__(self, job_id, job, fit) -> None:
        self.calls.append((job_id, job.title, fit.score if fit else None))


def make_deps(llm=None, kb_size: int = 10, fetch_url=None, saved=None) -> GraphDeps:
    return GraphDeps(
        llm=llm or make_llm(),
        search=fake_search,
        knowledge_base_size=lambda: kb_size,
        get_profile=lambda: Profile(name="Alex", languages=["English C1"], locations=["Berlin"]),
        get_preferences=Preferences,
        fetch_url=fetch_url or (lambda url: POSTING),
        save_job=saved or Saved(),
    )


def run(deps: GraphDeps, raw_input: str = POSTING, job_id: int | None = 7, checkpointer=None):
    graph = build_application_graph(deps, checkpointer or InMemorySaver())
    config = thread_config("job-7")
    graph.invoke({"raw_input": raw_input, "job_id": job_id}, config)
    return graph, config
