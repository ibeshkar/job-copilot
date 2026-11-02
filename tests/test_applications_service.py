# What: tests for app/services/applications.py, the layer between the pages and the graph.
# Why: it implements the interrupt pattern (start, read state, resume) and the board logic.
#      Everything runs offline with the fake graph from tests/graph_fakes.py.
# Used by: pytest.

import pytest

from app.agent.state import UNKNOWN_COMPANY, JobPostingExtraction, RequirementList
from app.db import repo
from app.services import applications
from app.ui.components import step_topic
from app.utils.errors import LLMServiceError
from tests.graph_fakes import EXTRACTION, POSTING, make_llm


def _jobs() -> list:
    with repo.session_scope() as session:
        return repo.list_jobs(session)


def test_add_job_runs_to_fit_review_and_updates_the_card(fake_app) -> None:
    progress: list[str] = []

    result = applications.add_job(POSTING, on_progress=progress.append)

    assert result.ok
    assert "Extracted 3 requirements" in progress
    assert "Scored the fit: 86/100" in progress
    view = applications.get_workflow_view(result.job_id)
    assert view.stage == "fit_review"
    card = applications.list_board()["saved"][0]
    assert (card.title, card.company, card.fit_score, card.stage) == (
        "Junior AI Engineer",
        "Lumen Health",
        86,
        "fit_review",
    )


def test_progress_says_what_runs_now_and_merges_the_search_retries(fake_app) -> None:
    progress: list[str] = []

    applications.add_job(POSTING, on_progress=progress.append)

    running = [p for p in progress if p.endswith("…")]
    finished = [p for p in progress if not p.endswith("…")]
    assert running[:3] == [
        "Checking your input…",
        "Reading the posting…",
        "Extracting the requirements…",
    ]
    # The fake search finds nothing for one requirement, so the agent retries twice.
    retry = "Searched your documents: nothing found for some requirements, trying other search"
    assert f"{retry} words (2/2)…" in running
    assert finished[-2] == "Searched your documents: evidence for 2 requirements (after retry 2/2)"
    # Each retry updates the "Searched your documents" line: one line per step, 5 in total.
    assert len({step_topic(p) for p in finished}) == applications.FIT_ANALYSIS_STEPS


def test_unnamed_company_gets_a_fallback_the_user_can_correct(fake_app) -> None:
    no_company = EXTRACTION.model_copy(update={"company": "  "})
    fake_app.llm.responders[JobPostingExtraction] = no_company
    progress: list[str] = []

    job_id = applications.add_job(POSTING, on_progress=progress.append).job_id

    assert applications.get_job_summary(job_id).company == UNKNOWN_COMPANY
    read = next(p for p in progress if p.startswith("Read the posting"))
    assert " at " not in read and "isn't named" in read
    assert applications.rename_job(job_id, "", "Lumen Health")
    assert not applications.rename_job(job_id, "", "Lumen Health")  # nothing changed
    # Later steps copy the posting onto the card again; the correction must survive that.
    applications.submit_fit_review(job_id, "continue")
    card = applications.get_job_summary(job_id)
    assert (card.title, card.company) == ("Junior AI Engineer", "Lumen Health")


def test_rejected_input_removes_the_card(fake_app) -> None:
    result = applications.add_job("Python job")

    assert not result.ok
    assert "too short" in result.message
    assert _jobs() == []


def test_no_api_key_gives_a_friendly_message(fake_app, monkeypatch) -> None:
    from app.config import get_settings

    # Settings would fall back to the developer's .env, so remove the key on the object itself.
    monkeypatch.setattr(get_settings(), "openrouter_api_key", None)

    result = applications.add_job(POSTING)

    assert not result.ok and "API key" in result.message
    assert _jobs() == []


def test_outage_keeps_the_card_and_retry_continues(fake_app) -> None:
    def busy(messages):
        raise LLMServiceError("503")

    fake_app.llm = make_llm({RequirementList: busy})
    result = applications.add_job(POSTING)

    assert not result.ok
    assert result.message == "The AI service is busy, please retry."
    assert applications.get_workflow_view(result.job_id).stage == "interrupted"

    fake_app.llm = make_llm()  # the service is back
    retry = applications.retry_analysis(result.job_id)

    assert retry.ok
    assert applications.get_workflow_view(result.job_id).stage == "fit_review"
    # Retry continued from the saved step: the posting wasn't extracted a second time.
    assert fake_app.llm.schemas_called()[0] == "RequirementList"


def test_not_a_fit_finishes_the_run(fake_app) -> None:
    job_id = applications.add_job(POSTING).job_id

    result = applications.submit_fit_review(job_id, "stop")

    assert result.ok
    view = applications.get_workflow_view(job_id)
    assert (view.stage, view.review_action) == ("finished", "stop")
    again = applications.submit_fit_review(job_id, "continue")
    assert not again.ok and "isn't waiting" in again.message


def test_add_context_rechecks_and_pauses_again(fake_app) -> None:
    job_id = applications.add_job(POSTING).job_id

    result = applications.submit_fit_review(
        job_id, "add_context", text="I run Kubernetes clusters at home."
    )

    assert result.ok
    view = applications.get_workflow_view(job_id)
    assert view.stage == "fit_review"
    assert view.fit.score == 100
    assert applications.get_job_summary(job_id).fit_score == 100


def test_invalid_action_is_rejected(fake_app) -> None:
    job_id = applications.add_job(POSTING).job_id

    assert not applications.submit_fit_review(job_id, "hire me").ok


def test_change_status_and_delete(fake_app) -> None:
    job_id = applications.add_job(POSTING).job_id

    applications.change_status(job_id, "applied")
    assert [c.id for c in applications.list_board()["applied"]] == [job_id]
    with pytest.raises(ValueError):
        applications.change_status(job_id, "hired")

    applications.delete_job(job_id)
    assert _jobs() == []
    assert (
        applications.get_graph().get_state({"configurable": {"thread_id": f"job-{job_id}"}}).values
        == {}
    )


def test_sample_jobs() -> None:
    samples = applications.sample_jobs()

    assert len(samples) == 3
    assert next(iter(samples)).startswith("Good fit: Junior AI Engineer")


# ---------------------------------------------------------------- Phase 5: claims and drafts

KEEP_TWO = [
    {"id": "C1", "action": "approve"},
    {"id": "C2", "action": "approve"},
    {"id": "C3", "action": "remove"},
]


def _to_draft_review() -> int:
    job_id = applications.add_job(POSTING).job_id
    assert applications.submit_fit_review(job_id, "continue").ok
    assert applications.submit_claim_review(job_id, KEEP_TWO).ok
    return job_id


def test_full_flow_from_claim_check_to_saved_versions(fake_app) -> None:
    job_id = applications.add_job(POSTING).job_id
    progress: list[str] = []

    applications.submit_fit_review(job_id, "continue", on_progress=progress.append)
    view = applications.get_workflow_view(job_id)
    assert view.stage == "claim_check"
    assert "Planned 3 claims about you (2 backed by your documents)" in progress
    assert [c.supported for c in view.claims] == [True, True, False]

    applications.submit_claim_review(job_id, KEEP_TWO, on_progress=progress.append)
    view = applications.get_workflow_view(job_id)
    assert view.stage == "draft_review"
    assert any(p.startswith("Fact check: passed") for p in progress)
    assert [v.version for v in applications.list_draft_versions(job_id)] == [1]

    edited = view.drafts.model_copy(update={"cv_bullets": ["My own bullet."]})
    assert applications.submit_draft_review(job_id, "accept", edited=edited).ok

    assert applications.get_workflow_view(job_id).stage == "finished"
    versions = applications.list_draft_versions(job_id)
    assert [(v.version, v.is_user_edited) for v in versions] == [(2, True), (1, False)]
    assert applications.latest_drafts(job_id).cv_bullets == ["My own bullet."]


def test_removing_every_claim_is_refused(fake_app) -> None:
    job_id = applications.add_job(POSTING).job_id
    applications.submit_fit_review(job_id, "continue")

    result = applications.submit_claim_review(job_id, [{"id": "C1", "action": "remove"}])

    assert not result.ok and "at least one claim" in result.message
    assert applications.get_workflow_view(job_id).stage == "claim_check"


def test_regenerate_section_returns_to_draft_review(fake_app) -> None:
    job_id = _to_draft_review()

    result = applications.submit_draft_review(
        job_id, "regenerate", section="cover_letter", instruction="shorter"
    )

    assert result.ok
    assert applications.get_workflow_view(job_id).stage == "draft_review"
    assert len(applications.list_draft_versions(job_id)) == 2


def test_edits_after_acceptance_rating_and_export(fake_app) -> None:
    job_id = _to_draft_review()
    applications.submit_draft_review(job_id, "accept")
    drafts = applications.latest_drafts(job_id)

    assert not applications.save_edited_drafts(job_id, drafts)  # nothing changed
    letter = drafts.cover_letter.model_copy(update={"body_paragraphs": ["A new letter."]})
    changed = drafts.model_copy(update={"cover_letter": letter})
    assert applications.save_edited_drafts(job_id, changed)
    assert applications.latest_drafts(job_id).cover_letter == letter

    applications.rate_drafts(job_id, "letter", -1, "too long")
    with repo.session_scope() as session:
        assert [(f.target, f.rating) for f in repo.list_feedback(session)] == [("letter", -1)]

    file = applications.export_cover_letter(job_id, changed.cover_letter, "pdf")
    assert file.file_name == "CoverLetter_LumenHealth.pdf"  # no profile saved
    assert file.data.startswith(b"%PDF") and not file.fell_back
