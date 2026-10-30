# What: a ready-made fake application graph setup: scripted LLM answers, a tiny document
#       search, a fake long-term memory, and helpers that build GraphDeps around them.
# Why: the graph tests, service tests and UI tests all need the same offline "world" in which
#      a sample job scores exactly 86 and pauses at the fit review, then proposes 3 claims
#      (2 backed by documents, 1 not), writes drafts from the approved ones, and learns a
#      "don't use this word" rule from every long word the user deletes.
# Used by: tests/test_application_graph.py, tests/test_applications_service.py,
#          tests/test_ui_pages.py.

import re

from langgraph.checkpoint.memory import InMemorySaver

from app.agent.application_graph import build_application_graph, thread_config
from app.agent.deps import GraphDeps
from app.agent.state import (
    AnswerFeedback,
    ChunkGrade,
    ClaimPlan,
    CoverLetter,
    Drafts,
    Evidence,
    ExtractedRequirement,
    FitAssessment,
    GradeList,
    GuardReport,
    InterviewQuestion,
    InterviewSummary,
    JobPostingExtraction,
    MatchClassification,
    PlannedClaim,
    ProposedStyleRule,
    RequirementList,
    SearchQueries,
    SearchQuery,
    StyleRule,
    StyleRuleProposal,
    UnsupportedStatement,
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


def _tag(messages, tag: str) -> str:
    return messages[-1].content.split(f"<{tag}>")[1].split(f"</{tag}>")[0].strip()


UNSUPPORTED_CLAIM = "Led a team of five engineers."
PASSIONATE = "I am passionate about building AI products."
GREETING, CLOSING = "Dear Hiring Team,", "Best regards,"


def answer_claims(messages) -> ClaimPlan:
    """One claim per evidence item (citing it), plus one claim with an invented citation."""
    evidence = _tag(messages, "candidate_evidence")
    claims = [
        PlannedClaim(text=m.group(2), evidence_ids=[m.group(1)])
        for m in re.finditer(r"^\[([^\]]+)\] \([^)]*\) (.*)$", evidence, re.M)
    ]
    return ClaimPlan(claims=[*claims, PlannedClaim(text=UNSUPPORTED_CLAIM, evidence_ids=["x#1"])])


def approved_claims(messages) -> dict[str, str]:
    """{"C1": text} from the <approved_claims> block of a drafting or guard prompt."""
    return dict(re.findall(r"^(C\d+): (.*)$", _tag(messages, "approved_claims"), re.M))


def answer_drafts(messages) -> Drafts:
    """A "writer" that uses every approved claim word for word, one paragraph each."""
    approved = approved_claims(messages)
    letter = CoverLetter(
        recipient_company="Lumen Health",
        recipient_department=None,
        recipient_city_country="Berlin, Germany",
        recipient_name=None,
        subject="Application: Junior AI Engineer – Lumen Health",
        salutation=GREETING,
        body_paragraphs=list(approved.values()),
        closing=CLOSING,
        sender_name="Alex",
    )
    return Drafts(
        cv_bullets=list(approved.values()), cover_letter=letter, used_claim_ids=list(approved)
    )


def replace_in_letter(drafts: Drafts, old: str, new: str) -> Drafts:
    """A copy of the drafts with `old` replaced by `new` in every letter paragraph."""
    letter = drafts.cover_letter
    paragraphs = [p.replace(old, new) for p in letter.body_paragraphs]
    return drafts.model_copy(
        update={"cover_letter": letter.model_copy(update={"body_paragraphs": paragraphs})}
    )


def with_paragraph(drafts: Drafts, paragraph: str) -> Drafts:
    """A copy of the drafts with one more letter paragraph at the end."""
    letter = drafts.cover_letter
    paragraphs = [*letter.body_paragraphs, paragraph]
    return drafts.model_copy(
        update={"cover_letter": letter.model_copy(update={"body_paragraphs": paragraphs})}
    )


def passionate_writer(messages) -> Drafts:
    """A writer that says "passionate" unless a style rule in its prompt bans it (like a real
    model following its instructions)."""
    drafts = answer_drafts(messages)
    if "'passionate'" not in messages[0].content:
        drafts.cover_letter.body_paragraphs.append(PASSIONATE)
    return drafts


def answer_guard(messages) -> GuardReport:
    """A "fact-checker": every draft line must be an approved claim, the greeting/closing or
    a line of the candidate's basics (their name, as the signature)."""
    basics = _tag(messages, "candidate_basics")
    allowed = set(approved_claims(messages).values()) | {GREETING, CLOSING}
    lines = [re.sub(r"^- ", "", line).strip() for line in _tag(messages, "draft").splitlines()]
    return GuardReport(
        unsupported=[
            UnsupportedStatement(quote=line, reason="Not in the approved claims.")
            for line in lines
            if line and not line.endswith(":") and line not in allowed and line not in basics
        ]
    )


def answer_style_rules(messages) -> StyleRuleProposal:
    """A "style learner": every longer word the user deleted (and didn't add back) is banned."""
    diff = _tag(messages, "edit_diff")
    removed = _words(" ".join(x for x in diff.splitlines() if x.startswith("- ")))
    added = _words(" ".join(x for x in diff.splitlines() if x.startswith("+ ")))
    return StyleRuleProposal(
        rules=[
            ProposedStyleRule(rule=f"Avoid the word '{word}'", avoid_phrases=[word])
            for word in sorted(removed - added)
            if len(word) >= 8
        ]
    )


def answer_question(messages) -> InterviewQuestion:
    """An "interviewer" that numbers its questions and always tests R1."""
    previous = _tag(messages, "previous_questions")
    n = 1 if previous == "(none)" else len(previous.splitlines()) + 1
    return InterviewQuestion(
        question=f"Question {n}: tell me about your Python work.",
        kind="technical",  # the graph overrides it with the planned kind
        requirement_id="R1",
        why="Checks Python depth.",
    )


def answer_feedback(messages) -> AnswerFeedback:
    """A "coach": long answers score 4, short ones 2; cites every evidence id it was given."""
    answer = _tag(messages, "candidate_answer")
    ids = re.findall(r"^\[([^\]]+)\]", _tag(messages, "candidate_evidence"), re.M)
    return AnswerFeedback(
        star_parts=["result", "situation"],
        relevance="Answers the question.",
        specificity="Some detail.",
        score=4 if len(answer) > 40 else 2,
        improvement="Add a number.",
        improved_answer="I built a RAG chatbot with LangChain and ChromaDB.",
        evidence_ids_used=[*ids, "invented#9"],
    )


INTERVIEW_SUMMARY = InterviewSummary(
    strongest_areas=["Python"],
    weakest_areas=["Kubernetes"],
    next_steps=["Prepare a Kubernetes learning story."],
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
        ClaimPlan: answer_claims,
        Drafts: answer_drafts,
        GuardReport: answer_guard,
        StyleRuleProposal: answer_style_rules,
        InterviewQuestion: answer_question,
        AnswerFeedback: answer_feedback,
        InterviewSummary: INTERVIEW_SUMMARY,
    }
    responders.update(overrides or {})
    return FakeStructuredLLM(responders)


class Saved:
    def __init__(self) -> None:
        self.calls: list[tuple[int, str, int | None]] = []

    def __call__(self, job_id, job, fit) -> None:
        self.calls.append((job_id, job.title, fit.score if fit else None))


class FakeMemory:
    """Long-term memory and the Draft table, kept in lists."""

    def __init__(self, facts: list[str] | None = None, rules: list[str] | None = None) -> None:
        self.facts = list(facts or [])
        self.rules = [StyleRule(rule=r, source="explicit") for r in rules or []]
        self.drafts: list[tuple[int, Drafts, bool]] = []

    def remember_fact(self, text: str, job_id: int | None) -> None:
        self.facts.append(text)

    def add_style_rule(self, rule: StyleRule) -> str:
        self.rules.append(rule)
        return f"rule-{len(self.rules)}"

    def save_draft(self, job_id: int, drafts: Drafts, is_user_edited: bool) -> None:
        self.drafts.append((job_id, drafts, is_user_edited))


def make_deps(
    llm=None, kb_size: int = 10, fetch_url=None, saved=None, memory: FakeMemory | None = None
) -> GraphDeps:
    memory = memory or FakeMemory()
    return GraphDeps(
        llm=llm or make_llm(),
        search=fake_search,
        knowledge_base_size=lambda: kb_size,
        get_profile=lambda: Profile(name="Alex", languages=["English C1"], locations=["Berlin"]),
        get_preferences=Preferences,
        fetch_url=fetch_url or (lambda url: POSTING),
        save_job=saved or Saved(),
        list_facts=lambda: list(memory.facts),
        remember_fact=memory.remember_fact,
        list_style_rules=lambda: list(memory.rules),
        add_style_rule=memory.add_style_rule,
        save_draft=memory.save_draft,
    )


def run(deps: GraphDeps, raw_input: str = POSTING, job_id: int | None = 7, checkpointer=None):
    graph = build_application_graph(deps, checkpointer or InMemorySaver())
    config = thread_config("job-7")
    graph.invoke({"raw_input": raw_input, "job_id": job_id}, config)
    return graph, config
