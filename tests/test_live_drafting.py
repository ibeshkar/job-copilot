# What: live end-to-end check of the Phase 5 flow with the real LLM: add the good-fit sample
#       job, continue, remove a claim, get drafts, accept, export a DOCX; and check that the
#       real output guard catches an invented claim.
# Why: the Phase 5 "done when". Costs a few cents, so it only runs with `uv run pytest -m live`.
# Used by: pytest (live marker).

import io
from pathlib import Path

import pytest
from docx import Document

from app.config import get_settings

GOOD_JOB = sorted((Path(__file__).resolve().parent.parent / "data/samples/jobs").glob("*.md"))[0]
FAKE_CLAIM = "I led a team of 50 engineers at Google for five years."


@pytest.fixture
def demo_app(app_db):
    if not get_settings().has_llm_key:
        pytest.skip("OPENROUTER_API_KEY not set")
    from app.memory.store import save_preferences, save_profile
    from app.services.onboarding import DEMO_PREFERENCES, DEMO_PROFILE, load_demo_documents

    save_profile(DEMO_PROFILE)
    save_preferences(DEMO_PREFERENCES)
    assert load_demo_documents().errors == {}


@pytest.mark.live
def test_full_flow_to_docx_without_removed_claim(demo_app) -> None:
    from app.agent.nodes.drafting import drafts_as_text, find_rejected_claims
    from app.services import applications

    added = applications.add_job(GOOD_JOB.read_text())
    assert added.ok, added.message
    job_id = added.job_id
    assert applications.submit_fit_review(job_id, "continue").ok

    claims = applications.get_workflow_view(job_id).claims
    assert len(claims) >= 3
    removed = next(c for c in claims if c.supported)  # remove a TRUE claim on purpose
    decisions = [
        {"id": c.id, "action": "remove" if c.id == removed.id else "approve"}
        for c in claims
        if c.supported
    ]
    result = applications.submit_claim_review(job_id, decisions)
    assert result.ok, result.message

    view = applications.get_workflow_view(job_id)
    assert view.stage == "draft_review"
    assert removed.id not in view.drafts.used_claim_ids
    # The removed claim's wording is not in the drafts. (Other guard issues may remain after
    # the one rewrite; then they are highlighted for the user, which is the designed outcome.)
    assert not find_rejected_claims(drafts_as_text(view.drafts), view.claims)
    assert applications.submit_draft_review(job_id, "accept").ok

    drafts = applications.latest_drafts(job_id)
    file = applications.export_drafts(job_id, drafts)
    assert file.file_name.endswith(".docx")
    text = "\n".join(p.text for p in Document(io.BytesIO(file.data)).paragraphs)
    assert drafts.cv_bullets[0] in text
    print(f"\nREMOVED: {removed.text}\nGUARD: {view.guard_issues}\n\n{drafts.cover_letter}")


@pytest.mark.live
def test_real_output_guard_flags_an_injected_fake_claim(demo_app) -> None:
    from app.agent.deps import default_deps
    from app.agent.nodes.drafting import output_guard
    from app.agent.state import Claim, Drafts

    claims = [
        Claim(
            id="C1",
            text="Built DocuMind, a RAG chatbot with LangChain and ChromaDB",
            evidence_chunk_ids=["documind.md#0"],
            supported=True,
            user_decision="approved",
        )
    ]
    drafts = Drafts(
        cv_bullets=["Built DocuMind, a RAG chatbot with LangChain and ChromaDB."],
        cover_letter=(
            "Dear Lumen Health team,\n\nI built DocuMind, a RAG chatbot with LangChain and "
            f"ChromaDB. {FAKE_CLAIM}\n\nBest regards, Alex Novak"
        ),
        used_claim_ids=["C1"],
    )
    state = {"drafts": drafts, "claims": claims, "draft_attempts": 2, "job_id": None}

    issues = output_guard(state, default_deps())["guard_issues"]

    assert any("Google" in issue.quote for issue in issues), issues
    assert not any("DocuMind" in issue.quote for issue in issues), issues
