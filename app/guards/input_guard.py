# What: checks user input before any LLM call. Job text: not empty, not too short or too long,
#       and a scan for common prompt-injection phrases. Chat messages and mock interview
#       answers: not empty, not too long, and no attempts to change the AI's instructions.
# Why: cheap checks in Python stop obvious bad input early (saves money) and give a clear message.
#      In a job posting, injection phrases are only flagged: the prompts treat the posting as
#      data (delimiters), so a posting that mentions "system prompt" can still be analysed. In
#      the chat, the text IS the instruction, so an injection attempt is refused instead.
#      The second chat layer, the LLM topic classifier, lives in app/agent/copilot_graph.py.
#      Lengths, empty input and control characters use the central rules in app/validation/.
# Used by: app/agent/nodes/ingest.py (input_guard node), app/agent/copilot_graph.py,
#          app/tools/job_posting.py, app/services/interview.py, app/services/help.py.

import re
from dataclasses import dataclass, field

from app.config import ANSWER_MAX_CHARS as MAX_ANSWER_CHARS
from app.config import CHAT_MAX_CHARS as MAX_CHAT_CHARS
from app.config import JOB_TEXT_MAX_CHARS as MAX_JOB_TEXT_CHARS
from app.config import JOB_TEXT_MIN_CHARS as MIN_JOB_TEXT_CHARS
from app.validation import validators as V
from app.validation.models import ChatInput, InterviewAnswerInput, check_input
from app.validation.validators import looks_like_url

__all__ = [
    "MAX_ANSWER_CHARS",
    "MAX_CHAT_CHARS",
    "MAX_JOB_TEXT_CHARS",
    "MIN_JOB_TEXT_CHARS",
    "looks_like_url",
]

# Attempts to override the model's instructions. Flagged in job postings, refused in the chat.
_OVERRIDE_PATTERNS = [
    r"ignore (all |any )?(the |your )?(previous|prior|above|earlier) (instructions|prompts|rules)",
    r"disregard (all |any )?(the |your )?(previous|prior|above) ",
    r"forget (all |everything |your )?(previous |prior )?(instructions|rules|you were told)",
    r"override (your|the|all) (instructions|rules|guidelines)",
    r"reveal (your|the) (system )?(prompt|instructions)",
    r"\b(developer|god|dan) mode\b",
    r"\bjailbreak",
    r"</?(system|assistant|instructions?)>",
    r"\[/?(system|inst)\]",
]
# Phrases that are suspicious inside a job posting but normal in a chat ("you are now my
# interview coach", "what is a system prompt?"). Flagged in postings only.
_SUSPICIOUS_PATTERNS = [
    r"\bsystem prompt\b",
    r"you are now\b",
    r"\bnew instructions\b",
    r"rate (this|the) candidate (as )?(100|perfect|excellent)",
]
_OVERRIDE_RE = re.compile("|".join(_OVERRIDE_PATTERNS), re.IGNORECASE)
_INJECTION_RE = re.compile("|".join(_OVERRIDE_PATTERNS + _SUSPICIOUS_PATTERNS), re.IGNORECASE)


@dataclass
class GuardResult:
    """The outcome of a guard check. `allowed=False` means: stop and show `reason`."""

    allowed: bool
    reason: str = ""
    warnings: list[str] = field(default_factory=list)


def find_injection_phrases(text: str, *, overrides_only: bool = False) -> list[str]:
    """Return the suspicious phrases found in `text` (empty list if none).

    Args:
        text: the text to scan.
        overrides_only: only look for attempts to override instructions (used for the chat).
    """
    pattern = _OVERRIDE_RE if overrides_only else _INJECTION_RE
    return sorted({match.group(0) for match in pattern.finditer(text)})


def check_job_text(text: str) -> GuardResult:
    """Check pasted (or downloaded) job text before it reaches the LLM.

    Returns:
        A GuardResult. Too short or too long text is not allowed; injection phrases only
        produce a warning.
    """
    try:
        stripped = V.job_text(text)  # the same rules as the Add job dialog (app/validation/)
    except ValueError as exc:
        return GuardResult(False, str(exc))

    warnings = []
    phrases = find_injection_phrases(stripped)
    if phrases:
        warnings.append(
            "The posting contains text that looks like instructions to an AI "
            f"({', '.join(repr(p) for p in phrases)}). I treated it as plain text and ignored it."
        )
    return GuardResult(True, warnings=warnings)


TOO_LONG_CHAT = (
    f"That message is too long for me (max {MAX_CHAT_CHARS:,} characters). For a job posting, "
    "use ➕ Add job on the Board instead."
)
INJECTION_CHAT = (
    "I can't change my instructions or role, but I'm happy to help with your job search: your "
    "applications, companies, job ads or your experience."
)


def check_chat_message(text: str) -> GuardResult:
    """Check a chat message with cheap Python rules (no LLM).

    Returns:
        A GuardResult; `reason` is the copilot's answer when the message is refused.
    """
    clean, errors = check_input(ChatInput, {"text": text})
    if clean is None:
        too_long = len(V.clean_multiline(text)) > MAX_CHAT_CHARS
        return GuardResult(False, TOO_LONG_CHAT if too_long else errors["text"])
    if find_injection_phrases(clean.text, overrides_only=True):
        return GuardResult(False, INJECTION_CHAT)
    return GuardResult(True)


EMPTY_ANSWER = "Please type your answer first, or end the interview."
TOO_LONG_ANSWER = (
    f"That answer is too long (max {MAX_ANSWER_CHARS:,} characters). In a real interview, aim "
    "for about two minutes."
)
INJECTION_ANSWER = "Please answer the interview question; I can't change my instructions."


def check_interview_answer(text: str) -> GuardResult:
    """Check a mock interview answer with the chat's Python rules (no LLM)."""
    clean, errors = check_input(InterviewAnswerInput, {"text": text})
    if clean is None:
        cleaned = V.clean_multiline(text)
        if not cleaned:
            return GuardResult(False, EMPTY_ANSWER)
        return GuardResult(
            False, TOO_LONG_ANSWER if len(cleaned) > MAX_ANSWER_CHARS else errors["text"]
        )
    if find_injection_phrases(clean.text, overrides_only=True):
        return GuardResult(False, INJECTION_ANSWER)
    return GuardResult(True)
