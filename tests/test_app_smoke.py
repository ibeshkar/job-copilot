# What: smoke tests that run the Streamlit app headlessly (no browser) with AppTest.
# Why: checks that `app/main.py` starts, builds the navigation and opens the right first page:
#      the onboarding wizard for a new user, the board once a profile exists.
# Used by: pytest.

from pathlib import Path

from streamlit.testing.v1 import AppTest

from app.memory.store import Profile, save_profile

MAIN = Path(__file__).resolve().parent.parent / "app" / "main.py"


def _run() -> AppTest:
    return AppTest.from_file(str(MAIN), default_timeout=30).run()


def test_new_user_sees_onboarding(app_db: Path) -> None:
    at = _run()

    assert not at.exception
    assert at.title[0].value == "👋 Get started"
    assert (app_db / "app.db").exists()


def test_onboarded_user_sees_board(app_db: Path) -> None:
    save_profile(Profile(name="Alex Novak", email="alex@example.com", target_roles=["AI Engineer"]))

    at = _run()

    assert not at.exception
    assert at.title[0].value == "🗂️ Board"
    assert any("Alex Novak" in m.value for m in at.sidebar.markdown)
