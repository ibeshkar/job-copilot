# What: `GraphDeps`, the bundle of outside services the graph nodes use (LLM, document search,
#       memory, web fetching, database), and `default_deps()` which wires up the real ones.
# Why: nodes receive their services instead of importing them. Tests pass fakes (a scripted LLM,
#      an in-memory search), so the whole graph runs offline and repeatably.
# Used by: app/agent/application_graph.py, app/agent/nodes/*.py, tests/test_application_graph.py.

from collections.abc import Callable
from dataclasses import dataclass

from langchain_core.language_models import BaseChatModel

from app.agent.state import Evidence, FitReport, JobPosting
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


def default_deps() -> GraphDeps:
    """The real services: OpenRouter LLM, Chroma search, SQLite memory and database.

    Without an API key the LLM is None. The graph can still be built, so the UI can show
    saved analyses; starting or resuming a run raises ConfigError.
    """
    from app.agent.llm import get_llm
    from app.config import get_settings
    from app.memory.store import get_preferences, get_profile
    from app.rag.ingest import count_chunks
    from app.rag.retriever import search_evidence
    from app.services.applications import save_analysis_to_job
    from app.services.web_fetch import fetch_page_text

    return GraphDeps(
        llm=get_llm() if get_settings().has_llm_key else None,
        search=lambda query, k: search_evidence(query, k=k),
        knowledge_base_size=count_chunks,
        get_profile=get_profile,
        get_preferences=get_preferences,
        fetch_url=fetch_page_text,
        save_job=save_analysis_to_job,
    )
