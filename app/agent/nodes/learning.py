# What: learning the user's writing style. `edit_diff` lists, in plain Python, the sentences the
#       user changed in a draft; `propose_rules` asks the LLM for 0-3 general style rules from
#       that diff (or from repeated thumbs-down comments) and drops duplicates of known rules;
#       `learn_from_edits` is the graph node that runs this when the user accepts edited drafts.
# Why: users shouldn't have to delete "passionate" from every letter. A learned rule goes into
#      long-term memory, the drafting prompt follows it, and the output guard checks its
#      `avoid_phrases` in Python (PROJECT_PLAN.md §6.5).
# Used by: app/agent/application_graph.py (node), app/services/learning.py (edits after
#          acceptance, ratings), tests/test_learning.py.

import re
from difflib import SequenceMatcher

from langchain_core.language_models import BaseChatModel

from app.agent.deps import GraphDeps
from app.agent.llm import invoke_structured
from app.agent.prompts import feedback_learner_messages, style_learner_messages
from app.agent.state import ApplicationState, Drafts, StyleRule, StyleRuleProposal
from app.utils.errors import AppError
from app.utils.logging import get_logger

logger = get_logger(__name__)

MAX_RULES_PER_EDIT = 3  # §6.5: "0-3 general style rules"
MAX_DIFF_CHARS = 4_000  # a full rewrite isn't worth sending in full
DUPLICATE_SIMILARITY = 0.85  # rules this similar (0..1) to a known rule count as the same
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+|\n+")


def _sentences(text: str) -> list[str]:
    return [s.strip() for s in _SENTENCE_END.split(text) if s.strip()]


def _diff_lines(before: list[str], after: list[str]) -> list[str]:
    lines = []
    matcher = SequenceMatcher(a=before, b=after, autojunk=False)
    for op, a1, a2, b1, b2 in matcher.get_opcodes():
        if op == "equal":
            continue
        lines += [f"- {s}" for s in before[a1:a2]]
        lines += [f"+ {s}" for s in after[b1:b2]]
    return lines


def edit_diff(before: Drafts, after: Drafts) -> str:
    """What the user changed, sentence by sentence ("- old" / "+ new"). Empty if nothing.

    Sentences, not characters: "- I am passionate about AI." / "+ I enjoy AI." shows the
    LLM the whole context of a change in a few tokens.
    """
    parts = []
    bullets = _diff_lines(before.cv_bullets, after.cv_bullets)
    if bullets:
        parts.append("CV bullets:\n" + "\n".join(bullets))
    letter = _diff_lines(_sentences(before.cover_letter.text), _sentences(after.cover_letter.text))
    if letter:
        parts.append("Cover letter:\n" + "\n".join(letter))
    return "\n\n".join(parts)[:MAX_DIFF_CHARS]


def _normalise(text: str) -> str:
    return " ".join(re.findall(r"[a-z0-9']+", text.lower()))


def is_duplicate(rule: StyleRule, existing: list[StyleRule]) -> bool:
    """True if a known rule says the same: near-identical wording or the same banned words."""
    words = {p.lower() for p in rule.avoid_phrases}
    for known in existing:
        if words and words <= {p.lower() for p in known.avoid_phrases}:
            return True
        ratio = SequenceMatcher(a=_normalise(rule.rule), b=_normalise(known.rule)).ratio()
        if ratio >= DUPLICATE_SIMILARITY:
            return True
    return False


def _to_rules(
    proposal: StyleRuleProposal,
    existing: list[StyleRule],
    source: str,
    diff: str | None = None,
) -> list[StyleRule]:
    """Turn the LLM's proposal into new StyleRules: cleaned, checked and without duplicates."""
    if diff is not None:
        deleted = "\n".join(x for x in diff.splitlines() if x.startswith("- ")).lower()
        added = "\n".join(x for x in diff.splitlines() if x.startswith("+ ")).lower()
    rules: list[StyleRule] = []
    for proposed in proposal.rules[:MAX_RULES_PER_EDIT]:
        text = proposed.rule.strip()
        if not text:
            continue
        phrases = [p.strip() for p in proposed.avoid_phrases if p.strip()]
        if diff is not None:
            # Only words the user really deleted (and didn't write again) can be banned; the
            # model can't add its own. A rule whose every banned word fails this check is
            # about something the user did NOT delete (e.g. a word they just added), so it is
            # dropped as a whole.
            kept = [p for p in phrases if p.lower() in deleted and p.lower() not in added]
            if phrases and not kept:
                continue
            phrases = kept
        rule = StyleRule(rule=text, source=source, avoid_phrases=phrases)
        if not is_duplicate(rule, existing + rules):
            rules.append(rule)
    return rules


def propose_rules(
    llm: BaseChatModel | None, before: Drafts, after: Drafts, existing: list[StyleRule]
) -> list[StyleRule]:
    """New style rules learned from the user's edit (empty if nothing changed or nothing general).

    Raises:
        ConfigError / LLMServiceError / StructuredOutputError: the LLM call failed.
    """
    diff = edit_diff(before, after)
    if not diff:
        return []
    proposal = invoke_structured(
        llm, StyleRuleProposal, style_learner_messages(diff, [r.rule for r in existing])
    )
    return _to_rules(proposal, existing, "edit_diff", diff)


def rules_from_feedback(
    llm: BaseChatModel | None, target: str, comments: list[str], existing: list[StyleRule]
) -> list[StyleRule]:
    """At most one rule from a complaint that repeats in thumbs-down comments.

    Raises:
        ConfigError / LLMServiceError / StructuredOutputError: the LLM call failed.
    """
    if len(comments) < 2:
        return []
    noun = {"letter": "cover letters", "bullets": "CV bullets"}.get(target, target)
    proposal = invoke_structured(
        llm,
        StyleRuleProposal,
        feedback_learner_messages(noun, comments, [r.rule for r in existing]),
    )
    proposal = proposal.model_copy(update={"rules": proposal.rules[:1]})
    return _to_rules(proposal, existing, "rating")


# ---------------------------------------------------------------- node


def learn_from_edits(state: ApplicationState, deps: GraphDeps) -> dict:
    """After "Accept & save": learn style rules from the user's edits and remember them.

    Learning is a bonus, so a failed LLM call is logged and skipped: the drafts are still
    saved and the workflow still finishes.
    """
    drafts, edits = state.get("drafts"), state.get("user_edits")
    if drafts is None or edits is None or edits == drafts:
        return {"learned_rules": []}
    try:
        rules = propose_rules(deps.llm, drafts, edits, deps.list_style_rules())
    except AppError as exc:
        logger.warning("Learning from edits skipped: %s", exc)
        return {"learned_rules": []}
    learned = [{"key": deps.add_style_rule(rule), "rule": rule.rule} for rule in rules]
    return {"learned_rules": learned}
