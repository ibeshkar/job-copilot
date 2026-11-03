# What: "learn from feedback" for the pages: learn style rules from edits made after the drafts
#       were accepted, from repeated thumbs-down comments, list the rules a run learned that
#       still exist, and undo one.
# Why: the graph node `learn_from_edits` covers edits at "Accept & save"; this covers the other
#      two ways a user gives feedback. Learning is a bonus, so these functions never raise:
#      a failure is logged and nothing is learned. Tokens are logged as the "learn_style" action.
# Used by: app/ui/pages/2_job_workspace.py, tests/test_learning.py.

from dataclasses import dataclass

from langchain_core.language_models import BaseChatModel

from app.agent.llm import get_llm
from app.agent.nodes.learning import propose_rules, rules_from_feedback
from app.agent.state import Drafts, StyleRule
from app.config import get_settings
from app.db import repo
from app.memory import store
from app.services.observability import track_run
from app.utils.errors import InvalidInputError
from app.utils.logging import get_logger, log_exception

logger = get_logger(__name__)

MAX_COMMENTS = 5  # the newest thumbs-down comments looked at


@dataclass
class LearnedRule:
    """A rule the app just learned; `key` is needed to undo it."""

    key: str
    rule: str


def _save(rules: list[StyleRule]) -> list[LearnedRule]:
    saved = []
    for rule in rules:
        try:
            saved.append(LearnedRule(store.add_style_rule(rule), rule.rule))
        except InvalidInputError as exc:  # the store's rules said no (e.g. far too long)
            logger.warning("Learned rule not saved: %s", exc)
    return saved


def get_model() -> BaseChatModel | None:
    """The LLM used for learning, or None without an API key. Tests replace this function."""
    return get_llm() if get_settings().has_llm_key else None


def learn_from_edit(
    job_id: int, before: Drafts, after: Drafts, llm: BaseChatModel | None = None
) -> list[LearnedRule]:
    """Learn rules from an edit saved after acceptance (the "💾 Save changes" button)."""
    model = llm or get_model()
    if model is None or before == after:
        return []
    try:
        with track_run("learn_style", job_id=job_id):
            existing = list(store.list_style_rules().values())
            return _save(propose_rules(model, before, after, existing))
    except Exception:
        log_exception(logger, "Learning from an edit failed", job_id=job_id)
        return []


def learn_from_ratings(target: str, llm: BaseChatModel | None = None) -> list[LearnedRule]:
    """Turn a complaint that repeats in thumbs-down comments on `target` into a rule.

    Args:
        target: "letter" or "bullets".
    """
    with repo.session_scope() as session:
        rows = repo.list_feedback(session, target)
    comments = [r.comment.strip() for r in rows if r.rating == -1 and r.comment.strip()]
    model = llm or get_model()
    if model is None or len(comments) < 2:
        return []
    try:
        with track_run("learn_style"):
            existing = list(store.list_style_rules().values())
            return _save(rules_from_feedback(model, target, comments[:MAX_COMMENTS], existing))
    except Exception:
        log_exception(logger, "Learning from ratings failed", target=target)
        return []


def still_saved(learned: list[dict]) -> list[LearnedRule]:
    """The rules a run learned (`WorkflowView.learned_rules`) that haven't been undone."""
    keys = store.list_style_rules().keys()
    return [LearnedRule(r["key"], r["rule"]) for r in learned if r.get("key") in keys]


def undo(key: str) -> None:
    """Forget a learned rule ("↩️ Undo")."""
    store.delete_style_rule(key)
