# What: the one place that answers "is the user set up?": a profile saved, and at least one
#       document in the knowledge base. `get_setup_status()` reads both; `SetupStatus` also
#       carries the friendly message to show when something is missing.
# Why: fit analysis compares a job with the user's profile and documents, so without them it
#      can only fail. The Board, the "Add a job" dialog, the application graph's input guard and
#      the copilot chat all ask the same question; one function keeps their answers identical.
# Used by: app/ui/pages/1_board.py, app/ui/pages/3_copilot_chat.py, app/ui/components.py,
#          app/agent/nodes/ingest.py (input_guard), tests/test_readiness.py.

from dataclasses import dataclass

from app.memory.store import Profile

NOT_READY_MESSAGE = (
    "Before I can analyse a job, I need your profile and at least one document (like your CV) "
    "to compare it with. Go to **Get started**, or use the demo persona for a quick try."
)
NO_PROFILE_MESSAGE = (
    "Before I can analyse a job, please create your profile on the **Get started** page "
    "(or use the demo persona). Your documents are already in place."
)
NO_DOCUMENTS_MESSAGE = (
    "Your knowledge base is empty, so I have no evidence to compare the job with. Upload your "
    "CV on the **Get started** page, or use the demo persona."
)
# Short version for the tooltip of the disabled "➕ Add job" button.
ADD_JOB_LOCKED_HELP = "Set up your profile and upload your CV first."


@dataclass(frozen=True)
class SetupStatus:
    """What the user has set up so far."""

    has_profile: bool
    has_documents: bool
    chunk_count: int = 0

    @property
    def is_ready(self) -> bool:
        """True when fit analysis can run: a profile and at least one document."""
        return self.has_profile and self.has_documents

    @property
    def message(self) -> str:
        """What is missing, in friendly words ("" when ready)."""
        if self.is_ready:
            return ""
        if not self.has_profile and not self.has_documents:
            return NOT_READY_MESSAGE
        return NO_PROFILE_MESSAGE if not self.has_profile else NO_DOCUMENTS_MESSAGE


def setup_status(profile: Profile | None, chunk_count: int) -> SetupStatus:
    """Build a SetupStatus from a profile and a chunk count (the graph passes its own)."""
    return SetupStatus(
        has_profile=profile is not None,
        has_documents=chunk_count > 0,
        chunk_count=chunk_count,
    )


def get_setup_status() -> SetupStatus:
    """The user's setup, read from the memory store and the `Document` table.

    Uses the database, not Chroma, so it works without an API key and costs nothing.
    """
    from app.memory.store import get_profile
    from app.services.onboarding import knowledge_base_summary

    chunks, _ = knowledge_base_summary()
    return setup_status(get_profile(), chunks)
