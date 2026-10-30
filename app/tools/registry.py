# What: TOOL_REGISTRY, the list of the copilot's 6 tools with a user-friendly label, a
#       description for the sidebar toggle, whether it's on by default, which tool calls need
#       the user's approval, and which API keys it needs.
# Why: one place decides what the copilot can do. The sidebar toggles, the chat graph's dynamic
#      tool binding and the approval step all read from here.
# Used by: app/main.py (sidebar toggles), app/services/copilot.py, app/agent/copilot_graph.py.

from collections.abc import Iterable
from dataclasses import dataclass

from langchain_core.tools import BaseTool

from app.config import Settings, get_settings
from app.tools.company import research_company
from app.tools.experience import search_my_experience
from app.tools.job_posting import fetch_job_posting
from app.tools.job_search import search_jobs_tool
from app.tools.salary import estimate_salary
from app.tools.tracker import get_applications, update_tracker


@dataclass(frozen=True)
class ToolSpec:
    """One toggle in the sidebar. A toggle can hold several tools (the tracker has two)."""

    name: str
    label: str
    description: str
    tools: tuple[BaseTool, ...]
    default_enabled: bool = True
    requires_approval: frozenset[str] = frozenset()  # names of tools that write data
    required_env_keys: tuple[str, ...] = ()


TOOL_REGISTRY: dict[str, ToolSpec] = {
    spec.name: spec
    for spec in [
        ToolSpec(
            name="job_posting",
            label="📄 Read job postings",
            description="Read a job ad from a link or pasted text.",
            tools=(fetch_job_posting,),
        ),
        ToolSpec(
            name="experience",
            label="🔎 Search my experience",
            description="Search your uploaded documents for evidence.",
            tools=(search_my_experience,),
        ),
        ToolSpec(
            name="company",
            label="🏢 Research companies",
            description="Web search about a company (Tavily).",
            tools=(research_company,),
            required_env_keys=("TAVILY_API_KEY",),
        ),
        ToolSpec(
            name="job_search",
            label="🌍 Search job ads",
            description="Find open roles on Arbeitnow (Germany/EU, no key needed).",
            tools=(search_jobs_tool,),
            default_enabled=True,
        ),
        ToolSpec(
            name="salary",
            label="💰 Estimate salaries",
            description="Salary range for a job from web data (Tavily).",
            tools=(estimate_salary,),
            default_enabled=True,
            required_env_keys=("TAVILY_API_KEY",),
        ),
        ToolSpec(
            name="tracker",
            label="🗂️ Read and update my tracker",
            description="See your board; changes need your approval.",
            tools=(get_applications, update_tracker),
            requires_approval=frozenset({"update_tracker"}),
        ),
    ]
}

ALL_TOOLS: dict[str, BaseTool] = {t.name: t for spec in TOOL_REGISTRY.values() for t in spec.tools}
APPROVAL_TOOLS: frozenset[str] = frozenset().union(
    *(spec.requires_approval for spec in TOOL_REGISTRY.values())
)
TOOL_LABELS: dict[str, str] = {
    t.name: spec.label for spec in TOOL_REGISTRY.values() for t in spec.tools
}


def missing_keys(spec: ToolSpec, settings: Settings | None = None) -> list[str]:
    """The env keys this tool needs that aren't set, e.g. ["TAVILY_API_KEY"]."""
    settings = settings or get_settings()
    return [key for key in spec.required_env_keys if getattr(settings, key.lower()) is None]


def enabled_tool_names(toggles: Iterable[str], settings: Settings | None = None) -> list[str]:
    """Turn the switched-on toggles into tool names, skipping tools whose keys are missing.

    Example: ["tracker", "company"] → ["get_applications", "update_tracker", "research_company"]
    (without "research_company" if TAVILY_API_KEY isn't set).
    """
    names: list[str] = []
    for toggle in toggles:
        spec = TOOL_REGISTRY.get(toggle)
        if spec is not None and not missing_keys(spec, settings):
            names += [t.name for t in spec.tools]
    return names


def default_toggles(settings: Settings | None = None) -> list[str]:
    """The toggles that start switched on: default-on tools whose keys are present."""
    return [
        name
        for name, spec in TOOL_REGISTRY.items()
        if spec.default_enabled and not missing_keys(spec, settings)
    ]
