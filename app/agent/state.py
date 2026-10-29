# What: Pydantic models for data that flows through the agent (PROJECT_PLAN.md §5.1), the
#       smaller "LLM output" models each prompt must return, and the graph state.
# Why: LLM answers must come back as typed objects (structured output), never as free text we
#      parse with regex. Shared models also give the UI, the graphs and the tests one vocabulary.
# Used by: app/agent/nodes/*, app/agent/application_graph.py, app/rag/retriever.py,
#          app/memory/store.py, and later the UI pages.

import operator
from datetime import UTC, datetime
from typing import Annotated, Literal, TypedDict

from pydantic import BaseModel, Field

# ---------------------------------------------------------------- domain models (§5.1)

CefrLevel = Literal["A1", "A2", "B1", "B2", "C1", "C2", "native", "unknown"]


class LanguageRequirement(BaseModel):
    """One language the job asks for, e.g. German at C1, required."""

    language: str = Field(description="Language name in English, e.g. 'German'")
    level: CefrLevel = Field(
        description="CEFR level. Map words: basic=A2, good=B2, fluent/business fluent=C1, "
        "native/mother tongue=native. Use 'unknown' if no level is given."
    )
    required: bool = Field(description="True if the posting requires it, False if 'a plus'")

    def __str__(self) -> str:
        level = "" if self.level == "unknown" else f" {self.level}"
        return f"{self.language}{level}{'' if self.required else ' (nice to have)'}"


class JobPostingExtraction(BaseModel):
    """What the LLM extracts from a posting. Code adds the raw text and URL afterwards."""

    title: str
    company: str
    location: str | None
    remote_policy: Literal["onsite", "hybrid", "remote", "unknown"]
    seniority: Literal["intern", "junior", "mid", "senior", "lead", "unknown"]
    language_requirements: list[LanguageRequirement]
    visa_sponsorship: Literal["yes", "no", "unknown"]
    salary_range: str | None
    summary: str = Field(description="2-3 sentences: what the team does and what the role is")
    is_job_posting: bool = Field(description="False if the text is clearly not a job posting")


class JobPosting(JobPostingExtraction):
    """A structured job posting, as stored in the graph state and the `Job.posting_json` column."""

    raw_text: str
    source_url: str | None = None


class Requirement(BaseModel):
    """One thing the employer asks for, e.g. "3+ years Python" (must)."""

    id: str
    text: str
    category: Literal["skill", "experience", "education", "language", "soft_skill", "other"]
    importance: Literal["must", "nice"]


class Evidence(BaseModel):
    """A piece of the user's own documents that supports (or doesn't) a requirement."""

    chunk_id: str = Field(description="Id of the knowledge-base chunk, e.g. 'cv.md#3'")
    doc_name: str = Field(description="File the chunk comes from, shown as the citation")
    snippet: str = Field(description="The chunk text, at most 300 characters")
    relevance: float = Field(ge=0.0, le=1.0, description="0 = unrelated, 1 = exact match")


class RequirementMatch(BaseModel):
    """How well the user meets one requirement, with the evidence for it."""

    requirement_id: str
    status: Literal["met", "partial", "missing"]
    evidence: list[Evidence]
    explanation: str
    honest_positioning: str | None


class RedFlag(BaseModel):
    """A practical blocker found by comparing the job with the profile (checked in Python)."""

    kind: Literal["language", "visa", "seniority", "location", "deal_breaker"]
    message: str
    blocking: bool  # True caps the fit score at 40 (§5.2)


class FitReport(BaseModel):
    """The result of the fit analysis, shown on the fit card."""

    matches: list[RequirementMatch]
    score: int  # 0..100, computed in Python by app/agent/nodes/fit.py → compute_fit_score
    red_flags: list[RedFlag]
    strengths_summary: str
    questions_to_ask_employer: list[str]


class Claim(BaseModel):
    """A statement about the candidate that a draft may use, e.g. "Built a RAG chatbot".

    `supported` is set in Python: True only if the claim cites evidence we really have. The
    user then approves, edits or removes it at the claim check; drafts use approved ones only.
    """

    id: str
    text: str
    evidence_chunk_ids: list[str]
    supported: bool
    user_decision: Literal["pending", "approved", "edited", "removed"] = "pending"
    # The cited evidence itself, so the claim check can show it next to the claim.
    evidence: list[Evidence] = Field(default_factory=list)

    @property
    def kept(self) -> bool:
        """True if the user approved or edited it (drafts may use it)."""
        return self.user_decision in ("approved", "edited")


class Drafts(BaseModel):
    """CV bullets and cover letter. Also the structured output of the drafting prompt."""

    cv_bullets: list[str] = Field(description="3-6 CV bullet points, each starting with a verb")
    cover_letter: str = Field(
        description="The full cover letter, paragraphs separated by a blank line"
    )
    used_claim_ids: list[str] = Field(description="Ids of the approved claims the drafts use")


class StyleRule(BaseModel):
    """A writing preference the app learned or the user stated, e.g. "Avoid 'passionate'"."""

    rule: str
    source: Literal["edit_diff", "explicit", "rating"]
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


# ---------------------------------------------------------------- LLM output models
# Each prompt in app/agent/prompts.py returns exactly one of these. They have no default
# values on purpose: OpenAI's strict JSON-schema mode requires every field to be filled.


class ExtractedRequirement(BaseModel):
    text: str = Field(description="Short, self-contained requirement, e.g. '3+ years of Python'")
    category: Literal["skill", "experience", "education", "language", "soft_skill", "other"]
    importance: Literal["must", "nice"]


class RequirementList(BaseModel):
    requirements: list[ExtractedRequirement]


class SearchQuery(BaseModel):
    requirement_id: str
    query: str = Field(description="What to search for in the candidate's documents")


class SearchQueries(BaseModel):
    queries: list[SearchQuery]


class ChunkGrade(BaseModel):
    requirement_id: str
    chunk_id: str
    relevant: bool = Field(description="Does this chunk show the candidate has (part of) it?")
    score: float = Field(ge=0.0, le=1.0, description="How strongly it supports it, 0..1")


class GradeList(BaseModel):
    grades: list[ChunkGrade]


class MatchClassification(BaseModel):
    requirement_id: str
    status: Literal["met", "partial", "missing"]
    supporting_chunk_ids: list[str] = Field(
        description="Ids of the evidence chunks that justify the status. Empty if missing."
    )
    explanation: str = Field(description="One sentence")
    honest_positioning: str | None = Field(
        description="For partial/missing: one honest sentence on how to address the gap. "
        "Null when met."
    )


class PlannedClaim(BaseModel):
    text: str = Field(description="One specific statement about the candidate, max 25 words")
    evidence_ids: list[str] = Field(
        description="Ids of the evidence items that prove it. Empty if none does."
    )


class ClaimPlan(BaseModel):
    claims: list[PlannedClaim]


class UnsupportedStatement(BaseModel):
    quote: str = Field(description="The exact sentence or phrase from the draft")
    reason: str = Field(description="Why it is not covered by the approved claims")


class GuardReport(BaseModel):
    unsupported: list[UnsupportedStatement] = Field(
        description="Statements about the candidate not backed by approved claims. Empty if none."
    )


class CompanyBrief(BaseModel):
    """A short company research summary for the Company tab (from web results only)."""

    summary: str = Field(description="2-3 sentences: what the company does")
    products: list[str] = Field(description="Main products or services")
    tech_stack: list[str] = Field(description="Technologies the company is known to use")
    culture: list[str] = Field(description="Culture, values, ways of working")
    recent_news: list[str] = Field(description="Notable recent news, each with its month/year")
    talking_points: list[str] = Field(description="2-4 honest things to mention when applying")


class FitAssessment(BaseModel):
    matches: list[MatchClassification]
    strengths_summary: str = Field(description="2-3 sentences, grounded in the evidence")
    questions_to_ask_employer: list[str] = Field(description="2-4 questions")
    deal_breaker_hits: list[str] = Field(
        description="Which of the candidate's deal-breakers this job hits. Empty if none."
    )


class TopicCheck(BaseModel):
    """The chat guard's verdict: is this message about the user's job search?"""

    on_topic: bool = Field(
        description="True for anything about jobs, careers, applications, companies, the "
        "user's skills or this app, including short follow-ups like 'yes' or 'thanks'."
    )
    reason: str = Field(description="One short sentence.")


# ---------------------------------------------------------------- human-in-the-loop answers


class FitReviewDecision(BaseModel):
    """What the user chose on the fit card; the value passed to `Command(resume=...)`."""

    action: Literal["continue", "stop", "add_context"]
    text: str | None = None  # the extra context, for "add_context"


class ClaimDecision(BaseModel):
    """The user's decision on one claim."""

    id: str
    action: Literal["approve", "edit", "remove"]
    text: str | None = None  # the new wording, for "edit"


class ClaimReviewDecision(BaseModel):
    """The resume value for the claim check. Claims missing from the list count as removed."""

    decisions: list[ClaimDecision]


class DraftReviewDecision(BaseModel):
    """The resume value for the draft review: accept (maybe with edits) or regenerate a section."""

    action: Literal["accept", "regenerate"]
    section: Literal["cover_letter", "cv_bullets"] | None = None  # for "regenerate"
    instruction: str | None = None  # e.g. "shorter, mention the remote setup"
    edited: Drafts | None = None  # the drafts as the user edited them in the UI


# ---------------------------------------------------------------- graph state


class ApplicationState(TypedDict, total=False):
    """Everything the application workflow graph knows about one job (one thread).

    The checkpointer saves this after every node, so the workflow can pause for the user and
    continue later, even after a restart.
    """

    job_id: int | None
    raw_input: str  # what the user pasted: a URL or the job text
    job: JobPosting | None
    requirements: list[Requirement]
    search_queries: dict[str, str]  # requirement id → query still to run
    evidence: dict[str, list[Evidence]]  # requirement id → relevant chunks (graded)
    fit: FitReport | None
    claims: list[Claim]
    drafts: Drafts | None  # the latest AI-written drafts
    user_edits: Drafts | None  # the drafts as the user accepted them (with their edits)
    new_facts: list[str]  # claims the user vouched for, saved to memory at this claim check
    guard_issues: list[UnsupportedStatement]  # statements the output guard couldn't back up
    draft_attempts: int  # drafts written since the last claim check or regenerate request
    regenerate: dict | None  # {"section": ..., "instruction": ...} while regenerating
    extra_context: str | None  # notes the user added at fit review ("I also used Airflow")
    rag_iterations: int  # how many times queries were rewritten (max 2)
    review_action: str | None  # the user's choice at fit review
    # `operator.add` is a reducer: when a node returns {"warnings": [...]}, LangGraph appends to
    # the list instead of replacing it, so warnings from different nodes are all kept.
    warnings: Annotated[list[str], operator.add]  # shown to the user, e.g. "possible injection"
    errors: Annotated[list[str], operator.add]  # non-empty means the workflow stopped early


# Types the checkpointer may turn back into Python objects when it loads a saved state.
# Anything else in a checkpoint is refused (see application_graph.make_checkpointer).
CHECKPOINT_TYPES: list[type] = [
    LanguageRequirement,
    JobPosting,
    Requirement,
    Evidence,
    RequirementMatch,
    RedFlag,
    FitReport,
    Claim,
    Drafts,
    UnsupportedStatement,
]
