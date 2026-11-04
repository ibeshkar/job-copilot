# What: the `assess_fit` step. The LLM classifies each requirement as met / partial / missing
#       from the graded evidence; Python then checks red flags against the profile and computes
#       the 0-100 fit score.
# Why: the score must be reproducible and explainable, so the arithmetic and the hard rules
#      (language level, visa, seniority, location) are plain Python, not LLM opinion (§5.2).
# Used by: app/agent/application_graph.py; tests/test_fit.py tests the pure functions.

import re

from app.agent.deps import GraphDeps
from app.agent.llm import invoke_structured
from app.agent.prompts import fit_assessor_messages
from app.agent.state import (
    ApplicationState,
    Evidence,
    FitAssessment,
    FitReport,
    JobPosting,
    RedFlag,
    Requirement,
    RequirementMatch,
)
from app.memory.store import Preferences, Profile

# ---------------------------------------------------------------- the score (pure)

IMPORTANCE_WEIGHT = {"must": 3, "nice": 1}
STATUS_POINTS = {"met": 1.0, "partial": 0.5, "missing": 0.0}
BLOCKING_CAP = 40


def compute_fit_score(
    requirements: list[Requirement],
    matches: list[RequirementMatch],
    red_flags: list[RedFlag],
) -> int:
    """Compute the fit score from 0 to 100.

    score = round(100 * Σ(weight * points) / Σ(weight)), where must-haves weigh 3 and
    nice-to-haves 1, and met = 1, partial = 0.5, missing = 0. A requirement without a match
    counts as missing. Any blocking red flag (e.g. a required language the user doesn't speak
    well enough) caps the score at 40, because no amount of skills makes up for it.

    Example: must met (3×1) + must partial (3×0.5) + nice missing (1×0) = 4.5 of 7 → 64.
    """
    if not requirements:
        return 0
    status_by_id = {m.requirement_id: m.status for m in matches}
    total_weight = sum(IMPORTANCE_WEIGHT[r.importance] for r in requirements)
    earned = sum(
        IMPORTANCE_WEIGHT[r.importance] * STATUS_POINTS[status_by_id.get(r.id, "missing")]
        for r in requirements
    )
    # Round half up (62.5 → 63). Python's round() would give 62 ("banker's rounding").
    score = int(100 * earned / total_weight + 0.5)
    if any(flag.blocking for flag in red_flags):
        score = min(score, BLOCKING_CAP)
    return score


# ---------------------------------------------------------------- red flags (pure)

LEVEL_RANK = {"A1": 1, "A2": 2, "B1": 3, "B2": 4, "C1": 5, "C2": 6, "native": 7}
# Words people type in their profile instead of a CEFR level.
_LEVEL_WORDS = {
    "native": "native",
    "mother tongue": "native",
    "fluent": "C1",
    "business": "C1",
    "proficient": "C1",
    "good": "B2",
    "intermediate": "B1",
    "basic": "A2",
    "beginner": "A1",
}
_CEFR_RE = re.compile(r"\b([ABC][12])\b", re.IGNORECASE)
UNKNOWN_LEVEL = "B2"  # used when a level isn't stated, on either side

SENIORITY_RANK = {"intern": 0, "junior": 1, "mid": 2, "senior": 3, "lead": 4}


def parse_profile_languages(languages: list[str]) -> dict[str, str]:
    """Turn the profile's free-text languages into {language: level}.

    Example: ["English C1", "German A2", "Czech native"] →
    {"english": "C1", "german": "A2", "czech": "native"}. This parses what the user typed in
    the onboarding form, not LLM output.
    """
    parsed: dict[str, str] = {}
    for entry in languages:
        words = entry.strip().split()
        if not words:
            continue
        level = UNKNOWN_LEVEL
        if match := _CEFR_RE.search(entry):
            level = match.group(1).upper()
        else:
            for word, mapped in _LEVEL_WORDS.items():
                if word in entry.lower():
                    level = mapped
                    break
        parsed[words[0].lower().strip(",:()")] = level
    return parsed


def needs_visa_sponsorship(work_authorisation: str) -> bool:
    """Guess from the profile text whether the user needs a visa sponsor.

    "EU citizen, no visa needed" → False; "Needs visa sponsorship" → True; empty → False.
    """
    text = work_authorisation.lower()
    if not text or "no visa" in text or "citizen" in text or "permanent" in text:
        return False
    return "sponsor" in text or "visa" in text


def find_red_flags(
    job: JobPosting, profile: Profile | None, deal_breaker_hits: list[str] | None = None
) -> list[RedFlag]:
    """Compare the job with the user's profile and list practical blockers.

    - Language: a required language missing from the profile, or 2+ levels too low, is
      blocking. One level too low is a warning.
    - Visa: the job says no sponsorship and the user seems to need it → blocking.
    - Seniority: the job is above the user's level → warning.
    - Location: on-site/hybrid in a city not in the user's locations → warning.
    - Deal-breakers the LLM spotted → warning.
    """
    flags: list[RedFlag] = [
        RedFlag(kind="deal_breaker", message=f"Hits your deal-breaker: {hit}", blocking=False)
        for hit in deal_breaker_hits or []
    ]
    if profile is None:
        return flags

    spoken = parse_profile_languages(profile.languages)
    for req in job.language_requirements:
        if not req.required:
            continue
        needed = req.level if req.level != "unknown" else UNKNOWN_LEVEL
        have = spoken.get(req.language.lower())
        if have is None:
            flags.append(
                RedFlag(
                    kind="language",
                    message=f"Requires {req.language} ({needed}); your profile doesn't list it.",
                    blocking=True,
                )
            )
            continue
        gap = LEVEL_RANK[needed] - LEVEL_RANK[have]
        if gap >= 2:
            flags.append(
                RedFlag(
                    kind="language",
                    message=f"Requires {req.language} {needed}; your level is {have}.",
                    blocking=True,
                )
            )
        elif gap == 1:
            flags.append(
                RedFlag(
                    kind="language",
                    message=f"Asks for {req.language} {needed}; your level is {have}, "
                    "one step below.",
                    blocking=False,
                )
            )

    if job.visa_sponsorship == "no" and needs_visa_sponsorship(profile.work_authorisation):
        flags.append(
            RedFlag(
                kind="visa",
                message="The employer doesn't sponsor visas, and your profile says you need one.",
                blocking=True,
            )
        )

    if job.seniority in SENIORITY_RANK:
        gap = SENIORITY_RANK[job.seniority] - SENIORITY_RANK[profile.seniority]
        if gap >= 1:
            flags.append(
                RedFlag(
                    kind="seniority",
                    message=f"This is a {job.seniority}-level role; your profile says "
                    f"{profile.seniority}.",
                    blocking=False,
                )
            )

    if job.remote_policy in ("onsite", "hybrid") and job.location:
        wanted = " ".join(profile.locations).lower()
        cities = [part.strip().lower() for part in re.split(r"[,/()]", job.location)]
        if "relocat" not in wanted and not any(city and city in wanted for city in cities):
            flags.append(
                RedFlag(
                    kind="location",
                    message=f"{job.remote_policy.capitalize()} in {job.location}, which is not "
                    "in your preferred locations.",
                    blocking=False,
                )
            )
    return flags


# ---------------------------------------------------------------- the node

PROFILE_CHUNK_ID = "profile"


def _profile_text(profile: Profile | None, preferences: Preferences) -> str:
    if profile is None:
        return "No profile saved."
    return (
        f"Seniority: {profile.seniority}. Languages: {', '.join(profile.languages) or 'none'}. "
        f"Locations: {', '.join(profile.locations) or 'none'}. "
        f"Work authorisation: {profile.work_authorisation or 'not stated'}. "
        f"Deal-breakers: {', '.join(preferences.deal_breakers) or 'none'}."
    )


def assess_fit(state: ApplicationState, deps: GraphDeps) -> dict:
    """Classify every requirement, attach the cited evidence, find red flags, compute the score.

    Grounding rule: a requirement may only be "met" or "partial" if the LLM cites at least one
    evidence id we actually gave it. Otherwise it is downgraded to "missing". This stops the
    model from claiming skills it can't point to.
    """
    job = state["job"]
    requirements = state["requirements"]
    evidence = state.get("evidence") or {}
    profile = deps.get_profile()
    preferences = deps.get_preferences()

    profile_text = _profile_text(profile, preferences)
    profile_evidence = Evidence(
        chunk_id=PROFILE_CHUNK_ID,
        doc_name="Your profile",
        snippet=profile_text[:300],
        relevance=1.0,
    )

    evidence_lines = []
    for r in requirements:
        evidence_lines.append(f"{r.id} ({r.importance}): {r.text}")
        chunks = evidence.get(r.id, [])
        if not chunks:
            evidence_lines.append("  (no evidence found in the candidate's documents)")
        evidence_lines += [f"  [{c.chunk_id}] ({c.doc_name}) {c.snippet}" for c in chunks]
    requirements_block = "\n".join(f"{r.id} ({r.importance}): {r.text}" for r in requirements)
    job_summary = (
        f"{job.title} at {job.company}. Location: {job.location or 'not stated'} "
        f"({job.remote_policy}). Seniority: {job.seniority}. {job.summary}"
    )
    profile_block = f"[{PROFILE_CHUNK_ID}] {profile_text}"

    assessment = invoke_structured(
        deps.llm,
        FitAssessment,
        fit_assessor_messages(
            job_summary, requirements_block, "\n".join(evidence_lines), profile_block
        ),
    )

    by_id = {m.requirement_id: m for m in assessment.matches}
    matches: list[RequirementMatch] = []
    for r in requirements:
        allowed = {c.chunk_id: c for c in evidence.get(r.id, [])}
        allowed[PROFILE_CHUNK_ID] = profile_evidence
        m = by_id.get(r.id)
        if m is None:
            matches.append(
                RequirementMatch(
                    requirement_id=r.id,
                    status="missing",
                    evidence=[],
                    explanation="Not assessed.",
                    honest_positioning=None,
                )
            )
            continue
        cited = [allowed[cid] for cid in m.supporting_chunk_ids if cid in allowed]
        status = m.status
        explanation = m.explanation
        if status != "missing" and not cited:
            status = "missing"
            explanation = f"{explanation} (Downgraded: no evidence from your documents.)"
        matches.append(
            RequirementMatch(
                requirement_id=r.id,
                status=status,
                evidence=cited,
                explanation=explanation,
                honest_positioning=m.honest_positioning,
            )
        )

    red_flags = find_red_flags(job, profile, assessment.deal_breaker_hits)
    fit = FitReport(
        matches=matches,
        score=compute_fit_score(requirements, matches, red_flags),
        red_flags=red_flags,
        strengths_summary=assessment.strengths_summary,
        questions_to_ask_employer=assessment.questions_to_ask_employer,
    )
    return {"fit": fit}
