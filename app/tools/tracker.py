# What: the tracker tools: `get_applications` (read the board) and `update_tracker` (change a
#       job's status, add a note or set the next action).
# Why: lets the copilot answer "summarise my applications" and act on "move it to Applied".
#      `update_tracker` writes data, so the copilot graph asks the user to approve every call
#      before it runs (human-in-the-loop for write tools, §6.2).
# Used by: app/tools/registry.py, app/agent/copilot_graph.py (approval summary).

from datetime import UTC, datetime
from typing import Literal

from langchain_core.tools import tool
from pydantic import BaseModel, Field

from app.agent.prompts import TOOL_DESCRIPTIONS
from app.config import TRACKER_NOTE_MAX_CHARS as MAX_NOTE_CHARS
from app.db import repo
from app.db.models import JobStatus
from app.utils.errors import GuardBlockedError, InvalidInputError, NotFoundError
from app.validation.models import TrackerUpdateInput, check_input

Status = Literal["saved", "applied", "interview", "offer", "rejected"]


class GetApplicationsArgs(BaseModel):
    status: Status | None = Field(default=None, description="Only jobs with this status")


class UpdateTrackerArgs(BaseModel):
    job_id: int = Field(description="The job id from get_applications")
    status: Status | None = Field(default=None, description="New board column")
    note: str | None = Field(default=None, description="A note to add to the job")
    next_action: str | None = Field(default=None, description="e.g. 'Follow up on Friday'")


def list_applications(status: str | None = None) -> str:
    """The board as one line per job, newest first."""
    with repo.session_scope() as session:
        jobs = repo.list_jobs(session, status)
    if not jobs:
        return "The tracker is empty." if status is None else f"No jobs with status {status}."
    lines = []
    for j in jobs:
        fit = f"fit {j.fit_score}" if j.fit_score is not None else "no fit score"
        nxt = f", next: {j.next_action}" if j.next_action else ""
        lines.append(
            f"- id {j.id}: {j.title} at {j.company} | {j.status} | {fit} | "
            f"added {j.created_at:%Y-%m-%d}{nxt}"
        )
    return "\n".join(lines)


def describe_update(
    job_id: int, status: str | None = None, note: str | None = None, next_action: str | None = None
) -> str:
    """A plain-English summary of a tracker change, shown on the approval card.

    Example: "Job #3 (Junior AI Engineer at Lumen Health): move to Applied".
    """
    with repo.session_scope() as session:
        try:
            job = repo.get_job(session, job_id)
            name = f"Job #{job_id} ({job.title} at {job.company})"
        except NotFoundError:
            name = f"Job #{job_id} (not found)"
    changes = []
    if status:
        changes.append(f"move to {status.capitalize()}")
    if note:
        changes.append(f'add the note "{note[:MAX_NOTE_CHARS]}"')
    if next_action:
        changes.append(f'set next action to "{next_action}"')
    return f"{name}: {', '.join(changes) or 'no change'}"


def apply_update(
    job_id: int, status: str | None = None, note: str | None = None, next_action: str | None = None
) -> str:
    """Change the job and return what was done.

    Raises:
        GuardBlockedError: nothing to change.
        InvalidInputError: the note or next action is too long, or the status is unknown.
        NotFoundError: the job doesn't exist.
    """
    # The same rules as the Notes tab: the copilot is just another way in (defence in depth).
    clean, errors = check_input(
        TrackerUpdateInput,
        {"job_id": job_id, "status": status, "note": note, "next_action": next_action},
    )
    if clean is None:
        if "__all__" in errors:
            raise GuardBlockedError("empty update", user_message=errors["__all__"])
        raise InvalidInputError(errors)
    status, note, next_action = clean.status, clean.note, clean.next_action
    fields: dict = {}
    with repo.session_scope() as session:
        job = repo.get_job(session, job_id)
        if status:
            fields["status"] = JobStatus(status).value
        if note:
            stamp = datetime.now(UTC).strftime("%Y-%m-%d")
            entry = f"{stamp}: {note}"
            fields["notes"] = f"{job.notes}\n{entry}".strip()
        if next_action:
            fields["next_action"] = next_action.strip()
        repo.update_job(session, job_id, **fields)
    return "Done. " + describe_update(job_id, status, note, next_action)


@tool(
    "get_applications",
    description=TOOL_DESCRIPTIONS["get_applications"],
    args_schema=GetApplicationsArgs,
)
def get_applications(status: str | None = None) -> str:
    return list_applications(status)


@tool(
    "update_tracker",
    description=TOOL_DESCRIPTIONS["update_tracker"],
    args_schema=UpdateTrackerArgs,
)
def update_tracker(
    job_id: int, status: str | None = None, note: str | None = None, next_action: str | None = None
) -> str:
    return apply_update(job_id, status, note, next_action)
