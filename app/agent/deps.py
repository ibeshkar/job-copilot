# What: `GraphDeps`, the bundle of outside services the graph nodes use (LLM, document search,
#       memory, web fetching, database), and `default_deps()` which wires up the real ones.
# Why: nodes receive their services instead of importing them. Tests pass fakes (a scripted LLM,
#      an in-memory search), so the whole graph runs offline and repeatably.
# Used by: app/agent/application_graph.py, app/agent/nodes/*.py, tests/graph_fakes.py.

from collections.abc import Callable
from dataclasses import dataclass

from langchain_core.language_models import BaseChatModel

from app.agent.state import Drafts, Evidence, FitReport, JobPosting, StyleRule
from app.memory.store import Preferences, Profile


@dataclass
class GraphDeps:
    """Everything the application graph needs from the outside world."""

    llm: BaseChatModel | None  # None when no API key: saved runs can be read, not run
    search: Callable[[str, int], list[Evidence]]  # (query, k) → best chunks
    knowledge_base_size: Callable[[], int]  # number of chunks; 0 blocks the analysis
    get_profile: Callable[[], Profile | None]
    get_preferences: Callable[[], Preferences]
    fetch_url: Callable[[str], str]  # URL → page text
    save_job: Callable[[int, JobPosting, FitReport | None], None]  # write results to the board
    list_facts: Callable[[], list[str]]  # facts the user confirmed in earlier claim checks
    remember_fact: Callable[[str, int | None], None]  # (text, job_id) → long-term memory
    list_style_rules: Callable[[], list[StyleRule]]  # writing rules, e.g. "Avoid 'passionate'"
    add_style_rule: Callable[[StyleRule], str]  # remember a learned rule → its key (for undo)
    save_draft: Callable[[int, Drafts, bool], None]  # (job_id, drafts, is_user_edited) → Draft


def default_deps() -> GraphDeps:
    """The real services: OpenRouter LLM, Chroma search, SQLite memory and database.

    Without an API key the LLM is None. The graph can still be built, so the UI can show
    saved analyses; starting or resuming a run raises ConfigError.
    """
    from app.agent.llm import get_llm
    from app.config import get_settings
    from app.memory import store
    from app.memory.store import Fact, get_preferences, get_profile
    from app.rag.ingest import count_chunks
    from app.rag.retriever import search_evidence
    from app.services.applications import save_analysis_to_job, save_draft_version
    from app.services.web_fetch import fetch_page_text

    return GraphDeps(
        llm=get_llm() if get_settings().has_llm_key else None,
        search=lambda query, k: search_evidence(query, k=k),
        knowledge_base_size=count_chunks,
        get_profile=get_profile,
        get_preferences=get_preferences,
        fetch_url=fetch_page_text,
        save_job=save_analysis_to_job,
        list_facts=lambda: [fact.text for fact in store.list_facts().values()],
        remember_fact=lambda text, job_id: store.add_fact(Fact(text=text, job_id=job_id)),
        list_style_rules=lambda: list(store.list_style_rules().values()),
        add_style_rule=store.add_style_rule,
        save_draft=save_draft_version,
    )
