# What: tests for the setup check (app/services/readiness.py), the checklist steps built from
#       it (app/ui/components.setup_steps) and the demo quick start.
# Why: the Board, the "Add a job" dialog, the graph's input guard and the chat all trust this
#      one answer. No profile / profile without documents / ready are the three states a new
#      user moves through.
# Used by: pytest.

from app.db import repo
from app.memory.store import Profile, get_profile, save_profile
from app.services import onboarding
from app.services.readiness import (
    NO_DOCUMENTS_MESSAGE,
    NO_PROFILE_MESSAGE,
    NOT_READY_MESSAGE,
    SetupStatus,
    get_setup_status,
    setup_status,
)
from app.ui.components import setup_steps


def _add_document(chunks: int = 7) -> None:
    with repo.session_scope() as session:
        repo.upsert_document(session, "cv.md", "cv", chunks)


def test_fresh_start_is_not_ready(app_db) -> None:
    status = get_setup_status()

    assert status == SetupStatus(has_profile=False, has_documents=False, chunk_count=0)
    assert not status.is_ready
    assert status.message == NOT_READY_MESSAGE


def test_profile_without_documents_is_not_ready(app_db) -> None:
    save_profile(Profile(name="Alex", email="alex@example.com", target_roles=["AI Engineer"]))

    status = get_setup_status()

    assert status.has_profile and not status.has_documents
    assert not status.is_ready
    assert status.message == NO_DOCUMENTS_MESSAGE


def test_documents_without_profile_is_not_ready(app_db) -> None:
    _add_document()

    status = get_setup_status()

    assert status.chunk_count == 7
    assert not status.is_ready
    assert status.message == NO_PROFILE_MESSAGE


def test_profile_and_documents_is_ready(app_db) -> None:
    save_profile(Profile(name="Alex", email="alex@example.com", target_roles=["AI Engineer"]))
    _add_document(12)

    status = get_setup_status()

    assert status.is_ready
    assert status.chunk_count == 12
    assert status.message == ""


def test_setup_status_from_graph_values() -> None:
    assert setup_status(None, 5).message == NO_PROFILE_MESSAGE
    assert setup_status(Profile(name="Alex"), 0).message == NO_DOCUMENTS_MESSAGE
    assert setup_status(Profile(name="Alex"), 5).is_ready


def _states(status: SetupStatus) -> list[str]:
    return [step.state for step in setup_steps(status)]


def test_checklist_has_exactly_one_next_step_and_locks_the_job() -> None:
    fresh = SetupStatus(False, False)
    profile_only = SetupStatus(True, False)
    docs_only = SetupStatus(False, True, 7)

    assert _states(fresh) == ["next", "todo", "locked"]
    assert _states(profile_only) == ["done", "next", "locked"]
    assert _states(docs_only) == ["next", "done", "locked"]
    for status in (fresh, profile_only, docs_only):
        assert len([s for s in setup_steps(status) if s.button]) == 1


def test_checklist_shows_the_chunk_count_once_documents_exist() -> None:
    docs_step = setup_steps(SetupStatus(True, True, 42))[1]

    assert docs_step.state == "done"
    assert "42 chunks" in docs_step.detail


def test_demo_quick_start_keeps_an_existing_profile_and_documents(app_db) -> None:
    save_profile(Profile(name="Sam", email="alex@example.com", target_roles=["AI Engineer"]))
    _add_document()

    report = onboarding.use_demo_persona()

    assert get_profile().name == "Sam"
    assert report.added == {} and report.errors == {}


def test_demo_quick_start_fills_in_a_missing_profile(app_db) -> None:
    _add_document()  # documents exist, so nothing is indexed (no API call)

    onboarding.use_demo_persona()

    assert get_profile() == onboarding.DEMO_PROFILE
    assert get_setup_status().is_ready
