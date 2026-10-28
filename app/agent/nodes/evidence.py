# What: agentic RAG. `retrieve_evidence` plans a search query per requirement, searches the
#       knowledge base, and lets the LLM grade each hit. `rewrite_queries` tries new wording for
#       requirements where nothing relevant was found (at most 2 times).
# Why: plain RAG trusts whatever the vector search returns. Grading throws away chunks that only
#      look similar, and rewriting finds evidence the first wording missed
#      (e.g. "Airflow" → "scheduled data pipelines").
# Used by: app/agent/application_graph.py (the retrieve → rewrite loop).

from app.agent.deps import GraphDeps
from app.agent.llm import invoke_structured
from app.agent.prompts import grader_messages, query_planner_messages, query_rewriter_messages
from app.agent.state import (
    ApplicationState,
    Evidence,
    GradeList,
    Requirement,
    SearchQueries,
)

TOP_K = 4  # chunks retrieved per query (PROJECT_PLAN.md §6.1)
MAX_EVIDENCE_PER_REQUIREMENT = 4
MAX_REWRITES = 2  # stops runaway loops and cost (§9)
MIN_GRADE_SCORE = 0.4  # a chunk must be graded relevant AND at least this strong
USER_NOTE_CHUNK_ID = "user-note"
GRADE_BATCH_PAIRS = 20  # (requirement, chunk) pairs per grading call; keeps answers short


def _requirements_block(requirements: list[Requirement]) -> str:
    return "\n".join(f"{r.id}: {r.text}" for r in requirements)


def _queries_for(requirements: list[Requirement], answer: SearchQueries) -> dict[str, str]:
    """Map requirement id → query. Missing or unknown ids fall back to the requirement text."""
    by_id = {q.requirement_id: q.query for q in answer.queries if q.query.strip()}
    return {r.id: by_id.get(r.id, r.text) for r in requirements}


def _todo_requirements(state: ApplicationState) -> list[Requirement]:
    """Requirements that still need a search.

    First pass: those without evidence. After "add context" at fit review: those not yet "met".
    """
    requirements = state["requirements"]
    fit = state.get("fit")
    if fit is not None:
        met = {m.requirement_id for m in fit.matches if m.status == "met"}
        return [r for r in requirements if r.id not in met]
    evidence = state.get("evidence") or {}
    return [r for r in requirements if not evidence.get(r.id)]


def _grade(
    deps: GraphDeps,
    requirements: dict[str, Requirement],
    candidates: dict[str, list[Evidence]],
) -> dict[str, list[Evidence]]:
    """Ask the LLM which candidate chunks really support each requirement.

    Pairs are graded in batches of at most GRADE_BATCH_PAIRS. One call for 15 requirements × 4
    chunks would need a ~1,800-token answer, more than the max_tokens limit.
    """
    graded: dict[str, list[Evidence]] = {}
    batch: dict[str, list[Evidence]] = {}
    for rid, chunks in candidates.items():
        if batch and sum(map(len, batch.values())) + len(chunks) > GRADE_BATCH_PAIRS:
            graded.update(_grade_batch(deps, requirements, batch))
            batch = {}
        batch[rid] = chunks
    if batch:
        graded.update(_grade_batch(deps, requirements, batch))
    return graded


def _grade_batch(
    deps: GraphDeps,
    requirements: dict[str, Requirement],
    candidates: dict[str, list[Evidence]],
) -> dict[str, list[Evidence]]:
    """Grade one batch of (requirement, chunk) pairs in a single LLM call."""
    lines = []
    for rid, chunks in candidates.items():
        if not chunks:
            continue
        lines.append(f"Requirement {rid}: {requirements[rid].text}")
        lines += [f"  [chunk_id={c.chunk_id}] ({c.doc_name}) {c.snippet}" for c in chunks]
    if not lines:
        return {}

    answer = invoke_structured(deps.llm, GradeList, grader_messages("\n".join(lines)))

    graded: dict[str, list[Evidence]] = {}
    for grade in answer.grades:
        # Only accept pairs we actually sent; ignore any id the model made up.
        chunk = next(
            (c for c in candidates.get(grade.requirement_id, []) if c.chunk_id == grade.chunk_id),
            None,
        )
        if chunk and grade.relevant and grade.score >= MIN_GRADE_SCORE:
            graded.setdefault(grade.requirement_id, []).append(
                chunk.model_copy(update={"relevance": grade.score})
            )
    return graded


def _merge(old: list[Evidence], new: list[Evidence]) -> list[Evidence]:
    """Combine evidence lists: one entry per chunk (best score), strongest first, max 4."""
    best: dict[str, Evidence] = {}
    for item in [*old, *new]:
        if item.chunk_id not in best or item.relevance > best[item.chunk_id].relevance:
            best[item.chunk_id] = item
    ranked = sorted(best.values(), key=lambda e: e.relevance, reverse=True)
    return ranked[:MAX_EVIDENCE_PER_REQUIREMENT]


def retrieve_evidence(state: ApplicationState, deps: GraphDeps) -> dict:
    """Search and grade evidence for every requirement that still needs it.

    Steps: (1) plan queries if none are pending, (2) vector search, top 4 per query,
    (3) add the user's note as a candidate if they gave one, (4) LLM grading,
    (5) merge the relevant chunks into `evidence`.
    """
    requirements = {r.id: r for r in state["requirements"]}
    evidence = dict(state.get("evidence") or {})
    queries = state.get("search_queries") or {}

    if not queries:
        todo = _todo_requirements(state)
        if not todo:
            return {"search_queries": {}}
        answer = invoke_structured(
            deps.llm, SearchQueries, query_planner_messages(_requirements_block(todo))
        )
        queries = _queries_for(todo, answer)

    candidates = {rid: list(deps.search(query, TOP_K)) for rid, query in queries.items()}

    note = state.get("extra_context")
    if note:
        note_evidence = Evidence(
            chunk_id=USER_NOTE_CHUNK_ID, doc_name="Your note", snippet=note[:300], relevance=1.0
        )
        for chunks in candidates.values():
            chunks.append(note_evidence)

    for rid, chunks in _grade(deps, requirements, candidates).items():
        evidence[rid] = _merge(evidence.get(rid, []), chunks)

    return {"evidence": evidence, "search_queries": queries}


def needs_rewrite(state: ApplicationState) -> bool:
    """True if some searched requirement found nothing and we may still rewrite (max 2 times)."""
    evidence = state.get("evidence") or {}
    missing = [rid for rid in state.get("search_queries") or {} if not evidence.get(rid)]
    return bool(missing) and state.get("rag_iterations", 0) < MAX_REWRITES


def rewrite_queries(state: ApplicationState, deps: GraphDeps) -> dict:
    """Write new queries for the requirements whose search found nothing relevant."""
    requirements = {r.id: r for r in state["requirements"]}
    evidence = state.get("evidence") or {}
    failed = {rid: query for rid, query in state["search_queries"].items() if not evidence.get(rid)}
    block = "\n".join(
        f"{rid}: {requirements[rid].text} | previous query: {query}"
        for rid, query in failed.items()
    )
    answer = invoke_structured(deps.llm, SearchQueries, query_rewriter_messages(block))
    new_queries = _queries_for([requirements[rid] for rid in failed], answer)
    return {"search_queries": new_queries, "rag_iterations": state.get("rag_iterations", 0) + 1}
