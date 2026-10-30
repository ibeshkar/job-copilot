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
from tests.graph_fakes import POSTING, UNSUPPORTED_CLAIM

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
    # Requirements: summary, then one expander per status group (gaps first and open).
    text = " ".join(m.value for m in at.markdown)
    assert "Must-have met: 2 / 2" in text
    groups = [e.label for e in at.expander if e.label[0] in "❌⚠✅"]
    assert groups == ["❌ Missing (1)", "✅ Met (2)"]
    assert "Kubernetes" in text and ":gray-badge[nice]" in text

    # A browser refresh is a brand-new session with the same URL.
    refreshed = _open_workspace(_app(job_id))
    assert refreshed.metric[0].value == "86/100"
    assert _button(refreshed, "Continue to application ▶")


def test_requirements_filter_hides_met_and_nice_to_have(fake_app) -> None:
    save_profile(Profile(name="Alex"))
    job_id = applications.add_job(POSTING).job_id
    at = _open_workspace(_app(job_id))
    control = at.segmented_control(key=f"fit_{job_id}_req_filter")
    assert control.value == "all"

    control.set_value("gaps").run()
    assert [e.label for e in at.expander if e.label[0] in "❌⚠✅"] == ["❌ Missing (1)"]

    at.segmented_control(key=f"fit_{job_id}_req_filter").set_value("must").run()
    assert [e.label for e in at.expander if e.label[0] in "❌⚠✅"] == ["✅ Met (2)"]
    assert not at.exception


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


def _claim_editors(at: AppTest) -> list:
    return [t for t in at.text_area if (t.key or "").startswith("claim_draft_")]


def test_claim_check_starts_unsupported_claims_unticked(fake_app) -> None:
    job_id = _at_claim_check()

    at = _open_workspace(_app(job_id))

    assert not at.exception
    assert [at.checkbox(key=f"keep_{job_id}_C{i}").value for i in (1, 2, 3)] == [
        True,
        True,
        False,
    ]
    text = "\n".join(m.value for m in at.markdown)
    assert "3 claims** · ✅ 2 backed by documents · ⚠️ 1 needs your confirmation" in text
    # Unconfirmed claims come first, struck through while unticked.
    assert text.index("Needs your confirmation") < text.index("Backed by your documents")
    assert f":gray[~~{UNSUPPORTED_CLAIM}~~]" in text
    assert "**2 of 3 claims selected**" in text
    assert not _claim_editors(at)  # no editor until ✏️ is clicked


def test_claim_check_edit_on_demand_and_bulk_buttons(fake_app) -> None:
    job_id = _at_claim_check()
    at = _open_workspace(_app(job_id))

    at.button(key=f"claim_editing_{job_id}_C3_open").click().run()
    at.text_area(key=f"claim_draft_{job_id}_C3").input("Led a team of two students.")
    at.button(key=f"claim_draft_{job_id}_C3_save").click().run()
    text = "\n".join(m.value for m in at.markdown)
    assert "Led a team of two students." in text and ":blue-badge[edited]" in text
    assert not _claim_editors(at)

    at.checkbox(key=f"keep_{job_id}_C1").uncheck().run()
    at.checkbox(key=f"keep_{job_id}_C3").check().run()
    _button(at, "Untick unconfirmed").click().run()
    _button(at, "Keep all backed").click().run()
    assert [at.checkbox(key=f"keep_{job_id}_C{i}").value for i in (1, 2, 3)] == [
        True,
        True,
        False,
    ]

    # A refresh is a new session: the choices start again from the graph's saved claims.
    refreshed = _open_workspace(_app(job_id))
    assert not refreshed.exception
    assert "Led a team of two students." not in "\n".join(m.value for m in refreshed.markdown)

    at.checkbox(key=f"keep_{job_id}_C3").check().run()
    _button(at, "✍️ Write my application →").click().run()
    assert not at.exception
    claims = {c.id: c for c in applications.get_workflow_view(job_id).claims}
    assert claims["C3"].user_decision == "edited"
    assert claims["C3"].text == "Led a team of two students."
    assert claims["C1"].user_decision == "approved"


def test_write_button_is_disabled_with_no_claim_selected(fake_app) -> None:
    job_id = _at_claim_check()
    at = _open_workspace(_app(job_id))

    at.checkbox(key=f"keep_{job_id}_C1").uncheck()
    at.checkbox(key=f"keep_{job_id}_C2").uncheck().run()

    assert _button(at, "✍️ Write my application →").disabled
    assert any("Tick at least one claim" in c.value for c in at.caption)


def test_claim_check_to_accepted_drafts(fake_app) -> None:
    job_id = _at_claim_check()
    at = _open_workspace(_app(job_id))

    at.checkbox(key=f"keep_{job_id}_C2").uncheck().run()
    _button(at, "✍️ Write my application →").click().run()

    assert not at.exception
    view = applications.get_workflow_view(job_id)
    assert view.stage == "draft_review"
    letter = view.drafts.cover_letter
    assert "RAG chatbot" not in letter.text  # the unticked claim is not used
    assert at.text_area(key=f"letter_p0_{job_id}_1").value == letter.body_paragraphs[0]
    assert at.text_input(key=f"letter_salutation_{job_id}_1").value == "Dear Hiring Team,"
    assert any("Fact check passed" in s.value for s in at.success)
    preview = next(m.value for m in at.markdown if "background:#fff" in m.value)
    assert preview.index("Lumen Health") < preview.index("Dear Hiring Team,")

    at.text_area(key=f"letter_p_new_{job_id}_1").input("P.S. I can start in May.").run()
    at.text_input(key=f"letter_subject_{job_id}_1").input("Application: AI Engineer").run()
    _button(at, "✅ Accept & save").click().run()

    assert not at.exception
    assert applications.get_workflow_view(job_id).stage == "finished"
    saved = applications.latest_drafts(job_id).cover_letter
    assert saved.body_paragraphs[-1] == "P.S. I can start in May."
    assert saved.subject == "Application: AI Engineer"


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


# ---------------------------------------------------------------- Phase 7: hardening pages


def _open(at: AppTest, page: str) -> AppTest:
    at.switch_page(f"ui/pages/{page}")
    return at.run()


def test_sidebar_shows_session_usage_and_last_action(fake_app) -> None:
    from app.db import repo

    save_profile(Profile(name="Alex"))
    at = _app().run()
    assert any("This session: **0 tokens" in m.value for m in at.sidebar.markdown)

    with repo.session_scope() as session:
        repo.log_usage(session, "analyse_fit", "openai/gpt-4o-mini", 3000, 1000, 0.0012)
    at.run()

    assert any("4.0k tokens · $0.0012" in m.value for m in at.sidebar.markdown)
    assert any("Last AI action: analyse_fit" in c.value for c in at.sidebar.caption)


def test_memory_page_edits_and_deletes(fake_app) -> None:
    from app.memory import store
    from app.memory.store import Fact

    save_profile(Profile(name="Alex"))
    key = store.add_fact(Fact(text="Led a 3-person team"))
    at = _open(_app(), "4_memory.py")
    assert not at.exception

    at.text_input(key=f"fact_{key}").input("Led a 4-person team")
    at.button(key=f"save_fact_{key}").click().run()
    assert store.list_facts()[key].text == "Led a 4-person team"
    assert any("Fact updated" in s.value for s in at.success)

    at.button(key=f"delete_fact_{key}").click().run()
    assert store.list_facts() == {}


def test_memory_page_delete_all_needs_the_confirm_word(fake_app) -> None:
    from app.services import my_data

    save_profile(Profile(name="Alex"))
    at = _open(_app(), "4_memory.py")
    delete = _button(at, "Delete everything")
    assert delete.disabled

    at.text_input(key="confirm_delete_all").input("DELETE").run()
    calls = []
    original = my_data.delete_all_data
    my_data.delete_all_data = lambda: calls.append(1) or original()
    try:
        _button(at, "Delete everything").click().run()
    finally:
        my_data.delete_all_data = original

    assert calls == [1]
    assert my_data.load().profile is None


def test_dev_settings_need_the_password(fake_app, monkeypatch) -> None:
    from pydantic import SecretStr

    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "app_dev_password", SecretStr("s3cret"))
    save_profile(Profile(name="Alex"))
    at = _open(_app(), "9_dev_settings.py")
    assert not at.exception
    assert not at.tabs  # nothing visible before unlocking

    at.text_input[0].input("wrong")
    _button(at, "Unlock").click().run()
    assert any("Wrong password" in e.value for e in at.error)

    at.text_input[0].input("s3cret")
    _button(at, "Unlock").click().run()
    assert not at.exception
    assert [t.label for t in at.tabs][:2] == ["🤖 Model", "💰 Usage & cost"]
    assert any("TOPIC_GUARD_SYSTEM" in str(s.options) for s in at.selectbox)


def test_chat_shows_retry_after_a_network_error(copilot_app) -> None:
    from langchain_core.messages import AIMessage

    from app.utils.errors import NETWORK_MESSAGE, LLMServiceError

    def offline(messages):
        raise LLMServiceError("down", user_message=NETWORK_MESSAGE)

    save_profile(Profile(name="Alex"))
    copilot_app([offline, AIMessage("Back online.")])
    at = _open(_app(), "3_copilot_chat.py")

    at.chat_input[0].set_value("How many jobs do I have?")
    at.run()
    assert any(NETWORK_MESSAGE in e.value for e in at.error)
    _button(at, "🔁 Retry").click().run()

    assert not at.exception
    assert any("Back online." in m.value for m in at.markdown)
    assert not [b for b in at.button if b.label == "🔁 Retry"]


def test_page_crash_shows_a_friendly_error(fake_app, monkeypatch) -> None:
    from app.services import applications as apps

    save_profile(Profile(name="Alex"))

    def broken():
        raise RuntimeError("database file is locked: /secret/path")

    monkeypatch.setattr(apps, "list_board", broken)
    at = _app().run()

    assert any("ran into a problem" in e.value for e in at.error)
    assert all("/secret/path" not in e.value for e in at.error)


# ---------------------------------------------------------------- Phase 8


def test_sidebar_tone_selector_saves_the_preference(fake_app) -> None:
    from app.memory.store import get_preferences

    save_profile(Profile(name="Alex"))
    at = _app().run()
    assert at.sidebar.selectbox(key="sidebar_tone").value == "friendly"

    at.sidebar.selectbox(key="sidebar_tone").select("concise").run()

    assert not at.exception
    assert get_preferences().tone == "concise"
    assert at.sidebar.selectbox(key="sidebar_tone").value == "concise"


def test_help_page_explains_and_answers_from_the_guide(fake_app, monkeypatch) -> None:
    from app.agent.state import HelpAnswer
    from app.services import help as help_bot
    from tests.fakes import FakeStructuredLLM

    answer = HelpAnswer(
        answer="Paste the job text on the Board.",
        covered_by_guide=True,
        sections=['Use case 1: "Is this job a fit for me?"'],
    )
    monkeypatch.setattr(help_bot, "get_llm", lambda: FakeStructuredLLM({HelpAnswer: answer}))
    save_profile(Profile(name="Alex"))
    at = _open(_app(), "5_help.py")
    assert not at.exception
    assert any("Will the app invent experience" in e.label for e in at.expander)

    at.chat_input[0].set_value("How do I add a job?").run()

    assert not at.exception
    assert any("Paste the job text on the Board." in m.value for m in at.markdown)
    assert any("From the guide" in c.value for c in at.caption)


def test_interview_tab_start_answer_and_feedback(fake_app) -> None:
    save_profile(Profile(name="Alex"))
    job_id = applications.add_job(POSTING).job_id
    at = _open_workspace(_app(job_id))
    assert not at.exception

    _button(at, "🎤 Start mock interview").click().run()
    assert not at.exception
    assert any("Q1 of 5" in m.value for m in at.markdown)

    next(t for t in at.text_area if t.label == "Your answer").input(
        "I built a RAG chatbot with LangChain and ChromaDB that 40 classmates used."
    )
    _button(at, "Send answer ▶").click().run()

    assert not at.exception
    assert any("Score: 4/5" in m.value for m in at.markdown)
    assert any("Q2 of 5" in m.value for m in at.markdown)

    _button(at, "End interview now").click().run()
    assert not at.exception
    assert at.metric[-1].value == "4.0/5"


def test_documents_tab_shows_a_learned_rule_with_undo(fake_app) -> None:
    from app.agent.state import Drafts
    from app.memory import store
    from tests.graph_fakes import PASSIONATE, passionate_writer

    fake_app.llm.responders[Drafts] = passionate_writer
    job_id = _at_claim_check()
    applications.submit_claim_review(
        job_id, [{"id": "C1", "action": "approve"}, {"id": "C2", "action": "approve"}]
    )
    at = _open_workspace(_app(job_id))
    letter = at.text_area(key=f"letter_p2_{job_id}_1")  # the writer adds it as paragraph 3
    assert PASSIONATE in letter.value

    letter.input(letter.value.replace(PASSIONATE, "I enjoy building AI products.")).run()
    _button(at, "✅ Accept & save").click().run()

    assert not at.exception
    [key] = store.list_style_rules()
    assert any("Avoid the word 'passionate'" in m.value for m in at.markdown)
    at.button(key=f"undo_rule_{key}").click().run()
    assert store.list_style_rules() == {}
    assert not any("I learned from your feedback" in m.value for m in at.markdown)


def test_memory_page_shows_rule_sources_and_adds_a_rule(fake_app) -> None:
    from app.agent.state import StyleRule
    from app.memory import store

    save_profile(Profile(name="Alex"))
    store.add_style_rule(
        StyleRule(
            rule="Avoid the word 'passionate'", source="edit_diff", avoid_phrases=["passionate"]
        )
    )
    at = _open(_app(), "4_memory.py")
    assert any("learned from your edits · checks for: passionate" in c.value for c in at.caption)

    next(t for t in at.text_input if t.label == "Add a rule").input("Keep it under 200 words")
    _button(at, "➕ Add rule").click().run()

    assert not at.exception
    assert sorted(r.rule for r in store.list_style_rules().values()) == [
        "Avoid the word 'passionate'",
        "Keep it under 200 words",
    ]


# ---------------------------------------------------------------- Phase 11: salary tab


def _save_estimate(job_id: int, **fields) -> None:
    from app.agent.state import SalaryEstimate, SalaryExplanation
    from app.db import repo

    estimate = SalaryEstimate(
        **(
            {
                "status": "ok",
                "low": 52000,
                "typical": 56500,
                "high": 61000,
                "confidence": "medium",
                "expectation": "55-65k EUR",
                "expectation_position": "within",
                "explanation": SalaryExplanation(
                    factors_up=["Berlin"],
                    factors_down=["Junior role"],
                    expectation_comparison="Your expectation fits.",
                    negotiation_tips=["Ask for the band early."],
                ),
            }
            | fields
        )
    )
    with repo.session_scope() as session:
        repo.save_salary_estimate(session, job_id, estimate.model_dump_json())


def test_salary_tab_and_board_badge_show_the_saved_estimate(fake_app) -> None:
    save_profile(Profile(name="Alex"))
    job_id = applications.add_job(POSTING).job_id
    _save_estimate(job_id)

    at = _open_workspace(_app(job_id))

    assert not at.exception
    text = " ".join(m.value for m in at.markdown)
    assert "€52,000 – €61,000 per year (gross)" in text
    assert "medium confidence" in text
    assert "Estimate only, not financial advice." in " ".join(c.value for c in at.caption)
    assert any("within" in i.value for i in at.info)
    assert _button(at, "🔄 Refresh")

    board = _app().run()
    assert "💰 €52k–61k" in " ".join(m.value for m in board.markdown)


def test_salary_tab_says_when_there_is_not_enough_data(fake_app) -> None:
    save_profile(Profile(name="Alex"))
    job_id = applications.add_job(POSTING).job_id
    _save_estimate(job_id, status="not_enough_data", low=None, typical=None, high=None)

    at = _open_workspace(_app(job_id))

    assert not at.exception
    assert any("Not enough data" in w.value for w in at.warning)
