# What: the `search_my_experience` tool: searches the user's own documents for evidence of a
#       skill or experience and returns quotes with file names.
# Why: the copilot may only say things about the user that their documents (or profile) show.
#      This tool is how it looks them up, with citations the user can check. With an empty
#      knowledge base it says so, so the copilot can point the user to Get started instead of
#      guessing.
# Used by: app/tools/registry.py (copilot chat).

from langchain_core.tools import tool
from pydantic import BaseModel, Field

from app.agent.prompts import TOOL_DESCRIPTIONS, wrap
from app.rag.retriever import search_evidence
from app.services.readiness import get_setup_status

MAX_K = 8
NO_DOCUMENTS_RESULT = (
    "The user hasn't uploaded any documents yet, so there is nothing to search. Tell them to "
    "upload their CV on the Get started page (or use the demo persona), and don't guess "
    "about their experience."
)


class SearchExperienceArgs(BaseModel):
    query: str = Field(description="What to look for, e.g. 'built a RAG chatbot' or 'SQL'")
    k: int = Field(default=5, description="How many results, 1-8")


def find_experience(query: str, k: int = 5) -> str:
    """Search the knowledge base and format the hits as "[file] quote" lines."""
    hits = search_evidence(query, k=max(1, min(k, MAX_K)))
    if not hits:
        if not get_setup_status().has_documents:
            return NO_DOCUMENTS_RESULT
        return f"No evidence found in the user's documents for: {query}"
    lines = [f"[{e.doc_name}] (relevance {e.relevance:.2f}) {e.snippet}" for e in hits]
    # The user's own documents, but still data: a CV can't give the copilot instructions.
    return wrap("candidate_evidence", "\n".join(lines))


@tool(
    "search_my_experience",
    description=TOOL_DESCRIPTIONS["search_my_experience"],
    args_schema=SearchExperienceArgs,
)
def search_my_experience(query: str, k: int = 5) -> str:
    return find_experience(query, k)
