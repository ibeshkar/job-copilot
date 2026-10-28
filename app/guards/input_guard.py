# What: checks the user's job input before any LLM call: not empty, not too short or too long,
#       and a scan for common prompt-injection phrases.
# Why: cheap checks in Python stop obvious bad input early (saves money) and give a clear message.
#      Injection phrases are not blocked, only flagged: the prompts already treat the posting as
#      data (delimiters), so a posting that mentions "system prompt" can still be analysed.
# Used by: app/agent/application_graph.py (input_guard node). Phase 7 adds the chat topic check.

import re
from dataclasses import dataclass, field

MAX_JOB_TEXT_CHARS = 30_000  # PROJECT_PLAN.md §9
MIN_JOB_TEXT_CHARS = 200  # shorter than a few sentences can't be a real posting

# Phrases typical of prompt-injection attempts. A match adds a warning; it never blocks.
_INJECTION_PATTERNS = [
    r"ignore (all |any )?(the )?(previous|prior|above|earlier) (instructions|prompts|rules)",
    r"disregard (all |any )?(the )?(previous|prior|above) ",
    r"\bsystem prompt\b",
    r"you are now\b",
    r"\bnew instructions\b",
    r"reveal (your|the) (prompt|instructions)",
    r"rate (this|the) candidate (as )?(100|perfect|excellent)",
]
_INJECTION_RE = re.compile("|".join(_INJECTION_PATTERNS), re.IGNORECASE)


@dataclass
class GuardResult:
    """The outcome of a guard check. `allowed=False` means: stop and show `reason`."""

    allowed: bool
    reason: str = ""
    warnings: list[str] = field(default_factory=list)


def looks_like_url(text: str) -> bool:
    """True if the input is a single http(s) URL rather than pasted job text."""
    stripped = text.strip()
    return stripped.lower().startswith(("http://", "https://")) and " " not in stripped


def find_injection_phrases(text: str) -> list[str]:
    """Return the suspicious phrases found in `text` (empty list if none)."""
    return sorted({match.group(0) for match in _INJECTION_RE.finditer(text)})


def check_job_text(text: str) -> GuardResult:
    """Check pasted (or downloaded) job text before it reaches the LLM.

    Returns:
        A GuardResult. Too short or too long text is not allowed; injection phrases only
        produce a warning.
    """
    stripped = text.strip()
    if not stripped:
        return GuardResult(False, "Please paste a job posting or a link to one.")
    if len(stripped) < MIN_JOB_TEXT_CHARS:
        return GuardResult(
            False,
            "This text is too short to be a job posting. Please paste the full posting "
            "(title, tasks and requirements).",
        )
    if len(stripped) > MAX_JOB_TEXT_CHARS:
        return GuardResult(
            False,
            f"This text is too long ({len(stripped):,} characters). Please paste only the job "
            f"posting (max {MAX_JOB_TEXT_CHARS:,} characters).",
        )

    warnings = []
    phrases = find_injection_phrases(stripped)
    if phrases:
        warnings.append(
            "The posting contains text that looks like instructions to an AI "
            f"({', '.join(repr(p) for p in phrases)}). I treated it as plain text and ignored it."
        )
    return GuardResult(True, warnings=warnings)
