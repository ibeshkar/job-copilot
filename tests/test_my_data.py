# What: tests for app/services/my_data.py, the "What I know about you" page's service.
# Why: users must be able to edit and delete what the app remembers, and "Delete all my
#      data" must really empty every store (memory, knowledge base, database, saved runs, log).
# Used by: pytest.

from datetime import UTC, datetime

from app.agent.state import StyleRule
from app.db import repo
from app.memory import store
from app.memory.store import Fact, Preferences, Profile
from app.services import applications, my_data
from tests.graph_fakes import POSTING


def _remember_things() -> tuple[str, str]:
    store.save_profile(Profile(name="Alex"))
    store.save_preferences(Preferences(tone="formal"))
    fact = store.add_fact(Fact(text="Led a 3-person team", job_id=1))
    rule = store.add_style_rule(
        StyleRule(rule="Avoid 'passionate'", source="explicit", created_at=datetime.now(UTC))
    )
    return fact, rule


def test_load_shows_everything(app_db) -> None:
    fact, rule = _remember_things()

    data = my_data.load()

    assert data.profile.name == "Alex"
    assert data.preferences.tone == "formal"
    assert data.facts[fact].text == "Led a 3-person team"
    assert data.style_rules[rule].rule == "Avoid 'passionate'"
    assert not data.is_empty


def test_edit_and_delete_single_items(app_db) -> None:
    fact, rule = _remember_things()

    my_data.update_fact(fact, "Led a 4-person team")
    my_data.update_style_rule(rule, "Never use 'passionate'")
    assert store.list_facts()[fact].text == "Led a 4-person team"
    assert store.list_facts()[fact].job_id == 1  # the rest of the fact is kept
    assert store.list_style_rules()[rule].rule == "Never use 'passionate'"

    my_data.update_fact(fact, "   ")  # empty text = delete
    my_data.delete_style_rule(rule)
    my_data.reset_preferences()
    my_data.delete_profile()

    data = my_data.load()
    assert data.facts == {} and data.style_rules == {}
    assert data.preferences == Preferences()
    assert data.profile is None and data.is_empty


def _delete_with(vectorstore):
    from app.rag.ingest import delete_document_chunks

    return lambda name: delete_document_chunks(name, vectorstore)


def test_delete_document_removes_record_and_chunks(app_db, vectorstore, monkeypatch) -> None:
    from app.services import onboarding

    monkeypatch.setattr(my_data, "delete_document_chunks", _delete_with(vectorstore))
    onboarding.add_documents([("cv.md", b"Python and SQL for two years. " * 10)], vectorstore)

    removed = my_data.delete_document("cv.md")

    assert removed > 0
    assert my_data.load().documents == []
    assert vectorstore.get(include=[])["ids"] == []


def test_delete_all_data_empties_every_store(fake_app, vectorstore, tmp_path, monkeypatch) -> None:
    from app.config import get_settings
    from app.rag import ingest
    from tests.fakes import KeywordEmbeddings

    _remember_things()
    job_id = applications.add_job(POSTING).job_id
    with repo.session_scope() as session:
        repo.upsert_document(session, "cv.md", "cv", 3)
        repo.log_usage(session, "chat", "openai/gpt-4o-mini", 10, 5, 0.0)
    ingest.ingest_file("cv.md", b"Python and SQL. " * 20, vectorstore=vectorstore)
    monkeypatch.setattr(ingest, "_maintenance_store", lambda: vectorstore)
    # Delete the threads of the fake graph the service uses in this test.
    monkeypatch.setattr(my_data, "_checkpointer", lambda: applications.get_graph().checkpointer)
    log = get_settings().runtime_dir / "app.log"
    log.write_text("Saved profile for Alex\n")
    assert applications.get_workflow_view(job_id).stage == "fit_review"

    my_data.delete_all_data()

    assert my_data.load().is_empty
    with repo.session_scope() as session:
        assert repo.list_jobs(session) == []
        assert repo.usage_totals(session) == (0, 0, 0.0)
    config = {"configurable": {"thread_id": f"job-{job_id}"}}
    assert applications.get_graph().get_state(config).values == {}
    # A fresh handle on the same folder: the old one points at the deleted collection.
    reopened = ingest.make_vectorstore(KeywordEmbeddings(), tmp_path / "chroma")
    assert ingest.count_chunks(reopened) == 0
    assert "Alex" not in log.read_text()
