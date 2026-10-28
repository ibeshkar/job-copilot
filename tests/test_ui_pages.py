# What: headless UI tests (Streamlit AppTest) for the Board and the Job workspace, using the
#       offline fake graph.
# Why: checks the Phase 4 "done when": a job added from the UI shows its fit card, and a
#      browser refresh (a new AppTest session with the same ?job= URL) shows it again.
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
