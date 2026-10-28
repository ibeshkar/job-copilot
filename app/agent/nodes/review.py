# What: the human-in-the-loop step `fit_review` (pause and wait for the user's decision on the
#       fit card) and `save_to_tracker` (copy the results onto the job's board card).
# Why: the user, not the agent, decides whether a job is worth applying to. `interrupt()` stops
#      the graph, the checkpointer saves its state, and `Command(resume=...)` continues it later,
#      even after the browser is closed or the app restarts.
# Used by: app/agent/application_graph.py.

from langgraph.types import interrupt

from app.agent.deps import GraphDeps
from app.agent.state import ApplicationState, FitReviewDecision


def fit_review(state: ApplicationState) -> dict:
    """Pause the graph and show the fit card; continue with the user's decision.

    The payload's `type` tells the UI which review component to render. When the graph is
    resumed with `Command(resume={"action": ..., "text": ...})`, this function runs again from
    the top and `interrupt()` returns that value instead of pausing.

    Raises:
        pydantic.ValidationError: the resume value is not a valid FitReviewDecision.
    """
    answer = interrupt(
        {
            "type": "fit_review",
            "job_title": state["job"].title,
            "company": state["job"].company,
            "fit": state["fit"].model_dump(mode="json"),
            "warnings": state.get("warnings", []),
            "options": ["continue", "stop", "add_context"],
        }
    )
    decision = FitReviewDecision.model_validate(answer)

    if decision.action == "add_context" and decision.text and decision.text.strip():
        previous = state.get("extra_context")
        note = decision.text.strip() if not previous else f"{previous}\n{decision.text.strip()}"
        # Clearing the queries and the counter makes retrieve_evidence plan fresh searches
        # for every requirement that isn't "met" yet, now with the note as extra evidence.
        return {
            "review_action": "add_context",
            "extra_context": note,
            "search_queries": {},
            "rag_iterations": 0,
        }
    if decision.action == "add_context":
        return {"review_action": "continue"}  # nothing was added; treat as continue
    return {"review_action": decision.action}


def save_to_tracker(state: ApplicationState, deps: GraphDeps) -> dict:
    """Write the posting and fit score to the job's row, so the board card shows them."""
    if state.get("job_id") is not None and state.get("job") is not None:
        deps.save_job(state["job_id"], state["job"], state.get("fit"))
    return {}
