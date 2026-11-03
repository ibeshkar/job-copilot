# What: the claim steps. `plan_claims` asks the LLM which statements about the candidate the
#       application should make, each citing evidence; `claim_check` pauses so the user can
#       approve, edit or remove each claim; `remember_confirmed_facts` saves what the user
#       confirmed to long-term memory.
# Why: this is the main defence against invented experience. Nothing reaches the drafts unless
#      the user approved it, and claims without real evidence start unchecked in the UI.
# Used by: app/agent/application_graph.py.

from langgraph.types import interrupt

from app.agent.deps import GraphDeps
from app.agent.llm import invoke_structured
from app.agent.prompts import claim_planner_messages
from app.agent.state import (
    ApplicationState,
    Claim,
    ClaimPlan,
    ClaimReviewDecision,
    Evidence,
)
from app.utils.errors import InvalidInputError
from app.utils.logging import get_logger

logger = get_logger(__name__)

MAX_CLAIMS = 10
FACT_ID_PREFIX = "fact-"


def _job_summary(state: ApplicationState) -> str:
    job = state["job"]
    return f"{job.title} at {job.company}. {job.summary}"


def collect_evidence(state: ApplicationState, facts: list[str]) -> dict[str, Evidence]:
    """All evidence a claim may cite: the fit card's cited chunks plus confirmed facts.

    Facts from earlier claim checks count as evidence because the user confirmed them, e.g.
    {"cv.md#2": Evidence(...), "fact-1": Evidence(doc_name="Fact you confirmed", ...)}.
    """
    found: dict[str, Evidence] = {}
    fit = state.get("fit")
    for match in fit.matches if fit else []:
        if match.status == "missing":
            continue
        for ev in match.evidence:
            found.setdefault(ev.chunk_id, ev)
    for i, text in enumerate(facts, start=1):
        found[f"{FACT_ID_PREFIX}{i}"] = Evidence(
            chunk_id=f"{FACT_ID_PREFIX}{i}",
            doc_name="Fact you confirmed earlier",
            snippet=text[:300],
            relevance=1.0,
        )
    return found


def plan_claims(state: ApplicationState, deps: GraphDeps) -> dict:
    """Propose claims for this job; mark each one supported only if its evidence is real.

    The model cites evidence ids. Python keeps only ids we actually gave it, so a made-up id
    can't make a claim look supported.
    """
    evidence = collect_evidence(state, deps.list_facts())
    status = {m.requirement_id: m.status for m in state["fit"].matches}
    requirements_block = "\n".join(
        f"{r.id} ({r.importance}, {status.get(r.id, 'missing')}): {r.text}"
        for r in state["requirements"]
    )
    evidence_block = "\n".join(
        f"[{ev.chunk_id}] ({ev.doc_name}) {ev.snippet}" for ev in evidence.values()
    )
    plan = invoke_structured(
        deps.llm,
        ClaimPlan,
        claim_planner_messages(
            _job_summary(state), requirements_block, evidence_block or "(no evidence)"
        ),
    )

    claims: list[Claim] = []
    for planned in plan.claims[:MAX_CLAIMS]:
        text = planned.text.strip()
        if not text:
            continue
        cited = [evidence[i] for i in dict.fromkeys(planned.evidence_ids) if i in evidence]
        claims.append(
            Claim(
                id=f"C{len(claims) + 1}",
                text=text,
                evidence_chunk_ids=[ev.chunk_id for ev in cited],
                supported=bool(cited),
                evidence=cited,
            )
        )
    return {"claims": claims}


def claim_check(state: ApplicationState) -> dict:
    """Pause and let the user approve, edit or remove each claim.

    Resume with `Command(resume={"decisions": [{"id": "C1", "action": "approve"}, ...]})`.
    A claim the user didn't decide on counts as removed: when in doubt, leave it out.

    Raises:
        pydantic.ValidationError: the resume value is not a valid ClaimReviewDecision.
    """
    answer = interrupt(
        {
            "type": "claim_check",
            "claims": [c.model_dump(mode="json") for c in state.get("claims", [])],
            "options": ["approve", "edit", "remove"],
        }
    )
    decisions = {d.id: d for d in ClaimReviewDecision.model_validate(answer).decisions}

    reviewed: list[Claim] = []
    for claim in state.get("claims", []):
        decision = decisions.get(claim.id)
        new_text = (decision.text or "").strip() if decision else ""
        if decision is None or decision.action == "remove":
            update = {"user_decision": "removed"}
        elif decision.action == "edit" and new_text and new_text != claim.text:
            update = {"user_decision": "edited", "text": new_text}
        elif decision.action == "edit" and not new_text:
            update = {"user_decision": "removed"}  # emptied the text box = remove
        else:
            update = {"user_decision": "approved"}
        reviewed.append(claim.model_copy(update=update))

    # A fresh round of drafting starts after every claim check.
    return {"claims": reviewed, "draft_attempts": 0, "guard_issues": [], "regenerate": None}


def remember_confirmed_facts(state: ApplicationState, deps: GraphDeps) -> dict:
    """Save to long-term memory what the user vouched for but the documents don't show.

    That is: kept claims without evidence, and claims the user reworded. Next time, they count
    as evidence ("Fact you confirmed earlier"), so the user isn't asked again. Exact
    duplicates of known facts are skipped.
    """
    known = {text.strip().lower() for text in deps.list_facts()}
    new_facts: list[str] = []
    for claim in state.get("claims", []):
        if not claim.kept:
            continue
        if claim.supported and claim.user_decision != "edited":
            continue  # already proven by the documents
        if claim.text.strip().lower() in known:
            continue
        try:
            deps.remember_fact(claim.text, state.get("job_id"))
        except InvalidInputError as exc:  # the store's rules said no: skip, don't break the run
            logger.warning("Fact not remembered: %s", exc)
            continue
        known.add(claim.text.strip().lower())
        new_facts.append(claim.text)
    return {"new_facts": new_facts}
