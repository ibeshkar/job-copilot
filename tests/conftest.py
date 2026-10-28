# What: shared pytest fixtures.
# Why: tests must never touch the real data in data/runtime/. Each test gets a fresh
#      in-memory or temporary database, and the whole test run uses a temporary runtime folder.
# Used by: every test module in tests/.

import os
import tempfile
from collections.abc import Iterator

# Set before any `app` module is imported, so even import-time setup (like the log file)
# goes to a throwaway folder instead of data/runtime/.
os.environ["RUNTIME_DIR"] = tempfile.mkdtemp(prefix="job-copilot-tests-")

import pytest  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402
from sqlmodel import Session, create_engine  # noqa: E402

from app.db.repo import init_db  # noqa: E402


def clear_app_caches() -> None:
    """Forget cached settings, engine, memory store and vector store, so the next call
    opens them again from the current RUNTIME_DIR."""
    from app.agent.application_graph import get_application_graph
    from app.config import get_settings
    from app.db.repo import get_engine
    from app.memory.store import get_store
    from app.rag.ingest import get_vectorstore

    for cached in (get_settings, get_engine, get_store, get_vectorstore, get_application_graph):
        cached.cache_clear()


@pytest.fixture
def session() -> Iterator[Session]:
    """A database session on an empty in-memory database with all tables created."""
    # StaticPool keeps one connection, so the in-memory database lives for the whole test.
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    init_db(engine)
    with Session(engine, expire_on_commit=False) as s:
        yield s


@pytest.fixture
def vectorstore(tmp_path):
    """An empty Chroma knowledge base in a temporary folder, using offline fake embeddings."""
    from app.rag.ingest import make_vectorstore
    from tests.fakes import KeywordEmbeddings

    return make_vectorstore(KeywordEmbeddings(), tmp_path / "chroma")


@pytest.fixture
def app_db(tmp_path, monkeypatch):
    """Point the app's settings and database at a temporary folder for this test."""
    monkeypatch.setenv("RUNTIME_DIR", str(tmp_path))
    clear_app_caches()
    init_db()
    yield tmp_path
    clear_app_caches()


@pytest.fixture
def fake_app(app_db, monkeypatch):
    """The app wired to an offline fake graph (scripted LLM, tiny search) in a temp folder.

    Returns the GraphDeps, so a test can swap the LLM (e.g. to simulate an outage).
    """
    from app.agent.application_graph import build_application_graph, make_checkpointer
    from app.services import applications
    from tests.graph_fakes import make_deps

    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")  # the service checks a key exists
    clear_app_caches()
    deps = make_deps()
    deps.save_job = applications.save_analysis_to_job  # write results to the temp database
    deps.save_draft = applications.save_draft_version
    graph = build_application_graph(deps, make_checkpointer(app_db / "checkpoints.db"))
    monkeypatch.setattr(applications, "get_graph", lambda: graph)
    return deps
