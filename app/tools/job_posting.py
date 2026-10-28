# What: the `fetch_job_posting` tool: reads a job posting from a URL (through the URL guard)
#       or from pasted text and returns its key facts.
# Why: in the chat, users often paste a link and ask "is this for me?". The tool reuses the
#      same guards and extraction prompt as the application workflow, so it is equally safe.
# Used by: app/tools/registry.py (copilot chat).

from collections.abc import Callable

from langchain_core.language_models import BaseChatModel
from langchain_core.tools import tool
from pydantic import BaseModel, Field

from app.agent.llm import get_llm, invoke_structured
from app.agent.prompts import TOOL_DESCRIPTIONS, job_extraction_messages
from app.agent.state import JobPostingExtraction
from app.guards.input_guard import check_job_text, looks_like_url
from app.services.web_fetch import fetch_page_text
from app.utils.errors import GuardBlockedError


class FetchJobPostingArgs(BaseModel):
    url_or_text: str = Field(description="A job ad URL (http/https) or the full posting text")


def read_job_posting(
    url_or_text: str,
    llm: BaseChatModel | None = None,
    fetch: Callable[[str], str] = fetch_page_text,
) -> str:
    """Download (if it's a URL), check and extract a posting; return its facts as text.

    Raises:
        GuardBlockedError: unsafe URL, or text too short/long.
        FetchError: the page couldn't be read (the user should paste the text instead).
    """
    raw = url_or_text.strip()
    text = fetch(raw) if looks_like_url(raw) else raw
    check = check_job_text(text)
    if not check.allowed:
        raise GuardBlockedError(check.reason, user_message=check.reason)

    job = invoke_structured(llm or get_llm(), JobPostingExtraction, job_extraction_messages(text))
    if not job.is_job_posting:
        return "This doesn't look like a job posting."
    languages = ", ".join(str(lang) for lang in job.language_requirements) or "none stated"
    return "\n".join(
        [
            f"Title: {job.title}",
            f"Company: {job.company}",
            f"Location: {job.location or 'not stated'} ({job.remote_policy})",
            f"Seniority: {job.seniority}",
            f"Languages: {languages}",
            f"Visa sponsorship: {job.visa_sponsorship}",
            f"Salary: {job.salary_range or 'not stated'}",
            f"Summary: {job.summary}",
            *check.warnings,
            "Note: this job is not on the board yet. Add it on the Board page to run the full "
            "fit analysis.",
        ]
    )


@tool(
    "fetch_job_posting",
    description=TOOL_DESCRIPTIONS["fetch_job_posting"],
    args_schema=FetchJobPostingArgs,
)
def fetch_job_posting(url_or_text: str) -> str:
    return read_job_posting(url_or_text)
