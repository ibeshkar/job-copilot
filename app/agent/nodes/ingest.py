# What: the first two graph steps: `input_guard` (is this input usable?) and `ingest_job`
#       (download the URL if needed, then extract a structured JobPosting with the LLM).
# Why: bad input should stop early with a clear message, before we spend money on LLM calls.
#      Everything later in the graph works on the clean JobPosting, not on raw text.
# Used by: app/agent/application_graph.py.

from app.agent.deps import GraphDeps
from app.agent.llm import invoke_structured
from app.agent.prompts import job_extraction_messages
from app.agent.state import (
    UNKNOWN_COMPANY,
    UNKNOWN_TITLE,
    ApplicationState,
    JobPosting,
    JobPostingExtraction,
)
from app.guards.input_guard import check_job_text, looks_like_url
from app.utils.errors import AppError

EMPTY_KB_MESSAGE = (
    "Your knowledge base is empty, so I have no evidence to compare with. Upload your CV on "
    "the Get started page, or use the demo persona."
)
NOT_A_JOB_MESSAGE = (
    "This doesn't look like a job posting. Please paste the posting's title, tasks and "
    "requirements."
)


def input_guard(state: ApplicationState, deps: GraphDeps) -> dict:
    """Stop early if there is nothing to compare against or the input is unusable.

    URLs are only checked for safety later, when `ingest_job` downloads them.
    """
    if deps.knowledge_base_size() == 0:
        return {"errors": [EMPTY_KB_MESSAGE]}

    raw = state.get("raw_input", "")
    if looks_like_url(raw):
        return {}
    result = check_job_text(raw)
    if not result.allowed:
        return {"errors": [result.reason]}
    return {"warnings": result.warnings}


def ingest_job(state: ApplicationState, deps: GraphDeps) -> dict:
    """Turn the raw input into a `JobPosting`.

    For a URL, the page is downloaded through the URL guard, then checked like pasted text.
    Problems (blocked URL, unreadable page, not a job posting) end the run with a friendly error.
    """
    raw = state["raw_input"].strip()
    url = None
    warnings: list[str] = []

    if looks_like_url(raw):
        url = raw
        try:
            text = deps.fetch_url(raw)
        except AppError as exc:
            return {"errors": [exc.user_message]}
        result = check_job_text(text)
        if not result.allowed:
            return {"errors": [result.reason]}
        warnings = result.warnings
    else:
        text = raw

    extraction = invoke_structured(deps.llm, JobPostingExtraction, job_extraction_messages(text))
    if not extraction.is_job_posting:
        return {"errors": [NOT_A_JOB_MESSAGE]}

    job = JobPosting(
        **extraction.model_dump(exclude={"title", "company"}),
        title=extraction.title.strip() or UNKNOWN_TITLE,
        company=extraction.company.strip() or UNKNOWN_COMPANY,
        raw_text=text,
        source_url=url,
    )
    return {"job": job, "warnings": warnings}
