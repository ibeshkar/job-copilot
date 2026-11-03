# What: small, pure functions that clean and check one kind of user input each: names,
#       emails, phone numbers, URLs, places, lists, salaries, dates and free text. Each returns
#       the normalised value or raises ValueError with a message the user can act on.
# Why: one set of rules for every page, the copilot and the storage layer, so the same input is
#      never accepted in one place and rejected in another. The Pydantic models in
#      app/validation/models.py are built from these; pages and services use the models.
# Used by: app/validation/models.py, app/guards/input_guard.py, app/ui/components.py (phone
#          display), app/services/export.py, tests/test_validation.py.

import re
import unicodedata
from datetime import date
from urllib.parse import urlsplit, urlunsplit

import phonenumbers
from email_validator import EmailNotValidError, validate_email

from app.config import (
    JOB_TEXT_MAX_CHARS,
    JOB_TEXT_MIN_CHARS,
    LIST_ITEM_MAX_CHARS,
    LIST_ITEM_MIN_CHARS,
    LIST_MAX_ITEMS,
    NAME_MAX_CHARS,
    NAME_MIN_CHARS,
    PHONE_DEFAULT_REGION,
    PLACE_MAX_CHARS,
    SALARY_CURRENCIES,
    SALARY_MAX_YEARLY,
    SALARY_MIN_YEARLY,
    URL_MAX_CHARS,
)

# ---------------------------------------------------------------- normalising

# Invisible characters that can hide text or flip its direction ("Trojan Source" tricks):
# zero-width spaces/joiners, bidi overrides and isolates, word joiners, the BOM.
_INVISIBLE = re.compile("[\u200b-\u200f\u202a-\u202e\u2060-\u2064\u2066-\u2069\ufeff]")
_SPACES = re.compile("[ \t\u00a0\u2000-\u200a\u202f\u205f\u3000]+")


def strip_control_chars(text: str, *, keep_newlines: bool = False) -> str:
    """Remove control characters (NUL, ESC, …) and invisible formatting characters.

    Newlines and tabs are kept only when `keep_newlines` is True (multi-line text).
    """
    # NFC joins a decomposed "e" + "¨" into one "ë", so lengths and duplicates compare fairly.
    text = unicodedata.normalize("NFC", text.replace("\r\n", "\n").replace("\r", "\n"))
    text = _INVISIBLE.sub("", text)
    out = []
    for ch in text:
        if ch in "\n\t":
            out.append(ch if keep_newlines else " ")
        elif unicodedata.category(ch) != "Cc":
            out.append(ch)
    return "".join(out)


def clean_line(value: object) -> str:
    """Single-line text: control characters removed, all whitespace runs → one space, trimmed."""
    text = strip_control_chars("" if value is None else str(value))
    return _SPACES.sub(" ", text.replace("\n", " ").replace("\t", " ")).strip()


def clean_multiline(value: object) -> str:
    """Multi-line text: like `clean_line` per line, but line breaks stay (max one empty line)."""
    text = strip_control_chars("" if value is None else str(value), keep_newlines=True)
    lines = [_SPACES.sub(" ", line.replace("\t", " ")).strip() for line in text.split("\n")]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


def _is_letter(ch: str) -> bool:
    # Letters of any script, and combining marks (the accent in a decomposed "é").
    return unicodedata.category(ch)[0] in ("L", "M")


# ---------------------------------------------------------------- free text


def free_text(
    value: object,
    *,
    label: str,
    min_chars: int = 1,
    max_chars: int,
    required: bool = True,
    multiline: bool = True,
) -> str:
    """Check free text (notes, claims, paragraphs, answers…): cleaned, not empty, within limits.

    Returns "" for an optional field left empty.
    """
    text = clean_multiline(value) if multiline else clean_line(value)
    if not text:
        if required:
            raise ValueError(f"Please enter {label}.")
        return ""
    if len(text) < min_chars:
        raise ValueError(f"{label.capitalize()} is too short (at least {min_chars} characters).")
    if len(text) > max_chars:
        raise ValueError(
            f"{label.capitalize()} is too long ({len(text):,} characters, max {max_chars:,})."
        )
    return text


# ---------------------------------------------------------------- name

_NAME_PUNCTUATION = set(" -'.’")  # "Jean-Luc", "O'Brien", "O’Brien", "J. R. R."


def person_name(value: object) -> str:
    """A person's name in any script: letters, spaces, hyphens, apostrophes and dots only.

    Examples that pass: "Esmaeil", "Zoë", "O'Brien", "Jean-Luc Picard", "José Álvarez",
    "Nguyễn Văn An", "李小龙". Digits and symbols like <>{}@ are rejected.
    """
    name = clean_line(value)
    if not name:
        raise ValueError("Please enter your name.")
    if len(name) < NAME_MIN_CHARS:
        raise ValueError(f"Your name needs at least {NAME_MIN_CHARS} characters.")
    if len(name) > NAME_MAX_CHARS:
        raise ValueError(f"Your name is too long (max {NAME_MAX_CHARS} characters).")
    if any(not (_is_letter(ch) or ch in _NAME_PUNCTUATION) for ch in name):
        raise ValueError(
            "A name can only contain letters, spaces, hyphens (-), apostrophes (') and dots."
        )
    if sum(_is_letter(ch) for ch in name) < NAME_MIN_CHARS:
        raise ValueError(f"Your name needs at least {NAME_MIN_CHARS} letters.")
    return name


# ---------------------------------------------------------------- email and phone

EMAIL_MESSAGE = "Please enter a valid email like name@example.com."


def email(value: object) -> str:
    """A syntactically valid email address, stored in lower case. No DNS lookup (works offline)."""
    text = clean_line(value)
    if not text:
        raise ValueError("Please enter your email address.")
    try:
        result = validate_email(text, check_deliverability=False)
    except EmailNotValidError as exc:
        raise ValueError(EMAIL_MESSAGE) from exc
    return result.normalized.lower()


PHONE_MESSAGE = "Please enter a valid phone number like +49 151 12345678 or 0151 12345678."
_PHONE_CHARS = re.compile(r"^[0-9+()\-./ ]+$")


def phone(value: object) -> str:
    """An optional phone number, stored in E.164 (e.g. "+4915112345678"); "" if left empty.

    Numbers without +/00 are read as German ("0151 …" → +49 151 …).
    """
    text = clean_line(value)
    if not text:
        return ""
    if not _PHONE_CHARS.match(text):
        raise ValueError(PHONE_MESSAGE)
    try:
        number = phonenumbers.parse(text, PHONE_DEFAULT_REGION)
    except phonenumbers.NumberParseException as exc:
        raise ValueError(PHONE_MESSAGE) from exc
    if not phonenumbers.is_valid_number(number):
        raise ValueError(PHONE_MESSAGE)
    return phonenumbers.format_number(number, phonenumbers.PhoneNumberFormat.E164)


def format_phone(stored: str) -> str:
    """A stored number for people to read: "+4915112345678" → "+49 1511 2345678".

    Anything that can't be parsed (old, unvalidated data) is returned unchanged.
    """
    if not stored:
        return ""
    try:
        number = phonenumbers.parse(stored, PHONE_DEFAULT_REGION)
    except phonenumbers.NumberParseException:
        return stored
    if not phonenumbers.is_valid_number(number):
        return stored
    return phonenumbers.format_number(number, phonenumbers.PhoneNumberFormat.INTERNATIONAL)


# ---------------------------------------------------------------- URLs

# For fields that must point at one site; subdomains (de.linkedin.com, www.github.com) count.
URL_DOMAINS = {"linkedin": "linkedin.com", "github": "github.com"}
_HOST_LABEL = re.compile(r"^(?!-)[a-z0-9-]{1,63}(?<!-)$")


def _valid_host(host: str) -> bool:
    try:
        ascii_host = host.encode("idna").decode("ascii")  # "müller.de" → "xn--mller-kva.de"
    except UnicodeError:
        return False
    labels = ascii_host.lower().split(".")
    return (
        len(labels) >= 2
        and all(_HOST_LABEL.match(label) for label in labels)
        and not labels[-1].isdigit()  # a top-level domain is never a number
    )


def url(value: object, *, kind: str | None = None, label: str = "link") -> str:
    """An optional http(s) URL with a real host name; "" if left empty.

    A missing scheme is added ("linkedin.com/in/x" → "https://linkedin.com/in/x"). Other
    schemes (javascript:, file:, ftp:) and user:password@ parts are rejected. With `kind`
    ("linkedin" or "github") the host must belong to that site.

    This checks the *format* only. Links the app downloads also pass the SSRF guard
    (app/guards/url_guard.py), which checks where the host really points.
    """
    text = clean_line(value)
    if not text:
        return ""
    if " " in text:
        raise ValueError(f"A {label} can't contain spaces.")
    if len(text) > URL_MAX_CHARS:
        raise ValueError(f"This {label} is too long (max {URL_MAX_CHARS:,} characters).")
    if "://" not in text and not text.lower().startswith(("javascript:", "data:", "mailto:")):
        text = "https://" + text
    try:
        parts = urlsplit(text)
        host = parts.hostname or ""
        parts.port  # noqa: B018 (raises ValueError for a port like ":abc")
    except ValueError as exc:
        raise ValueError(f"Please enter a valid {label} like https://example.com/page.") from exc
    if parts.scheme.lower() not in ("http", "https"):
        raise ValueError(f"Only http:// and https:// {label}s are allowed.")
    if parts.username or parts.password:
        raise ValueError(f"A {label} can't contain a user name or password.")
    if not _valid_host(host):
        raise ValueError(f"Please enter a valid {label} like https://example.com/page.")
    if kind is not None:
        domain = URL_DOMAINS[kind]
        if host.lower() != domain and not host.lower().endswith("." + domain):
            example = {"linkedin": "linkedin.com/in/your-name", "github": "github.com/you"}[kind]
            raise ValueError(f"This must be a {domain} link, like {example}.")
    return urlunsplit(parts._replace(scheme=parts.scheme.lower(), netloc=parts.netloc.lower()))


def display_url(stored: str) -> str:
    """A URL without "https://" and a trailing slash, for the cover letter header."""
    return re.sub(r"^https?://(www\.)?", "", stored).rstrip("/")


def looks_like_url(text: str) -> bool:
    """True if the input is a single http(s) URL rather than pasted job text."""
    stripped = clean_line(text)
    return stripped.lower().startswith(("http://", "https://")) and " " not in stripped


EMPTY_JOB_MESSAGE = "Please paste a job posting or a link to one."
SHORT_JOB_MESSAGE = (
    "This text is too short to be a job posting. Please paste the full posting (title, tasks "
    "and requirements), or a link to it."
)


def job_text(value: object) -> str:
    """Pasted job posting text: cleaned, JOB_TEXT_MIN_CHARS–JOB_TEXT_MAX_CHARS characters."""
    text = clean_multiline(value)
    if not text:
        raise ValueError(EMPTY_JOB_MESSAGE)
    if len(text) < JOB_TEXT_MIN_CHARS:
        raise ValueError(SHORT_JOB_MESSAGE)
    if len(text) > JOB_TEXT_MAX_CHARS:
        raise ValueError(
            f"This text is too long ({len(text):,} characters). Please paste only the job "
            f"posting (max {JOB_TEXT_MAX_CHARS:,} characters)."
        )
    return text


def job_input(value: object) -> str:
    """The "Add job" box: a link (format checked here, SSRF-checked when it's downloaded) or
    the posting text (must look like a posting: at least JOB_TEXT_MIN_CHARS characters)."""
    if looks_like_url(str(value or "")):
        return url(value, label="job link")
    return job_text(value)


# ---------------------------------------------------------------- places and lists

_PLACE_PUNCTUATION = set(" -,.'’()")  # "Frankfurt am Main", "St. Gallen", "Remote (EU)"


def place(value: object, *, label: str = "place") -> str:
    """An optional city, country or location: letters, spaces, hyphens, commas, dots,
    apostrophes and brackets; at most PLACE_MAX_CHARS. "" if left empty."""
    text = clean_line(value)
    if not text:
        return ""
    if len(text) > PLACE_MAX_CHARS:
        raise ValueError(f"This {label} is too long (max {PLACE_MAX_CHARS} characters).")
    if any(not (_is_letter(ch) or ch in _PLACE_PUNCTUATION) for ch in text) or not any(
        _is_letter(ch) for ch in text
    ):
        raise ValueError(
            f"A {label} can only contain letters, spaces, hyphens and commas, "
            "e.g. 'Berlin, Germany'."
        )
    return text


def split_list(text: object) -> list[str]:
    """Turn "Berlin, Remote" into ["Berlin", "Remote"]; a list is passed through."""
    if isinstance(text, list | tuple):
        return [str(item) for item in text]
    return [part for part in clean_line(text).split(",") if part.strip()]


def text_list(
    value: object,
    *,
    label: str,
    min_items: int = 0,
    max_items: int = LIST_MAX_ITEMS,
    min_chars: int = LIST_ITEM_MIN_CHARS,
    max_chars: int = LIST_ITEM_MAX_CHARS,
    item=None,
) -> list[str]:
    """A list of short entries (from a list or a comma-separated string).

    Each entry is cleaned, must have `min_chars`–`max_chars` characters (and pass `item`, a
    validator for one entry, if given); duplicates (ignoring case) are an error.
    """
    items = [clean_line(raw) for raw in split_list(value)]
    items = [i for i in items if i]
    if len(items) < min_items:
        if min_items == 1:
            raise ValueError(f"Please enter your {label} (separate several with commas).")
        raise ValueError(f"Please enter at least {min_items} {label}.")
    if len(items) > max_items:
        raise ValueError(f"Please enter at most {max_items} {label} (you entered {len(items)}).")
    seen: set[str] = set()
    for entry in items:
        if len(entry) < min_chars or len(entry) > max_chars:
            raise ValueError(
                f"Each of your {label} needs {min_chars}–{max_chars} characters ('{entry[:40]}')."
            )
        if item is not None:
            item(entry)
        if entry.casefold() in seen:
            raise ValueError(f"'{entry}' is listed twice in your {label}.")
        seen.add(entry.casefold())
    return items


# ---------------------------------------------------------------- salary

_NUMBER = re.compile(r"\d+(?:[.,\s]\d{3})*(?:[.,]\d+)?")


def _amounts(text: str) -> list[float]:
    """Numbers in "€50,000 – 60.000" style text, read as whole amounts."""
    values = []
    for raw in _NUMBER.findall(text):
        digits = re.sub(r"[.,\s](?=\d{3}\b)", "", raw.strip()).replace(",", ".")
        try:
            values.append(float(digits))
        except ValueError:
            continue
    return values


def salary_expectation(value: object) -> str:
    """An optional expected salary like "55,000 EUR", "50-60k €" or "4,500 € per month".

    Every amount (converted to yearly gross) must be between SALARY_MIN_YEARLY and
    SALARY_MAX_YEARLY, and the currency must be one of SALARY_CURRENCIES (EUR if none is
    named). Returns the cleaned text, because the salary tab reads ranges and periods from it.
    """
    text = clean_line(value)
    if not text:
        return ""
    lowered = text.lower()
    named = {code for code, marks in SALARY_CURRENCIES.items() if any(m in lowered for m in marks)}
    allowed_symbols = {m for marks in SALARY_CURRENCIES.values() for m in marks if len(m) == 1}
    other_symbols = {ch for ch in text if unicodedata.category(ch) == "Sc"} - allowed_symbols
    other_codes = set(re.findall(r"\b[A-Z]{3}\b", text)) - set(SALARY_CURRENCIES)
    if other_symbols or other_codes or len(named) > 1:
        raise ValueError(
            "Please use one currency from this list: " + ", ".join(SALARY_CURRENCIES) + "."
        )
    amounts = _amounts(text)
    if not amounts:
        raise ValueError("Please enter an amount, e.g. 55,000 EUR or 50–60k €.")
    if re.search(r"\d\s*k\b", lowered):  # "50-60k": the k belongs to both numbers
        amounts = [a * 1000 if a < 1000 else a for a in amounts]
    factor = 1
    if re.search(r"month|monat|/\s*m\b|p\.?\s?m\.?", lowered):
        factor = 12
    elif re.search(r"hour|stunde|/\s*h\b", lowered):
        factor = 40 * 52
    if any(not (SALARY_MIN_YEARLY <= a * factor <= SALARY_MAX_YEARLY) for a in amounts):
        raise ValueError(
            f"Please enter a yearly salary between {SALARY_MIN_YEARLY:,} and "
            f"{SALARY_MAX_YEARLY:,} (monthly amounts: add 'per month')."
        )
    return text


# ---------------------------------------------------------------- dates


def past_date_warning(day: date | None, today: date | None = None) -> str | None:
    """A gentle warning if a due date is already in the past (it's still saved)."""
    if day is not None and day < (today or date.today()):
        return f"The due date {day:%d %b %Y} is in the past. Saved anyway."
    return None
