# What: table-driven tests for every validator in app/validation/validators.py, the Pydantic
#       input models in app/validation/models.py and the output escaping in sanitize.py.
# Why: the rules decide what users can save, so each gets valid and invalid examples:
#      international names, German phone formats, empty / whitespace / very long input, emoji,
#      control characters and invisible Unicode.
# Used by: pytest.

from datetime import date

import pytest

from app import config as C
from app.utils.errors import InvalidInputError
from app.validation import validators as V
from app.validation.models import (
    ChatInput,
    ClaimEdit,
    CoverLetterInput,
    JobInput,
    NoteInput,
    PreferencesInput,
    ProfileInput,
    SettingsInput,
    TrackerUpdateInput,
    check_input,
    summary,
    validate_input,
)
from app.validation.sanitize import html_text, md

LONG = "a" * 10_000

# ---------------------------------------------------------------- normalising


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("  Anna   Maria  ", "Anna Maria"),
        ("tab\there", "tab here"),
        ("line\nbreak", "line break"),
        ("nul\x00byte", "nulbyte"),
        ("esc\x1b[31mred", "esc[31mred"),
        ("zero​width", "zerowidth"),
        ("bidi‮evil", "bidievil"),
        ("nbsp space", "nbsp space"),
        ("Zoë", "Zoë"),  # decomposed ë → one character (NFC)
        (None, ""),
        ("   ", ""),
    ],
)
def test_clean_line(raw, expected) -> None:
    assert V.clean_line(raw) == expected


def test_clean_multiline_keeps_paragraphs_but_not_runs_of_blank_lines() -> None:
    text = "  First  line \r\n\r\n\r\n\r\nSecond\x07 line  "

    assert V.clean_multiline(text) == "First line\n\nSecond line"


# ---------------------------------------------------------------- names


@pytest.mark.parametrize(
    "name",
    [
        "Esmaeil",
        "Zoë",
        "O'Brien",
        "O’Brien",
        "Jean-Luc Picard",
        "José Álvarez",
        "Jürgen Groß-Weber",
        "Nguyễn Văn An",
        "Łukasz Żółć",
        "李小龙",
        "Мария Иванова",
        "محمد",
        "J. R. R. Tolkien",
        "Al",
    ],
)
def test_valid_names(name) -> None:
    assert V.person_name(name) == name


@pytest.mark.parametrize(
    ("name", "message"),
    [
        ("", "Please enter your name"),
        ("   ", "Please enter your name"),
        ("A", "at least 2"),
        ("R2D2", "only contain letters"),
        ("<script>", "only contain letters"),
        ("{name}", "only contain letters"),
        ("alex@example.com", "only contain letters"),
        ("Alex 🚀", "only contain letters"),
        ("--", "at least 2 letters"),
        ("a" * (C.NAME_MAX_CHARS + 1), "too long"),
    ],
)
def test_invalid_names(name, message) -> None:
    with pytest.raises(ValueError, match=message):
        V.person_name(name)


def test_name_is_trimmed_and_spaces_collapsed() -> None:
    assert V.person_name("  Jean-Luc   Picard ") == "Jean-Luc Picard"


# ---------------------------------------------------------------- email


@pytest.mark.parametrize(
    ("raw", "stored"),
    [
        ("alex@example.com", "alex@example.com"),
        ("  Alex.Novak@Example.COM ", "alex.novak@example.com"),
        ("first+jobs@sub.example.de", "first+jobs@sub.example.de"),
        ("zoë@exämple.de", "zoë@exämple.de"),
    ],
)
def test_valid_emails_are_stored_in_lower_case(raw, stored) -> None:
    assert V.email(raw) == stored


@pytest.mark.parametrize(
    "raw",
    ["alex", "alex@", "@example.com", "alex@example", "alex@@example.com", "a b@example.com",
     "alex@example..com", "alex@exa_mple.com", LONG + "@example.com"],
)  # fmt: skip
def test_invalid_emails(raw) -> None:
    with pytest.raises(ValueError, match="valid email like name@example.com"):
        V.email(raw)


def test_empty_email_says_it_is_required() -> None:
    with pytest.raises(ValueError, match="Please enter your email"):
        V.email("   ")


# ---------------------------------------------------------------- phone


@pytest.mark.parametrize(
    ("raw", "e164"),
    [
        ("+49 151 12345678", "+4915112345678"),
        ("0049 151 12345678", "+4915112345678"),
        ("0151 12345678", "+4915112345678"),
        ("0151-123 456 78", "+4915112345678"),
        ("(030) 1234567", "+49301234567"),
        ("030/1234567", "+49301234567"),
        ("+43 660 1234567", "+436601234567"),  # Austria: a + number keeps its country
        ("+1 (415) 555-2671", "+14155552671"),
        ("", ""),  # optional
        ("   ", ""),
    ],
)
def test_phone_numbers_are_stored_in_e164(raw, e164) -> None:
    assert V.phone(raw) == e164


@pytest.mark.parametrize("raw", ["123", "0151", "abc", "+49 151 12345678 ext", "📞 0151 1234567",
                                 "+999 1234567", "1" * 40])  # fmt: skip
def test_invalid_phone_numbers(raw) -> None:
    with pytest.raises(ValueError, match="valid phone number"):
        V.phone(raw)


def test_phone_is_shown_formatted_and_old_values_unchanged() -> None:
    assert V.format_phone("+4915112345678") == "+49 1511 2345678"
    assert V.format_phone("call me maybe") == "call me maybe"  # old, unvalidated data
    assert V.format_phone("") == ""


# ---------------------------------------------------------------- URLs


@pytest.mark.parametrize(
    ("raw", "kind", "stored"),
    [
        ("linkedin.com/in/alex", "linkedin", "https://linkedin.com/in/alex"),
        ("https://www.linkedin.com/in/alex/", "linkedin", "https://www.linkedin.com/in/alex/"),
        ("HTTPS://DE.LinkedIn.com/in/alex", "linkedin", "https://de.linkedin.com/in/alex"),
        ("github.com/alex", "github", "https://github.com/alex"),
        ("http://alex.dev/portfolio", None, "http://alex.dev/portfolio"),
        ("https://müller.de/cv", None, "https://müller.de/cv"),
        ("", "linkedin", ""),  # optional
    ],
)
def test_valid_urls(raw, kind, stored) -> None:
    assert V.url(raw, kind=kind) == stored


@pytest.mark.parametrize(
    ("raw", "kind", "message"),
    [
        ("javascript:alert(1)", None, "Only http"),
        ("ftp://example.com/file", None, "Only http"),
        ("file:///etc/passwd", None, "Only http"),
        ("data:text/html,<b>x</b>", None, "Only http"),
        ("https://user:pw@example.com", None, "user name or password"),
        ("https://localhost/x", None, "valid link"),
        ("https://exa mple.com", None, "spaces"),
        ("https://-bad-.com", None, "valid link"),
        ("https://example.123", None, "valid link"),
        ("https://linkedin.com.evil.io/in/x", "linkedin", "linkedin.com link"),
        ("https://evil-linkedin.com/in/x", "linkedin", "linkedin.com link"),
        ("https://gitlab.com/alex", "github", "github.com link"),
        ("https://example.com/" + LONG, None, "too long"),
    ],
)
def test_invalid_urls(raw, kind, message) -> None:
    with pytest.raises(ValueError, match=message):
        V.url(raw, kind=kind)


def test_display_url_drops_scheme_and_www() -> None:
    assert V.display_url("https://www.linkedin.com/in/alex/") == "linkedin.com/in/alex"


# ---------------------------------------------------------------- places and lists


@pytest.mark.parametrize(
    "raw", ["Berlin", "Berlin, Germany", "Frankfurt am Main", "St. Gallen", "Remote (EU)",
            "Düsseldorf", "L'Aquila", "São Paulo", ""],
)  # fmt: skip
def test_valid_places(raw) -> None:
    assert V.place(raw) == raw


@pytest.mark.parametrize("raw", ["10115 Berlin", "Berlin; DROP TABLE", "<b>", "🏙️", "---",
                                 "a" * (C.PLACE_MAX_CHARS + 1)])  # fmt: skip
def test_invalid_places(raw) -> None:
    with pytest.raises(ValueError):
        V.place(raw)


@pytest.mark.parametrize(
    ("raw", "kwargs", "expected"),
    [
        ("AI Engineer, Data Analyst", {}, ["AI Engineer", "Data Analyst"]),
        ("  AI   Engineer ,, ", {}, ["AI Engineer"]),
        (["Berlin", " Remote "], {}, ["Berlin", "Remote"]),
        ("", {}, []),
    ],
)
def test_valid_lists(raw, kwargs, expected) -> None:
    assert V.text_list(raw, label="roles", **kwargs) == expected


@pytest.mark.parametrize(
    ("raw", "kwargs", "message"),
    [
        ("", {"min_items": 1}, "Please enter your roles"),
        (", , ", {"min_items": 1}, "Please enter your roles"),
        (",".join(f"Role {i}" for i in range(11)), {}, "at most 10"),
        ("AI Engineer, ai engineer", {}, "listed twice"),
        ("A, Data Analyst", {}, "2–80 characters"),
        ("x" * 81, {}, "2–80 characters"),
    ],
)
def test_invalid_lists(raw, kwargs, message) -> None:
    with pytest.raises(ValueError, match=message):
        V.text_list(raw, label="roles", **kwargs)


# ---------------------------------------------------------------- salary


@pytest.mark.parametrize(
    "raw",
    ["55,000 EUR", "€50,000 – €60,000", "50-60k €", "55.000 €", "4,500 € per month",
     "60000", "$120,000", "£45k", "CHF 110,000", "35 EUR per hour", ""],
)  # fmt: skip
def test_valid_salary_expectations(raw) -> None:
    assert V.salary_expectation(raw) == raw.strip()


@pytest.mark.parametrize(
    ("raw", "message"),
    [
        ("lots", "enter an amount"),
        ("5", "between 10,000 and 500,000"),
        ("9,999 EUR", "between 10,000 and 500,000"),
        ("1,000,000 EUR", "between 10,000 and 500,000"),
        ("60000 PLN", "one currency"),
        ("60000 ¥", "one currency"),
        ("50k € or $60k", "one currency"),
    ],
)
def test_invalid_salary_expectations(raw, message) -> None:
    with pytest.raises(ValueError, match=message):
        V.salary_expectation(raw)


# ---------------------------------------------------------------- free text and job input


@pytest.mark.parametrize(
    ("raw", "kwargs", "message"),
    [
        ("", {}, "Please enter the note"),
        ("   \n\t ", {}, "Please enter the note"),
        ("\x00\x01\x02", {}, "Please enter the note"),
        ("hi", {"min_chars": 5}, "too short"),
        (LONG, {}, "too long"),
    ],
)
def test_free_text_rejects_empty_short_and_long(raw, kwargs, message) -> None:
    with pytest.raises(ValueError, match=message):
        V.free_text(raw, label="the note", max_chars=500, **kwargs)


def test_free_text_keeps_emoji_and_strips_control_characters() -> None:
    assert V.free_text("Great call 🎉\x07", label="x", max_chars=50) == "Great call 🎉"
    assert V.free_text("   ", label="x", max_chars=50, required=False) == ""


def test_job_input_takes_a_link_or_a_long_enough_posting() -> None:
    assert JobInput(raw_input=" https://jobs.example.com/42 ").is_url
    posting = "Junior AI Engineer at Lumen. " * 10
    assert not JobInput(raw_input=posting).is_url

    for raw, message in [
        ("", "Please paste a job posting"),
        ("Junior AI Engineer", "too short to be a job posting"),
        ("https://localhost/job", "valid job link"),
        ("x" * (C.JOB_TEXT_MAX_CHARS + 1), "too long"),
    ]:
        _, errors = check_input(JobInput, {"raw_input": raw})
        assert message in errors["raw_input"], raw[:40]


# ---------------------------------------------------------------- dates


def test_past_due_date_warns_but_is_allowed() -> None:
    today = date(2026, 11, 3)
    note = NoteInput(next_action="Follow up", next_action_date=date(2026, 10, 1))

    assert "in the past" in note.warnings(today)[0]
    assert NoteInput(next_action_date=date(2026, 12, 1)).warnings(today) == []
    assert NoteInput().warnings(today) == []  # no date: nothing to say


def test_note_rejects_an_invalid_date() -> None:
    _, errors = check_input(NoteInput, {"next_action_date": "31.02.2026"})

    assert "next_action_date" in errors


# ---------------------------------------------------------------- models


VALID_PROFILE = {
    "name": "Zoë O'Brien",
    "email": "Zoe@Example.com",
    "target_roles": "AI Engineer, Data Analyst",
    "locations": "Berlin, Remote (EU)",
    "languages": "English C1, German A2",
    "salary_expectations": "55,000 EUR",
    "phone": "0151 12345678",
    "linkedin": "linkedin.com/in/zoe",
    "city": "Berlin, Germany",
}


def test_profile_input_normalises_every_field() -> None:
    profile = validate_input(ProfileInput, VALID_PROFILE)

    assert profile.email == "zoe@example.com"
    assert profile.phone == "+4915112345678"
    assert profile.linkedin == "https://linkedin.com/in/zoe"
    assert profile.target_roles == ["AI Engineer", "Data Analyst"]


def test_optional_profile_fields_left_empty_are_fine() -> None:
    minimal = {"name": "Al", "email": "al@example.com", "target_roles": "AI Engineer"}
    empty = dict.fromkeys(["phone", "linkedin", "city", "salary_expectations", "locations",
                           "languages", "work_authorisation"], "")  # fmt: skip

    profile, errors = check_input(ProfileInput, {**minimal, **empty})

    assert errors == {}
    assert profile.salary_expectations is None


def test_profile_errors_name_each_wrong_field() -> None:
    bad = {**VALID_PROFILE, "name": "R2D2", "email": "nope", "phone": "123", "linkedin": "x.com"}

    _, errors = check_input(ProfileInput, bad)

    assert set(errors) == {"name", "email", "phone", "linkedin"}
    assert errors["email"] == "Please enter a valid email like name@example.com."
    assert summary(errors, ProfileInput) == "Please fix 4 fields: Name, Email, Phone, LinkedIn."
    with pytest.raises(InvalidInputError) as caught:
        validate_input(ProfileInput, bad)
    assert caught.value.errors == errors


def test_preferences_default_language_when_empty() -> None:
    prefs = validate_input(PreferencesInput, {"letter_language": "  ", "tone": "formal"})

    assert prefs.letter_language == "Same as the job posting"
    assert check_input(PreferencesInput, {"tone": "rude"})[1] == {
        "tone": "Please pick one of the options."
    }


@pytest.mark.parametrize(
    ("text", "ok"),
    [("Led a 3-person team", True), ("Led", False), ("   ", False), ("x" * 501, False),
     ("Shipped 🚀 a RAG app", True)],
)  # fmt: skip
def test_claim_edit(text, ok) -> None:
    assert (check_input(ClaimEdit, {"text": text})[0] is not None) is ok


def test_cover_letter_needs_a_paragraph_and_checks_lengths() -> None:
    ok = {"body_paragraphs": ["Hello", "  ", "World"], "recipient_city_country": "Berlin"}
    assert validate_input(CoverLetterInput, ok).body_paragraphs == ["Hello", "World"]

    _, errors = check_input(
        CoverLetterInput,
        {"body_paragraphs": [" ", ""], "subject": "x" * 201, "recipient_city_country": "<b>"},
    )
    assert set(errors) == {"body_paragraphs", "subject", "recipient_city_country"}
    _, errors = check_input(CoverLetterInput, {"body_paragraphs": ["ok", LONG]})
    assert "Paragraph 2 is too long" in errors["body_paragraphs"]


@pytest.mark.parametrize(
    ("data", "field"),
    [
        ({"default_model": "openai/gpt-4o-mini", "temperature": 2.5, "max_tokens": 900},
         "temperature"),
        ({"default_model": "openai/gpt-4o-mini", "temperature": -0.1, "max_tokens": 900},
         "temperature"),
        ({"default_model": "openai/gpt-4o-mini", "temperature": 0.2, "max_tokens": 10},
         "max_tokens"),
        ({"default_model": "openai/gpt-4o", "temperature": 0.2, "max_tokens": 20_000},
         "max_tokens"),
        ({"default_model": "evil/model", "temperature": 0.2, "max_tokens": 900},
         "default_model"),
    ],
)  # fmt: skip
def test_settings_input_rejects(data, field) -> None:
    assert field in check_input(SettingsInput, data)[1]


def test_settings_input_accepts_the_model_limit() -> None:
    data = {"default_model": "openai/gpt-4.1-mini", "temperature": 2.0, "max_tokens": 32_768}

    assert validate_input(SettingsInput, data).max_tokens == 32_768


def test_chat_input_limits() -> None:
    assert check_input(ChatInput, {"text": "Hi there"})[0] is not None
    assert "text" in check_input(ChatInput, {"text": " "})[1]
    assert "too long" in check_input(ChatInput, {"text": "x" * (C.CHAT_MAX_CHARS + 1)})[1]["text"]


def test_tracker_update_needs_something_to_change() -> None:
    _, errors = check_input(TrackerUpdateInput, {"job_id": 1})
    assert "Nothing to change" in errors["__all__"]
    _, errors = check_input(TrackerUpdateInput, {"job_id": 1, "status": "hired"})
    assert "status" in errors


# ---------------------------------------------------------------- output escaping


@pytest.mark.parametrize(
    ("text", "escaped"),
    [
        ("![x](https://evil.example/?leak)", r"!\[x\](https\://evil.example/?leak)"),  # no image
        ("[Click me](https://evil.example)", r"\[Click me\](https\://evil.example)"),  # no link
        ("https://evil.example", r"https\://evil.example"),  # no autolink
        (":red[ALERT]", r"\:red\[ALERT\]"),  # no Streamlit colour directive
        ("$x^2$", r"\$x^2\$"),  # no LaTeX
        ("**bold** _it_", r"\*\*bold\*\* \_it\_"),
        ("<img src=x onerror=alert(1)>", r"\<img src=x onerror=alert(1)\>"),
        ("# Heading", r"\# Heading"),
        ("- item\n1. one", "\\- item\n1\\. one"),
    ],
)
def test_md_neutralises_markdown(text, escaped) -> None:
    assert md(text) == escaped


def test_md_leaves_plain_text_alone() -> None:
    assert md("Jean-Luc O'Brien, 12.5k, +49 151") == "Jean-Luc O'Brien, 12.5k, +49 151"
    assert md(None) == ""


def test_html_text_escapes_tags_and_quotes() -> None:
    assert html_text('<b onclick="x">Hi</b>\nthere') == (
        "&lt;b onclick=&quot;x&quot;&gt;Hi&lt;/b&gt;<br>there"
    )
