# What: live checks of Phase 8 with the real LLM: the Phase 8 "done when" (remove "passionate"
#       from a letter → a style rule → the next letter doesn't use it), one round of the mock
#       interview, and one help-bot answer.
# Why: the offline tests use scripted answers; these show the real prompts do the job. They cost
#      a few cents, so they only run with `uv run pytest -m live`.
# Used by: pytest (live marker).

from pathlib import Path

import pytest

from app.config import get_settings

GOOD_JOB = sorted((Path(__file__).resolve().parent.parent / "data/samples/jobs").glob("*.md"))[0]
PASSIONATE = "I am passionate about building AI products that help people."


@pytest.fixture
def demo_app(app_db):
    if not get_settings().has_llm_key:
        pytest.skip("OPENROUTER_API_KEY not set")
    from app.memory.store import save_preferences, save_profile
    from app.services.onboarding import DEMO_PREFERENCES, DEMO_PROFILE, load_demo_documents

    save_profile(DEMO_PROFILE)
    save_preferences(DEMO_PREFERENCES)
    assert load_demo_documents().errors == {}


def _to_drafts(job_text: str) -> int:
    from app.services import applications

    added = applications.add_job(job_text)
    assert added.ok, added.message
    assert applications.submit_fit_review(added.job_id, "continue").ok
    claims = applications.get_workflow_view(added.job_id).claims
    decisions = [{"id": c.id, "action": "approve"} for c in claims if c.supported]
    assert applications.submit_claim_review(added.job_id, decisions).ok
    return added.job_id


@pytest.mark.live
def test_done_when_passionate_is_learned_and_avoided(demo_app) -> None:
    from app.memory import store
    from app.services import applications

    first = _to_drafts(GOOD_JOB.read_text())
    drafts = applications.get_workflow_view(first).drafts
    # The AI's letter as if it had said "passionate", and the user's edit that removes it.
    letter = drafts.cover_letter
    paragraphs = [f"{PASSIONATE} {letter.body_paragraphs[0]}", *letter.body_paragraphs[1:]]
    ai_letter = letter.model_copy(update={"body_paragraphs": paragraphs})
    # Written "as" the output guard; continuing the run pauses at the draft review again.
    applications.get_graph().update_state(
        applications.job_config(first),
        {"drafts": drafts.model_copy(update={"cover_letter": ai_letter})},
        as_node="output_guard",
    )
    assert applications.retry_analysis(first).ok
    assert applications.get_workflow_view(first).stage == "draft_review"
    edited = drafts.model_copy(update={"cover_letter": letter})  # the user removed it
    assert applications.submit_draft_review(first, "accept", edited=edited).ok

    rules = list(store.list_style_rules().values())
    print("Learned:", [(r.rule, r.avoid_phrases) for r in rules])
    assert any("passionate" in [p.lower() for p in r.avoid_phrases] for r in rules)

    second = _to_drafts(GOOD_JOB.read_text())
    letter = applications.get_workflow_view(second).drafts.cover_letter
    assert "passionate" not in letter.text.lower()


@pytest.mark.live
def test_one_interview_round(demo_app) -> None:
    from app.services import applications, interview

    added = applications.add_job(GOOD_JOB.read_text())
    assert added.ok, added.message
    assert interview.start(added.job_id).ok
    view = interview.get_view(added.job_id)
    assert view.stage == "question" and view.current.kind == "behavioural"
    print("Q1:", view.current.question)

    result = interview.answer(
        added.job_id,
        "At Turing College I built DocuMind, a RAG chatbot over our course notes, with two "
        "classmates. I wrote the retrieval part with LangChain and ChromaDB.",
    )
    assert result.ok, result.message
    turn = interview.get_view(added.job_id).turns[0]
    print("Feedback:", turn.feedback.model_dump())
    assert 1 <= turn.feedback.score <= 5
    assert turn.feedback.improved_answer
    assert interview.end_early(added.job_id).ok
    assert interview.get_view(added.job_id).summary is not None


@pytest.mark.live
def test_help_bot_answers_from_the_guide(demo_app) -> None:
    from app.services import help as help_bot

    reply = help_bot.ask("How do I undo a style rule the app learned?")
    print("Help:", reply)
    assert reply.ok and reply.covered_by_guide
    assert "undo" in reply.answer.lower()

    off_guide = help_bot.ask("What is the capital of Australia?")
    assert off_guide.ok and not off_guide.covered_by_guide
