# What: searches the knowledge base and returns `Evidence` objects (snippet + file + score).
# Why: graph nodes and the copilot's `search_my_experience` tool need citations they can show
#      the user, not raw vector-store results.
# Used by: later app/agent/nodes/evidence.py (agentic RAG) and app/tools/experience.py.

from langchain_chroma import Chroma

from app.agent.state import Evidence
from app.rag.ingest import get_vectorstore

SNIPPET_MAX_CHARS = 300


def search_evidence(
    query: str,
    k: int = 4,
    doc_types: list[str] | None = None,
    vectorstore: Chroma | None = None,
) -> list[Evidence]:
    """Find the chunks of the user's documents that best match `query`.

    Args:
        query: what to look for, e.g. "built a RAG chatbot".
        k: how many results to return at most.
        doc_types: only search these kinds of documents, e.g. ["cv", "project"].
        vectorstore: the store to search; defaults to the app's knowledge base.

    Returns:
        Evidence sorted from most to least relevant. Empty if the knowledge base is empty.
    """
    vectorstore = vectorstore or get_vectorstore()
    if not query.strip() or not vectorstore.get(limit=1, include=[])["ids"]:
        return []

    where = {"doc_type": {"$in": doc_types}} if doc_types else None
    results = vectorstore.similarity_search_with_relevance_scores(query, k=k, filter=where)
    return [
        Evidence(
            chunk_id=chunk.metadata["chunk_id"],
            doc_name=chunk.metadata["doc_name"],
            snippet=chunk.page_content[:SNIPPET_MAX_CHARS],
            # Cosine relevance can be slightly outside 0..1 because of float rounding.
            relevance=min(max(score, 0.0), 1.0),
        )
        for chunk, score in results
    ]
