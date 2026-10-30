# What: the writing steps. `draft_materials` writes CV bullets and a structured cover letter
#       from the approved claims only, then fixes the letter's header fields in Python;
#       `output_guard` has a second LLM call list any statement about the candidate that no
#       approved claim backs, and checks in Python that no removed claim and no word banned by
#       a style rule appears; `draft_review` pauses so the user can edit, regenerate one
#       section, or accept.
# Why: the drafting prompt never sees removed or unapproved claims, and the guard catches what
#      the writer adds on its own. If the guard still finds problems after one rewrite, they are
#      highlighted for the user instead of looping forever.
# Used by: app/agent/application_graph.py.

import re

from langgraph.types import interrupt

from app.agent.deps import GraphDeps
from app.agent.llm import invoke_structured
from app.agent.prompts import draft_writer_messages, output_guard_messages
from app.agent.state import (
    ApplicationState,
    Claim,
    CoverLetter,
    DraftReviewDecision,
    Drafts,
    GuardReport,
    JobPosting,
    StyleRule,
    UnsupportedStatement,
)
from app.memory.store import Profile

MAX_DRAFT_ATTEMPTS = 2  # first draft + one rewrite after the guard complains (§6.1)
SAME_AS_POSTING = "Same as the job posting"
FALLBACK_SALUTATION = "Dear Hiring Team,"
FALLBACK_SALUTATION_DE = "Sehr geehrte Damen und Herren,"
FALLBACK_CLOSING = "Kind regards,"
_GERMAN_WORDS = {"und", "ich", "die", "der", "das", "mit", "für", "nicht", "sie", "bei", "ihr"}
_ENGLISH_WORDS = {"and", "i", "the", "with", "for", "not", "you", "at", "to", "of", "your"}

# ---------------------------------------------------------------- prompt blocks


def claims_block(claims: list[Claim]) -> str:
    """The approved claims as "C1: text" lines. Removed claims are never included."""
    return "\n".join(f"{c.id}: {c.text}" for c in claims if c.kept)


def basics_block(profile: Profile | None) -> str:
    """Facts from the profile the letter may state (name, languages, locations)."""
    if profile is None:
        return "No profile saved."
    return (
        f"Name: {profile.name}. Languages: {', '.join(profile.languages) or 'not stated'}. "
        f"Based in / open to: {', '.join(profile.locations) or 'not stated'}. "
        f"Work authorisation: {profile.work_authorisation or 'not stated'}."
    )


def _gaps_block(state: ApplicationState) -> str:
    texts = {r.id: r.text for r in state["requirements"]}
    lines = []
    for m in state["fit"].matches:
        if m.status == "met":
            continue
        note = f" Honest note: {m.honest_positioning}" if m.honest_positioning else ""
        lines.append(f"{texts.get(m.requirement_id, m.requirement_id)} ({m.status}).{note}")
    return "\n".join(lines)


def drafts_as_text(drafts: Drafts, section: str | None = None) -> str:
    """Drafts as plain text: both sections, or only "cover_letter" / "cv_bullets"."""
    bullets = "CV bullets:\n" + "\n".join(f"- {b}" for b in drafts.cv_bullets)
    letter = f"Cover letter:\n{drafts.cover_letter.text}"
    if section == "cv_bullets":
        return bullets
    if section == "cover_letter":
        return letter
    return f"{bullets}\n\n{letter}"


def _clean_bullets(bullets: list[str]) -> list[str]:
    # Models sometimes add their own "- " or "• " although the list is already structured.
    cleaned = (re.sub(r"^\s*[-•*]\s*", "", b).strip() for b in bullets)
    return [b for b in cleaned if b]


def _looks_german(text: str) -> bool:
    words = re.findall(r"\w+", text.lower())
    return sum(w in _GERMAN_WORDS for w in words) > sum(w in _ENGLISH_WORDS for w in words)


def _normalised(text: str) -> str:
    return " ".join(text.lower().split())


def finalise_letter(letter: CoverLetter, job: JobPosting, profile: Profile | None) -> CoverLetter:
    """Fix the letter's header fields in Python instead of trusting the model with them.

    A contact name is kept only if it really appears in the posting; without one the
    salutation is the generic fallback. The sender is the profile's name. Empty fields get
    sensible defaults, so the export never shows a blank subject or closing.
    """
    body = [p.strip() for p in letter.body_paragraphs if p.strip()]
    name = (letter.recipient_name or "").strip()
    if not name or _normalised(name) not in _normalised(job.raw_text):
        name = ""
    salutation = letter.salutation.strip()
    if not name or not salutation:
        german = _looks_german(" ".join(body))
        salutation = FALLBACK_SALUTATION_DE if german else FALLBACK_SALUTATION
    return CoverLetter(
        recipient_company=letter.recipient_company.strip() or job.company,
        recipient_department=(letter.recipient_department or "").strip() or None,
        recipient_city_country=(letter.recipient_city_country or "").strip() or None,
        recipient_name=name or None,
        subject=letter.subject.strip() or f"Application: {job.title} – {job.company}",
        salutation=salutation,
        body_paragraphs=body,
        closing=letter.closing.strip() or FALLBACK_CLOSING,
        sender_name=profile.name if profile else letter.sender_name.strip(),
    )


# ---------------------------------------------------------------- nodes


def draft_materials(state: ApplicationState, deps: GraphDeps) -> dict:
    """Write the drafts from the approved claims, the posting, memory and style rules.

    Three cases: a first draft; a rewrite that must drop statements the guard flagged; or a
    regenerate request for one section, where the other section is kept exactly as it was.
    """
    claims = state.get("claims", [])
    approved_ids = {c.id for c in claims if c.kept}
    preferences = deps.get_preferences()
    regenerate = state.get("regenerate")
    current = state.get("drafts")
    issues = state.get("guard_issues") or []
    job = state["job"]
    profile = deps.get_profile()
    language = (
        "the same language as the job posting"
        if preferences.letter_language == SAME_AS_POSTING
        else preferences.letter_language
    )

    answer = invoke_structured(
        deps.llm,
        Drafts,
        draft_writer_messages(
            job_summary=f"{job.title} at {job.company} ({job.location or 'location not stated'}). "
            f"{job.summary}\n\n{job.raw_text[:6000]}",
            claims_block=claims_block(claims),
            basics_block=basics_block(profile),
            gaps_block=_gaps_block(state),
            tone=preferences.tone,
            letter_length=preferences.letter_length,
            language=language,
            style_rules=[rule.rule for rule in deps.list_style_rules()],
            fix_issues=[f'"{i.quote}" ({i.reason})' for i in issues] or None,
            regenerate=regenerate,
            current_drafts=drafts_as_text(current) if current else None,
        ),
    )
    drafts = Drafts(
        cv_bullets=_clean_bullets(answer.cv_bullets),
        cover_letter=finalise_letter(answer.cover_letter, job, profile),
        # Only ids of approved claims count; anything else the model lists is dropped.
        used_claim_ids=[i for i in dict.fromkeys(answer.used_claim_ids) if i in approved_ids],
    )
    if regenerate and current is not None:
        if regenerate.get("section") == "cv_bullets":
            drafts = drafts.model_copy(update={"cover_letter": current.cover_letter})
        else:
            drafts = drafts.model_copy(update={"cv_bullets": current.cv_bullets})
        merged = dict.fromkeys([*current.used_claim_ids, *drafts.used_claim_ids])
        drafts = drafts.model_copy(update={"used_claim_ids": list(merged)})
    return {"drafts": drafts, "draft_attempts": state.get("draft_attempts", 0) + 1}


def find_rejected_claims(text: str, claims: list[Claim]) -> list[UnsupportedStatement]:
    """Plain-Python safety net: a removed claim's exact wording must not appear in the draft."""
    lowered = " ".join(text.lower().split())
    return [
        UnsupportedStatement(quote=c.text, reason="You removed this claim at the claim check.")
        for c in claims
        if c.user_decision == "removed" and " ".join(c.text.lower().split()) in lowered
    ]


def find_avoided_phrases(text: str, rules: list[StyleRule]) -> list[UnsupportedStatement]:
    """Plain-Python check: words the user's style rules ban (e.g. "passionate") must not appear.

    Whole words only, ignoring case, so banning "passion" doesn't flag "compassionate".
    """
    issues = []
    for rule in rules:
        for phrase in rule.avoid_phrases:
            pattern = r"(?<!\w)" + r"\s+".join(map(re.escape, phrase.split())) + r"(?!\w)"
            if phrase.strip() and re.search(pattern, text, re.IGNORECASE):
                issues.append(
                    UnsupportedStatement(quote=phrase, reason=f"Your style rule says: {rule.rule}")
                )
    return issues


def output_guard(state: ApplicationState, deps: GraphDeps) -> dict:
    """List statements about the candidate in the new draft that no approved claim backs.

    If there are any and this was the first attempt, the graph goes back to `draft_materials`
    for one rewrite. Otherwise the draft goes to the user, with any remaining issues
    highlighted, and is saved as a new version in the `Draft` table.
    """
    drafts = state["drafts"]
    claims = state.get("claims", [])
    regenerate = state.get("regenerate")
    # After "regenerate one section", only that section is new; the other one may contain the
    # user's own edits, which are theirs to decide.
    text = drafts_as_text(drafts, regenerate.get("section") if regenerate else None)
    rejected = "\n".join(f"- {c.text}" for c in claims if c.user_decision == "removed")

    report = invoke_structured(
        deps.llm,
        GuardReport,
        output_guard_messages(
            text, claims_block(claims), basics_block(deps.get_profile()), rejected
        ),
    )
    issues = find_rejected_claims(text, claims) + find_avoided_phrases(
        text, deps.list_style_rules()
    )
    seen = {i.quote.lower() for i in issues}
    for item in report.unsupported:
        if item.quote.strip() and item.quote.lower() not in seen:
            issues.append(item)
            seen.add(item.quote.lower())

    if not needs_redraft({**state, "guard_issues": issues}) and state.get("job_id") is not None:
        deps.save_draft(state["job_id"], drafts, False)
    return {"guard_issues": issues}


def needs_redraft(state: ApplicationState) -> bool:
    """Edge rule after the guard: rewrite once if it found problems, then show the user."""
    return bool(state.get("guard_issues")) and state.get("draft_attempts", 0) < MAX_DRAFT_ATTEMPTS


def draft_review(state: ApplicationState) -> dict:
    """Pause and show the drafts; continue with accept or "regenerate this section".

    Resume with `Command(resume={"action": "accept", "edited": {...}})` or
    `{"action": "regenerate", "section": "cover_letter", "instruction": "shorter"}`. Edits
    the user made in the UI are kept in both cases.

    Raises:
        pydantic.ValidationError: the resume value is not a valid DraftReviewDecision.
    """
    answer = interrupt(
        {
            "type": "draft_review",
            "drafts": state["drafts"].model_dump(mode="json"),
            "guard_issues": [i.model_dump(mode="json") for i in state.get("guard_issues", [])],
            "options": ["accept", "regenerate"],
        }
    )
    decision = DraftReviewDecision.model_validate(answer)
    current = decision.edited or state["drafts"]

    if decision.action == "regenerate":
        return {
            "drafts": current,
            "regenerate": {
                "section": decision.section or "cover_letter",
                "instruction": decision.instruction or "",
            },
            "draft_attempts": 0,
            "guard_issues": [],
        }
    return {"user_edits": current, "regenerate": None}
