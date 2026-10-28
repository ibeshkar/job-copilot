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

    llm: BaseChatModel
    search: Callable[[str, int], list[Evidence]]  # (query, k) → best chunks
    knowledge_base_size: Callable[[], int]  # number of chunks; 0 blocks the analysis
    get_profile: Callable[[], Profile | None]
    get_preferences: Callable[[], Preferences]
    fetch_url: Callable[[str], str]  # URL → page text
    save_job: Callable[[int, JobPosting, FitReport | None], None]  # write results to the board


def _save_job_to_db(job_id: int, job: JobPosting, fit: FitReport | None) -> None:
    """Copy the analysis results onto the job's row in the tracker database."""
    from app.db import repo

    with repo.session_scope() as session:
        repo.update_job(
            session,
            job_id,
            title=job.title,
            company=job.company,
            url=job.source_url,
            raw_text=job.raw_text,
            posting_json=job.model_dump_json(),
            fit_score=fit.score if fit else None,
        )


def default_deps() -> GraphDeps:
    """The real services: OpenRouter LLM, Chroma search, SQLite memory and database."""
    from app.agent.llm import get_llm
    from app.memory.store import get_preferences, get_profile
    from app.rag.ingest import count_chunks
    from app.rag.retriever import search_evidence
    from app.services.web_fetch import fetch_page_text

    return GraphDeps(
        llm=get_llm(),
        search=lambda query, k: search_evidence(query, k=k),
        knowledge_base_size=count_chunks,
        get_profile=get_profile,
        get_preferences=get_preferences,
        fetch_url=fetch_page_text,
        save_job=_save_job_to_db,
    )
