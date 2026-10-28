# What: headless UI tests (Streamlit AppTest) for the Board and the Job workspace, using the
#       offline fake graph.
# Why: checks the Phase 4 "done when": a job added from the UI shows its fit card, and a
#      browser refresh (a new AppTest session with the same ?job= URL) shows it again. Phase 5
#      adds the claim check form, the draft review and the notes; Phase 6 the copilot chat
#      (starter prompts, tool expanders, the approval card), tool toggles and the Company tab.
# Used by: pytest.

from pathlib import Path

from streamlit.testing.v1 import AppTest

from app.memory.store import Profile, save_profile
from app.services import applications
from tests.graph_fakes import POSTING

MAIN = str(Path(__file__).resolve().parent.parent / "app" / "main.py")


def _app(job_id: int | None = None) -> AppTest:
    at = AppTest.from_file(MAIN, default_timeout=30)
    if job_id is not None:
        at.query_params["job"] = str(job_id)
    return at


def _open_workspace(at: AppTest) -> AppTest:
    at.switch_page("ui/pages/2_job_workspace.py")
    return at.run()


def _button(at: AppTest, label: str):
    return next(b for b in at.button if b.label == label)


def test_empty_board_explains_what_to_do(fake_app) -> None:
    save_profile(Profile(name="Alex"))

    at = _app().run()

    assert not at.exception
    assert any("No jobs yet" in info.value for info in at.info)


def test_add_job_dialog_runs_the_analysis(fake_app) -> None:
    save_profile(Profile(name="Alex"))
    at = _app().run()

    _button(at, "➕ Add job").click().run()
    at.text_area[0].input(POSTING).run()
    _button(at, "Analyse fit").click().run()

    assert not at.exception
    jobs = applications.list_board()["saved"]
    assert [(j.title, j.fit_score) for j in jobs] == [("Junior AI Engineer", 86)]


def test_board_card_shows_score_and_review_badge(fake_app) -> None:
    save_profile(Profile(name="Alex"))
    applications.add_job(POSTING)

    at = _app().run()

    text = " ".join(m.value for m in at.markdown)
    assert "Junior AI Engineer" in text
    assert "86 · strong fit" in text
    assert "review the fit" in text


def test_workspace_shows_fit_card_and_survives_refresh(fake_app) -> None:
    save_profile(Profile(name="Alex"))
    job_id = applications.add_job(POSTING).job_id

    at = _open_workspace(_app(job_id))
    assert not at.exception
    assert at.metric[0].value == "86/100"
    assert any(e.label.startswith("❌ Kubernetes") for e in at.expander)

    # A browser refresh is a brand-new session with the same URL.
    refreshed = _open_workspace(_app(job_id))
    assert refreshed.metric[0].value == "86/100"
    assert _button(refreshed, "Continue to application ▶")


def test_not_a_fit_button_resumes_the_graph(fake_app) -> None:
    save_profile(Profile(name="Alex"))
    job_id = applications.add_job(POSTING).job_id
    at = _open_workspace(_app(job_id))

    _button(at, "🚫 Not a fit").click().run()

    assert not at.exception
    assert any("not a fit" in info.value for info in at.info)
    assert applications.get_workflow_view(job_id).review_action == "stop"


def test_add_context_button_rechecks(fake_app) -> None:
    save_profile(Profile(name="Alex"))
    job_id = applications.add_job(POSTING).job_id
    at = _open_workspace(_app(job_id))

    at.text_area(key=f"context_{job_id}").input("I run Kubernetes clusters at home.").run()
    _button(at, "Re-check with this context").click().run()

    assert not at.exception
    assert at.metric[0].value == "100/100"


def test_workspace_without_jobs_points_to_board(fake_app) -> None:
    save_profile(Profile(name="Alex"))

    at = _open_workspace(_app())

    assert any("No jobs yet" in info.value for info in at.info)


# ---------------------------------------------------------------- Phase 5: claims and drafts


def _at_claim_check() -> int:
    save_profile(Profile(name="Alex"))
    job_id = applications.add_job(POSTING).job_id
    applications.submit_fit_review(job_id, "continue")
    return job_id


def test_claim_check_starts_unsupported_claims_unticked(fake_app) -> None:
    job_id = _at_claim_check()

    at = _open_workspace(_app(job_id))

    assert not at.exception
    assert [at.checkbox(key=f"keep_{job_id}_C{i}").value for i in (1, 2, 3)] == [
        True,
        True,
        False,
    ]
    assert any("no evidence" in w.value for w in at.warning)


def test_claim_check_to_accepted_drafts(fake_app) -> None:
    job_id = _at_claim_check()
    at = _open_workspace(_app(job_id))

    at.checkbox(key=f"keep_{job_id}_C2").uncheck()
    _button(at, "Confirm claims and write drafts ▶").click().run()

    assert not at.exception
    view = applications.get_workflow_view(job_id)
    assert view.stage == "draft_review"
    assert "RAG chatbot" not in view.drafts.cover_letter  # the unticked claim is not used
    letter = at.text_area(key=f"letter_{job_id}_1")
    assert letter.value == view.drafts.cover_letter
    assert any("Fact check passed" in s.value for s in at.success)

    letter.input(letter.value + "\nP.S. I can start in May.").run()
    _button(at, "✅ Accept & save").click().run()

    assert not at.exception
    assert applications.get_workflow_view(job_id).stage == "finished"
    assert applications.latest_drafts(job_id).cover_letter.endswith("I can start in May.")


def test_notes_are_saved(fake_app) -> None:
    save_profile(Profile(name="Alex"))
    job_id = applications.add_job(POSTING).job_id
    at = _open_workspace(_app(job_id))

    # Widgets inside a form send their values only when the form is submitted.
    next(t for t in at.text_area if t.label == "Notes").input("Recruiter: Anna")
    next(t for t in at.text_input if t.label == "Next action").input("Send CV")
    _button(at, "Save notes").click().run()

    card = applications.get_job_summary(job_id)
    assert (card.notes, card.next_action) == ("Recruiter: Anna", "Send CV")


# ---------------------------------------------------------------- Phase 6: copilot chat


def _open_chat(at: AppTest) -> AppTest:
    at.switch_page("ui/pages/3_copilot_chat.py")
    return at.run()


def test_sidebar_switches_off_tools_without_keys(fake_app, monkeypatch) -> None:
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "tavily_api_key", None)
    save_profile(Profile(name="Alex"))

    at = _app().run()

    company = at.sidebar.toggle(key="tool_company")
    assert company.disabled and company.value is False
    assert at.sidebar.toggle(key="tool_tracker").value is True
    assert any("TAVILY_API_KEY" in c.value for c in at.sidebar.caption)


def test_chat_starter_prompt_gets_an_answer_with_tool_expander(copilot_app) -> None:
    from langchain_core.messages import AIMessage

    from tests.copilot_fakes import calls, two_jobs

    save_profile(Profile(name="Alex"))
    two_jobs()
    copilot_app([calls(("get_applications", {})), AIMessage("You applied to nothing yet.")])
    at = _open_chat(_app())
    assert not at.exception

    _button(at, "Summarise my applications this week").click().run()

    assert not at.exception
    assert any(e.label == "🔧 Used: 🗂️ Read and update my tracker" for e in at.expander)
    assert any("You applied to nothing yet." in m.value for m in at.markdown)


def test_chat_approval_card_moves_the_job(copilot_app) -> None:
    from tests.copilot_fakes import research_and_move_script, two_jobs

    save_profile(Profile(name="Alex"))
    _, new = two_jobs()
    copilot_app(research_and_move_script(new))
    at = _open_chat(_app())

    at.chat_input[0].set_value("Research the company for my newest job and move it to Applied")
    at.run()

    assert not at.exception
    assert any("move to Applied" in m.value for m in at.markdown)
    assert at.chat_input[0].disabled  # no new message until the user decides
    _button(at, "✅ Approve").click().run()

    assert not at.exception
    assert applications.get_job_summary(new).status == "applied"


def test_company_tab_without_tavily_key(fake_app, monkeypatch) -> None:
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "tavily_api_key", None)
    save_profile(Profile(name="Alex"))
    job_id = applications.add_job(POSTING).job_id

    at = _open_workspace(_app(job_id))

    assert not at.exception
    assert any("TAVILY_API_KEY" in i.value for i in at.info)
    assert _button(at, "🔎 Research Lumen Health").disabled
