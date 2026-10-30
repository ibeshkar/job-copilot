# What: the Salary tab's actions: run the salary estimate (tracked as "estimate_salary") and
#       read the cached one.
# Why: pages never call tools or the LLM directly, and never see a stack trace. The pipeline
#      itself lives in app/tools/salary.py, so the Salary tab and the copilot share it.
# Used by: app/ui/pages/2_job_workspace.py.

from app.agent.state import SalaryEstimate
from app.services.observability import track_run
from app.tools.salary import estimate_for_job, saved_estimate
from app.utils.errors import friendly_message
from app.utils.logging import get_logger, log_exception

logger = get_logger(__name__)

__all__ = ["run_estimate", "saved_estimate"]


def run_estimate(job_id: int) -> tuple[SalaryEstimate | None, str]:
    """Estimate the salary and cache it. Never raises: (estimate, "") or (None, message)."""
    try:
        with track_run("estimate_salary", job_id=job_id):
            return estimate_for_job(job_id), ""
    except Exception as exc:
        log_exception(logger, "Salary estimate failed", job_id=job_id)
        return None, friendly_message(exc)
