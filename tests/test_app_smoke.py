# What: a smoke test that runs the Streamlit app headlessly (no browser) with AppTest.
# Why: checks that `app/main.py` starts, builds the navigation and renders the default page
#      without exceptions. Uses a temporary runtime folder so the real database is untouched.
# Used by: pytest.

from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from app.config import get_settings
from app.db.repo import get_engine

MAIN = Path(__file__).resolve().parent.parent / "app" / "main.py"


@pytest.fixture(autouse=True)
def _temp_runtime(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RUNTIME_DIR", str(tmp_path))
    get_settings.cache_clear()
    get_engine.cache_clear()
    yield
    get_settings.cache_clear()
    get_engine.cache_clear()


def test_app_starts_and_shows_board(tmp_path: Path) -> None:
    at = AppTest.from_file(str(MAIN), default_timeout=30).run()

    assert not at.exception
    assert at.title[0].value == "🗂️ Board"
    assert (tmp_path / "app.db").exists()
