# What: the `extract_requirements` step: the LLM lists what the job asks for, each item
#       tagged as must-have or nice-to-have.
# Why: the fit analysis judges the candidate requirement by requirement, so we need a clean,
#      numbered list (R1, R2, ...) to search evidence for and to score.
# Used by: app/agent/application_graph.py.

from app.agent.deps import GraphDeps
from app.agent.llm import invoke_structured
from app.agent.prompts import requirements_messages
from app.agent.state import ApplicationState, Requirement, RequirementList

MAX_REQUIREMENTS = 15  # keeps the later grading prompt small and the fit card readable


def extract_requirements(state: ApplicationState, deps: GraphDeps) -> dict:
    """Extract up to 15 requirements and give them stable ids R1, R2, ...

    The ids are assigned here in Python, not by the LLM, so they are always unique.
    """
    result = invoke_structured(
        deps.llm, RequirementList, requirements_messages(state["job"].raw_text)
    )
    requirements = [
        Requirement(id=f"R{i}", text=r.text, category=r.category, importance=r.importance)
        for i, r in enumerate(result.requirements[:MAX_REQUIREMENTS], start=1)
    ]
    if not requirements:
        return {"errors": ["I couldn't find any requirements in this posting."]}
    return {"requirements": requirements, "search_queries": {}, "evidence": {}, "rag_iterations": 0}
