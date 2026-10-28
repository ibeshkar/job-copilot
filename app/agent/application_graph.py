# What: wires the application workflow graph (PROJECT_PLAN.md §6.1), analysis half:
#       input_guard → ingest_job → extract_requirements → retrieve_evidence ⇄ rewrite_queries
#       → assess_fit → fit_review (pause for the user) → save_to_tracker / END.
# Why: applying is high-stakes, so the steps run in a fixed, auditable order (a workflow, not a
#      free agent). The SQLite checkpointer saves the state after every step, which is what lets
#      the graph pause for the user and resume later.
# Used by: scripts/analyse_sample_jobs.py, tests/test_application_graph.py; Phase 4 UI.

import sqlite3
from functools import lru_cache, partial
from pathlib import Path

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from app.agent.deps import GraphDeps, default_deps
from app.agent.nodes.evidence import needs_rewrite, retrieve_evidence, rewrite_queries
from app.agent.nodes.fit import assess_fit
from app.agent.nodes.ingest import ingest_job, input_guard
from app.agent.nodes.requirements import extract_requirements
from app.agent.nodes.review import fit_review, save_to_tracker
from app.agent.state import CHECKPOINT_TYPES, ApplicationState
from app.config import get_settings


def _stop_on_errors(next_node: str):
    """Edge rule: go to END if the last step reported an error, otherwise to `next_node`."""

    def route(state: ApplicationState) -> str:
        return END if state.get("errors") else next_node

    return route


def _after_retrieval(state: ApplicationState) -> str:
    # The "agentic" decision: if a search found nothing useful, try other words (max 2 times).
    return "rewrite_queries" if needs_rewrite(state) else "assess_fit"


def _after_fit_review(state: ApplicationState) -> str:
    action = state.get("review_action")
    if action == "add_context":
        return "retrieve_evidence"
    if action == "stop":
        return "save_to_tracker"
    # "continue": Phase 5 will route this to plan_claims. For now the analysis is saved and
    # the run ends.
    return "save_to_tracker"


def build_application_graph(
    deps: GraphDeps, checkpointer: BaseCheckpointSaver | None
) -> CompiledStateGraph:
    """Build and compile the workflow graph.

    Args:
        deps: the services the nodes use (real ones from `default_deps()`, or fakes in tests).
        checkpointer: where state is saved between steps. Required for `interrupt()`.

    Returns:
        The compiled graph. Run it with a config like
        `{"configurable": {"thread_id": "job-3"}}`.
    """
    graph = StateGraph(ApplicationState)

    # Nodes that need outside services get them through `partial(..., deps=deps)`.
    graph.add_node("input_guard", partial(input_guard, deps=deps))
    graph.add_node("ingest_job", partial(ingest_job, deps=deps))
    graph.add_node("extract_requirements", partial(extract_requirements, deps=deps))
    graph.add_node("retrieve_evidence", partial(retrieve_evidence, deps=deps))
    graph.add_node("rewrite_queries", partial(rewrite_queries, deps=deps))
    graph.add_node("assess_fit", partial(assess_fit, deps=deps))
    graph.add_node("fit_review", fit_review)
    graph.add_node("save_to_tracker", partial(save_to_tracker, deps=deps))

    graph.add_edge(START, "input_guard")
    graph.add_conditional_edges("input_guard", _stop_on_errors("ingest_job"), ["ingest_job", END])
    graph.add_conditional_edges(
        "ingest_job", _stop_on_errors("extract_requirements"), ["extract_requirements", END]
    )
    graph.add_conditional_edges(
        "extract_requirements", _stop_on_errors("retrieve_evidence"), ["retrieve_evidence", END]
    )
    graph.add_conditional_edges(
        "retrieve_evidence", _after_retrieval, ["rewrite_queries", "assess_fit"]
    )
    graph.add_edge("rewrite_queries", "retrieve_evidence")
    graph.add_edge("assess_fit", "fit_review")
    graph.add_conditional_edges(
        "fit_review", _after_fit_review, ["retrieve_evidence", "save_to_tracker"]
    )
    graph.add_edge("save_to_tracker", END)

    return graph.compile(checkpointer=checkpointer)


def make_checkpointer(db_path: Path) -> SqliteSaver:
    """A SQLite checkpointer that only loads our own state types back from disk.

    The default serializer can rebuild any Python class named in a checkpoint. An allowlist
    means a tampered checkpoint file can't make the app create unexpected objects.
    """
    conn = sqlite3.connect(db_path, check_same_thread=False)
    serde = JsonPlusSerializer(allowed_msgpack_modules=CHECKPOINT_TYPES)
    return SqliteSaver(conn, serde=serde)


@lru_cache
def get_application_graph() -> CompiledStateGraph:
    """The app's graph with real services and the checkpointer in data/runtime/checkpoints.db."""
    settings = get_settings()
    return build_application_graph(
        default_deps(), make_checkpointer(settings.runtime_dir / "checkpoints.db")
    )


def thread_config(thread_id: str) -> dict:
    """The config that selects one saved workflow run, e.g. `thread_config("job-3")`."""
    return {"configurable": {"thread_id": thread_id}, "recursion_limit": 25}
