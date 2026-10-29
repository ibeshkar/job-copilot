# What: the work behind the "What I know about you" page: read everything the app remembers
#       (profile, preferences, confirmed facts, style rules, documents), edit or delete single
#       items, and "Delete all my data".
# Why: users must stay in control of their data (PROJECT_PLAN.md §7 and §9). Deleting
#      everything touches five places (memory store, knowledge base, app database, saved
#      workflow runs, log file); one tested function makes sure none is forgotten.
# Used by: app/ui/pages/4_memory.py, tests/test_my_data.py.

from dataclasses import dataclass

from app.agent.state import StyleRule
from app.config import get_settings
from app.db import repo
from app.memory import store
from app.memory.store import Fact, Preferences, Profile
from app.rag.ingest import clear_knowledge_base, delete_document_chunks
from app.utils.logging import get_logger

logger = get_logger(__name__)


@dataclass
class DocumentInfo:
    """One file in the knowledge base."""

    filename: str
    doc_type: str
    chunk_count: int


@dataclass
class MyData:
    """Everything the app remembers about the user, for the Memory page."""

    profile: Profile | None
    preferences: Preferences
    facts: dict[str, Fact]  # key → fact
    style_rules: dict[str, StyleRule]  # key → rule
    documents: list[DocumentInfo]

    @property
    def is_empty(self) -> bool:
        return not (self.profile or self.facts or self.style_rules or self.documents)


def load() -> MyData:
    """Read everything remembered about the user."""
    with repo.session_scope() as session:
        documents = [
            DocumentInfo(d.filename, d.doc_type, d.chunk_count)
            for d in repo.list_documents(session)
        ]
    return MyData(
        profile=store.get_profile(),
        preferences=store.get_preferences(),
        facts=store.list_facts(),
        style_rules=store.list_style_rules(),
        documents=documents,
    )


# ---------------------------------------------------------------- single items
# Thin wrappers, so the page talks to one service instead of three storage modules.


def save_profile(profile: Profile) -> None:
    store.save_profile(profile)


def delete_profile() -> None:
    store.delete_profile()


def save_preferences(preferences: Preferences) -> None:
    store.save_preferences(preferences)


def reset_preferences() -> None:
    store.delete_preferences()


def set_tone(tone: str) -> None:
    """Change only the tone (the sidebar selector); the other preferences stay as they are.

    Raises:
        pydantic.ValidationError: `tone` is not formal, friendly or concise.
    """
    current = store.get_preferences()
    store.save_preferences(Preferences.model_validate({**current.model_dump(), "tone": tone}))


def update_fact(key: str, text: str) -> None:
    """Reword a fact. Empty text deletes it."""
    if text.strip():
        store.update_fact(key, text.strip())
    else:
        store.delete_fact(key)


def delete_fact(key: str) -> None:
    store.delete_fact(key)


def update_style_rule(key: str, text: str) -> None:
    """Reword a style rule. Empty text deletes it."""
    if text.strip():
        store.update_style_rule(key, text.strip())
    else:
        store.delete_style_rule(key)


def delete_style_rule(key: str) -> None:
    store.delete_style_rule(key)


def add_style_rule(text: str) -> str | None:
    """Add a rule the user typed on the Memory page. Returns its key, or None if empty."""
    if not text.strip():
        return None
    return store.add_style_rule(StyleRule(rule=text.strip(), source="explicit"))


def delete_document(filename: str) -> int:
    """Remove a file from the knowledge base. Returns how many chunks were removed."""
    removed = delete_document_chunks(filename)
    with repo.session_scope() as session:
        repo.delete_document(session, filename)
    logger.info("Deleted document %s (%d chunks)", filename, removed)
    return removed


# ---------------------------------------------------------------- delete everything


def _checkpointer():
    """The checkpointer holding all saved job runs and chats (both graphs share its file)."""
    from app.agent.application_graph import get_application_graph

    return get_application_graph().checkpointer


def _delete_all_threads() -> int:
    """Delete every saved workflow run and chat (the checkpointer). Returns the thread count."""
    checkpointer = _checkpointer()
    if checkpointer is None:
        return 0
    thread_ids = {c.config["configurable"]["thread_id"] for c in checkpointer.list(None)}
    for thread_id in thread_ids:
        checkpointer.delete_thread(thread_id)
    return len(thread_ids)


def delete_all_data() -> None:
    """Delete everything the app stores about the user ("Delete all my data").

    Removes: long-term memory (profile, preferences, facts, style rules, company research),
    the knowledge base (all chunks), the app database (jobs, drafts, documents, usage,
    feedback, interviews), every saved workflow run and chat, and the log file's contents.
    Developer settings (model choice) are kept, because they are not about the user.
    """
    store.delete_all_memories()
    clear_knowledge_base()
    with repo.session_scope() as session:
        repo.delete_all_rows(session)
    threads = _delete_all_threads()
    # The log can contain job titles and parts of AI answers, so it is emptied too.
    log_file = get_settings().runtime_dir / "app.log"
    if log_file.exists():
        log_file.write_text("")
    logger.info("All user data deleted (%d saved threads)", threads)
