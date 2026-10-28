# What: reusable Streamlit widgets: empty states, status/fit badges and the fit card.
# Why: the board and the workspace show the same badges, and keeping the fit card here keeps
#      the page files short. These functions only draw; they never call the LLM or the graph.
# Used by: the pages in app/ui/pages/.

import streamlit as st

from app.agent.state import FitReport, JobPosting, Requirement

STATUS_LABELS = {
    "saved": "🔖 Saved",
    "applied": "📨 Applied",
    "interview": "🎤 Interview",
    "offer": "🎉 Offer",
    "rejected": "✖️ Rejected",
}
MATCH_ICONS = {"met": "✅", "partial": "⚠️", "missing": "❌"}


def coming_soon(phase: int, what: str) -> None:
    """Show an empty state for a feature that is planned but not built yet.

    Args:
        phase: the implementation phase (PROJECT_PLAN.md §12) that builds it.
        what: one sentence describing what it will do.
    """
    st.info(f"{what}\n\nThis is built in **Phase {phase}**.", icon="🚧")


def fit_badge(score: int | None) -> str:
    """A coloured score label in Streamlit markdown, e.g. ':green-badge[84 · strong fit]'."""
    if score is None:
        return ":gray-badge[no score yet]"
    if score >= 70:
        return f":green-badge[{score} · strong fit]"
    if score >= 40:
        return f":orange-badge[{score} · partial fit]"
    return f":red-badge[{score} · weak fit]"


def stage_badge(stage: str, review_action: str | None) -> str:
    """A badge for where the job's workflow stands (empty string if nothing to say)."""
    if stage == "fit_review":
        return ":blue-badge[👀 review the fit]"
    if stage in ("interrupted", "not_started"):
        return ":orange-badge[⏳ analysis not finished]"
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
) -> None:
    """Draw the fit card: score, red flags, and the requirement list with evidence."""
    left, right = st.columns([1, 3])
    with left:
        st.metric("Fit score", f"{fit.score}/100")
        st.progress(fit.score / 100)
        st.markdown(fit_badge(fit.score))
    with right:
        st.markdown(f"#### {job.title} · {job.company}")
        st.caption(_job_facts(job))
        st.write(fit.strengths_summary)
        counts = {s: sum(1 for m in fit.matches if m.status == s) for s in MATCH_ICONS}
        st.markdown(
            f"✅ {counts['met']} met · ⚠️ {counts['partial']} partial · "
            f"❌ {counts['missing']} missing"
        )

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

    st.markdown("##### Requirements")
    by_id = {m.requirement_id: m for m in fit.matches}
    ordered = sorted(requirements, key=lambda r: (r.importance != "must", r.id))
    for req in ordered:
        match = by_id.get(req.id)
        if match is None:
            continue
        label = f"{MATCH_ICONS[match.status]} {req.text} · {req.importance}"
        with st.expander(label):
            st.write(match.explanation)
            if match.evidence:
                for ev in match.evidence:
                    st.markdown(f"**📄 {ev.doc_name}** · relevance {ev.relevance:.0%}")
                    st.markdown(f"> {ev.snippet}")
            else:
                st.caption("No evidence found in your documents.")
            if match.honest_positioning:
                st.info(match.honest_positioning, icon="💡")

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
