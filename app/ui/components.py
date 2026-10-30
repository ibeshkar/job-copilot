# What: reusable Streamlit widgets: status/fit/salary badges, the fit card with its
#       requirements summary, filter and compact requirement rows, claim
#       badges and the reviewed-claims list, the fact-check warning, draft history, and the
#       profile and preferences forms.
# Why: the board and the workspace show the same badges, and onboarding and the Memory page
#      share the forms. These functions only draw; they never call the LLM or the graph.
# Used by: the pages in app/ui/pages/.

import streamlit as st

from app.agent.state import (
    Claim,
    FitReport,
    JobPosting,
    Requirement,
    RequirementMatch,
    SalaryEstimate,
    UnsupportedStatement,
)
from app.memory.store import Preferences, Profile

STATUS_LABELS = {
    "saved": "🔖 Saved",
    "applied": "📨 Applied",
    "interview": "🎤 Interview",
    "offer": "🎉 Offer",
    "rejected": "✖️ Rejected",
}
MATCH_ICONS = {"met": "✅", "partial": "⚠️", "missing": "❌"}
MATCH_NAMES = {"met": "Met", "partial": "Partial", "missing": "Missing"}
MATCH_COLORS = {"met": "green", "partial": "orange", "missing": "red"}
# Gaps first and open, met last and closed: the user's attention goes where the work is.
STATUS_GROUPS = [("missing", True), ("partial", True), ("met", False)]  # (status, expanded)
REQUIREMENT_FILTERS = {"all": "All", "gaps": "Gaps only", "must": "Must-have only"}
DECISION_ICONS = {"approved": "✅", "edited": "✏️", "removed": "🗑️", "pending": "⏳"}
SENIORITIES = ["intern", "junior", "mid", "senior", "lead"]
TONES = ["formal", "friendly", "concise"]
TONE_LABELS = {"formal": "🎩 Formal", "friendly": "😊 Friendly", "concise": "✂️ Concise"}
LENGTHS = ["short", "medium", "long"]


def fit_badge(score: int | None) -> str:
    """A coloured score label in Streamlit markdown, e.g. ':green-badge[84 · strong fit]'."""
    if score is None:
        return ":gray-badge[no score yet]"
    if score >= 70:
        return f":green-badge[{score} · strong fit]"
    if score >= 40:
        return f":orange-badge[{score} · partial fit]"
    return f":red-badge[{score} · weak fit]"


CONFIDENCE_BADGES = {
    "high": ":green-badge[high confidence]",
    "medium": ":orange-badge[medium confidence]",
    "low": ":red-badge[low confidence]",
}


def thousands(amount: int) -> str:
    """Short money amount, e.g. 52500 → "52.5k", 61000 → "61k"."""
    return f"{amount / 1000:.1f}".removesuffix(".0") + "k"


def salary_badge(estimate: SalaryEstimate | None) -> str:
    """A board badge like ':violet-badge[💰 €52k–61k]', or "" if there is no estimate."""
    if estimate is None:
        return ""
    if estimate.status != "ok":
        return ":gray-badge[💰 no salary data]"
    return f":violet-badge[💰 €{thousands(estimate.low)}–{thousands(estimate.high)}]"


def stage_badge(stage: str, review_action: str | None) -> str:
    """A badge for where the job's workflow stands (empty string if nothing to say)."""
    if stage == "fit_review":
        return ":blue-badge[👀 review the fit]"
    if stage in ("interrupted", "not_started"):
        return ":orange-badge[⏳ analysis not finished]"
    if stage == "claim_check":
        return ":blue-badge[✅ check the claims]"
    if stage == "draft_review":
        return ":blue-badge[📝 review the drafts]"
    if stage == "failed":
        return ":red-badge[analysis failed]"
    if review_action == "stop":
        return ":gray-badge[🚫 not a fit]"
    return ""


def _job_facts(job: JobPosting) -> str:
    facts = [
        job.location or "location not stated",
        job.remote_policy if job.remote_policy != "unknown" else None,
        f"{job.seniority} level" if job.seniority != "unknown" else None,
        job.salary_range,
        {"yes": "visa sponsorship offered", "no": "no visa sponsorship"}.get(job.visa_sponsorship),
    ]
    return " · ".join(f for f in facts if f)


def fit_card(
    job: JobPosting,
    requirements: list[Requirement],
    fit: FitReport,
    warnings: list[str],
    extra_context: str | None = None,
    key: str = "fit",
) -> None:
    """Draw the fit card: score, red flags, and the requirements (summary, filter, groups).

    `key` makes the widget keys unique per job, e.g. "fit_3".
    """
    left, right = st.columns([1, 3])
    with left:
        st.metric("Fit score", f"{fit.score}/100")
        st.progress(fit.score / 100)
        st.markdown(fit_badge(fit.score))
    with right:
        st.markdown(f"#### {job.title} · {job.company}")
        st.caption(_job_facts(job))
        st.write(fit.strengths_summary)

    for flag in fit.red_flags:
        if flag.blocking:
            st.error(f"**Blocking:** {flag.message}", icon="⛔")
        else:
            st.warning(flag.message, icon="⚠️")
    for warning in warnings:
        st.info(warning, icon="🛡️")

    if fit.matches and all(m.status == "missing" for m in fit.matches):
        st.warning(
            "I found no evidence for any requirement in your documents. If you do have this "
            "experience, use **Add context** below or upload more documents.",
            icon="🔎",
        )

    requirements_section(requirements, fit, key=key)

    if extra_context:
        with st.expander("📝 Context you added"):
            st.write(extra_context)
    if fit.questions_to_ask_employer:
        with st.expander("❓ Questions to ask the employer"):
            for question in fit.questions_to_ask_employer:
                st.write(f"- {question}")
    with st.expander("🧮 How is the score calculated?"):
        st.markdown(
            "Each requirement is weighted: **must-have = 3**, **nice-to-have = 1**. "
            "It earns **met = 1**, **partial = 0.5**, **missing = 0** of its weight.  \n"
            "Score = 100 × earned ÷ total weight, rounded. A **blocking** red flag (like a "
            "required language you don't speak well enough) caps the score at **40**.  \n"
            "The AI only decides met / partial / missing; the arithmetic is plain Python, so "
            "the same assessment always gives the same score."
        )


# ---------------------------------------------------------------- requirements

Row = tuple[Requirement, RequirementMatch]


def match_rows(requirements: list[Requirement], fit: FitReport) -> list[Row]:
    """Each requirement with its match, in the posting's order; unmatched ones are skipped."""
    by_id = {m.requirement_id: m for m in fit.matches}
    return [(r, by_id[r.id]) for r in requirements if r.id in by_id]


def group_requirements(rows: list[Row], view: str = "all") -> dict[str, list[Row]]:
    """Filter the rows and group them by status: missing → partial → met.

    view: "all", "gaps" (missing + partial) or "must" (must-haves only). Inside a group
    must-haves come first, otherwise the posting's order is kept. Empty groups are left out.
    """
    if view == "gaps":
        rows = [(r, m) for r, m in rows if m.status != "met"]
    elif view == "must":
        rows = [(r, m) for r, m in rows if r.importance == "must"]
    groups = {}
    for status, _ in STATUS_GROUPS:
        members = [(r, m) for r, m in rows if m.status == status]
        if members:
            groups[status] = sorted(members, key=lambda row: row[0].importance != "must")
    return groups


def status_badge(status: str, count: int | None = None) -> str:
    """A coloured status badge, e.g. ':red-badge[❌ 2 missing]' or ':green-badge[✅ Met]'."""
    text = f"{count} {MATCH_NAMES[status].lower()}" if count is not None else MATCH_NAMES[status]
    return f":{MATCH_COLORS[status]}-badge[{MATCH_ICONS[status]} {text}]"


def importance_badge(importance: str) -> str:
    return ":violet-badge[MUST]" if importance == "must" else ":gray-badge[nice]"


def requirements_summary(rows: list[Row]) -> None:
    """ "Must-have met: X / Y" with a progress bar, and the met / partial / missing counts."""
    musts = [m for r, m in rows if r.importance == "must"]
    met = sum(m.status == "met" for m in musts)
    partial = sum(m.status == "partial" for m in musts)
    if musts:
        extra = f" (+{partial} partial)" if partial else ""
        st.markdown(f"**Must-have met: {met} / {len(musts)}**{extra}")
        st.progress(met / len(musts))
    else:
        st.caption("The posting lists no must-have requirements.")
    counts = {status: sum(m.status == status for _, m in rows) for status in MATCH_ICONS}
    st.markdown(" ".join(status_badge(s, counts[s]) for s in ("met", "partial", "missing")))


def _details(req: Requirement, match: RequirementMatch, key: str) -> None:
    """The row's details button: evidence (met / partial) and the honest tip (missing / partial)."""
    show_evidence = match.status in ("met", "partial")
    show_tip = match.status in ("missing", "partial")
    label = {"met": "📄 Evidence", "missing": "💡 Tip", "partial": "📄 Details"}[match.status]
    has_content = (show_evidence and match.evidence) or (show_tip and match.honest_positioning)
    with st.popover(label, key=key, disabled=not has_content, width="stretch"):
        st.markdown(f"**{req.text}**")
        if show_evidence:
            for ev in match.evidence:
                st.markdown(f"**📄 {ev.doc_name}** · relevance {ev.relevance:.0%}")
                st.markdown(f"> {ev.snippet}")
            if not match.evidence:
                st.caption("No evidence found in your documents.")
        if show_tip and match.honest_positioning:
            st.info(match.honest_positioning, icon="💡")


def requirement_row(req: Requirement, match: RequirementMatch, key: str) -> None:
    """One compact row: "icon requirement MUST/nice" with the explanation below, and a details
    button on the right.

    Two columns that don't wrap (`wrap=False`), so on a phone the button stays next to the
    text instead of the row breaking into four stacked blocks.
    """
    text, details = st.columns([5, 1.6], vertical_alignment="center", wrap=False)
    with text:
        st.markdown(f"{MATCH_ICONS[match.status]} {req.text} {importance_badge(req.importance)}")
        st.caption(match.explanation)
    with details:
        _details(req, match, key)


def requirements_section(requirements: list[Requirement], fit: FitReport, key: str) -> None:
    """Summary, a filter, then one expander per status group with compact rows (no nesting)."""
    st.markdown("##### Requirements")
    rows = match_rows(requirements, fit)
    if not rows:
        st.caption("No requirements found in the posting.")
        return
    requirements_summary(rows)
    view = st.segmented_control(
        "Show",
        list(REQUIREMENT_FILTERS),
        default="all",
        required=True,
        format_func=REQUIREMENT_FILTERS.get,
        key=f"{key}_req_filter",
        label_visibility="collapsed",
    )
    groups = group_requirements(rows, view or "all")
    if not groups:
        st.caption("Nothing to show with this filter.")
    for status, expanded in STATUS_GROUPS:
        if status not in groups:
            continue
        label = f"{MATCH_ICONS[status]} {MATCH_NAMES[status]} ({len(groups[status])})"
        with st.expander(label, expanded=expanded, key=f"{key}_req_group_{status}"):
            for req, match in groups[status]:
                requirement_row(req, match, key=f"{key}_req_{req.id}")


# ---------------------------------------------------------------- claims and drafts


def claim_badge(claim: Claim) -> str:
    """Green if documents back the claim, orange if only the user can vouch for it."""
    if claim.supported:
        return ":green-badge[backed by your documents]"
    return ":orange-badge[no evidence in your documents]"


def claim_evidence(claim: Claim) -> None:
    """The evidence quotes a claim cites (inside an expander)."""
    if not claim.evidence:
        st.caption("No document mentions this. Keep it only if it's true.")
    for ev in claim.evidence:
        st.markdown(f"**📄 {ev.doc_name}**")
        st.markdown(f"> {ev.snippet}")


def reviewed_claims(claims: list[Claim], new_facts: list[str]) -> None:
    """Read-only list of the claims after the claim check, with the user's decisions."""
    for claim in claims:
        text = f"~~{claim.text}~~" if claim.user_decision == "removed" else claim.text
        st.markdown(f"{DECISION_ICONS[claim.user_decision]} {text} {claim_badge(claim)}")
    if new_facts:
        st.info(
            "I remembered these as facts you confirmed (see them on the Memory page):\n"
            + "\n".join(f"- {fact}" for fact in new_facts),
            icon="🧠",
        )


def guard_issues_box(issues: list[UnsupportedStatement]) -> None:
    """Highlight statements the fact-checker couldn't match to an approved claim."""
    if not issues:
        st.success(
            "Fact check passed: every statement about you is backed by a claim you approved.",
            icon="🔎",
        )
        return
    lines = "\n".join(f'- "{i.quote}": {i.reason}' for i in issues)
    st.warning(
        "**Please check these.** The fact-checker couldn't match them to a claim you approved, "
        f"or your style rules ban them. Edit or delete them unless they're fine.\n\n{lines}",
        icon="🔎",
    )


# ---------------------------------------------------------------- profile and preferences


def split_list(text: str) -> list[str]:
    """Turn "Berlin, Remote" into ["Berlin", "Remote"]."""
    return [part.strip() for part in text.split(",") if part.strip()]


def profile_form(profile: Profile, key: str, submit_label: str) -> Profile | None:
    """The profile form. Returns the new Profile when submitted and valid, else None."""
    with st.form(key):
        name = st.text_input("Your name *", value=profile.name)
        roles = st.text_input(
            "Target roles (comma-separated)",
            value=", ".join(profile.target_roles),
            placeholder="Junior AI Engineer, Data Analyst",
        )
        seniority = st.selectbox(
            "Seniority", SENIORITIES, index=SENIORITIES.index(profile.seniority)
        )
        locations = st.text_input(
            "Locations (comma-separated)",
            value=", ".join(profile.locations),
            placeholder="Berlin, Remote (EU)",
        )
        work_auth = st.text_input(
            "Work authorisation",
            value=profile.work_authorisation,
            placeholder="EU citizen / Blue Card / needs visa sponsorship",
        )
        languages = st.text_input(
            "Languages with level (comma-separated)",
            value=", ".join(profile.languages),
            placeholder="English C1, German A2",
        )
        salary = st.text_input(
            "Salary expectations (optional)", value=profile.salary_expectations or ""
        )
        submitted = st.form_submit_button(submit_label, type="primary")

    if not submitted:
        return None
    if not name.strip():
        st.error("Please enter your name.")
        return None
    return Profile(
        name=name.strip(),
        target_roles=split_list(roles),
        seniority=seniority,
        locations=split_list(locations),
        work_authorisation=work_auth.strip(),
        languages=split_list(languages),
        salary_expectations=salary.strip() or None,
    )


def preferences_form(prefs: Preferences, key: str, submit_label: str) -> Preferences | None:
    """The writing-preferences form. Returns the new Preferences when submitted, else None."""
    with st.form(key):
        tone = st.radio("Tone", TONES, index=TONES.index(prefs.tone), horizontal=True)
        length = st.radio(
            "Cover letter length",
            LENGTHS,
            index=LENGTHS.index(prefs.letter_length),
            horizontal=True,
        )
        language = st.text_input("Language of letters", value=prefs.letter_language)
        deal_breakers = st.text_input(
            "Deal-breakers (comma-separated)",
            value=", ".join(prefs.deal_breakers),
            placeholder="No relocation, no unpaid overtime",
        )
        submitted = st.form_submit_button(submit_label, type="primary")

    if not submitted:
        return None
    return Preferences(
        tone=tone,
        letter_length=length,
        letter_language=language.strip() or "Same as the job posting",
        deal_breakers=split_list(deal_breakers),
    )
