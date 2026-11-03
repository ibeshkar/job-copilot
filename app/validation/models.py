# What: one Pydantic model per kind of user input (profile, preferences, job, notes, claim
#       edit, fact, style rule, cover letter, chat message, interview answer, settings, …),
#       built from the rules in validators.py, plus `check_input` / `validate_input`, which turn
#       a Pydantic ValidationError into {field: friendly message}.
# Why: pages validate a form with one call and show each message under its field; services and
#      storage call the same models again before saving (defence in depth), so data that a page
#      didn't check (the copilot, a test, a future API) can't get in either.
# Used by: app/ui/components.py and the pages (forms), app/memory/store.py, app/db/repo.py,
#          app/services/*.py, app/tools/tracker.py, app/guards/input_guard.py, tests.

from datetime import date
from typing import Annotated, ClassVar, Literal, TypeVar

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    ValidationError,
    ValidationInfo,
    field_validator,
    model_validator,
)

from app import config as C
from app.utils.errors import InvalidInputError
from app.validation import validators as V

M = TypeVar("M", bound=BaseModel)


def _rule(fn, **kwargs) -> BeforeValidator:
    """A validator from validators.py with fixed options, run before Pydantic's type check."""
    return BeforeValidator(lambda value: fn(value, **kwargs))


def _optional_line(label: str, max_chars: int = C.SHORT_TEXT_MAX_CHARS) -> BeforeValidator:
    return _rule(V.free_text, label=label, max_chars=max_chars, required=False, multiline=False)


class InputModel(BaseModel):
    """Base class: unknown keys are ignored; `LABELS` names fields in the error summary."""

    model_config = ConfigDict(extra="ignore")
    LABELS: ClassVar[dict[str, str]] = {}

    @classmethod
    def label(cls, field: str) -> str:
        return cls.LABELS.get(field, field.replace("_", " ").capitalize())


# ---------------------------------------------------------------- turning errors into messages


def _message(error: dict) -> str:
    if error["type"] == "value_error":
        return str(error["ctx"]["error"])  # our own message, without Pydantic's "Value error, "
    if error["type"] == "literal_error":
        return "Please pick one of the options."
    if error["type"] == "missing":
        return "This field is required."
    if error["type"].startswith(("greater_than", "less_than")):
        return f"Please enter a value {error['msg'].removeprefix('Input should be ')}."
    return error["msg"]


def error_messages(exc: ValidationError) -> dict[str, str]:
    """{field: message} with the first problem per field ("__all__" for whole-model checks)."""
    errors: dict[str, str] = {}
    for error in exc.errors():
        field = str(error["loc"][0]) if error["loc"] else "__all__"
        errors.setdefault(field, _message(error))
    return errors


def check_input(model: type[M], data: dict) -> tuple[M | None, dict[str, str]]:
    """Validate form data. Returns (the clean model, {}) or (None, {field: message})."""
    try:
        return model.model_validate(data), {}
    except ValidationError as exc:
        return None, error_messages(exc)


def validate_input(model: type[M], data: dict) -> M:
    """Validate data in a service or before saving.

    Raises:
        InvalidInputError: with {field: message}; its `user_message` lists the problems.
    """
    clean, errors = check_input(model, data)
    if clean is None:
        raise InvalidInputError(errors)
    return clean


def summary(errors: dict[str, str], model: type[InputModel]) -> str:
    """One line for the top of a form, e.g. "Please fix 2 fields: Email, Phone."."""
    names = [model.label(field) for field in errors if field != "__all__"]
    if len(errors) == 1:
        return f"Please fix this: {next(iter(errors.values()))}"
    return f"Please fix {len(errors)} fields: {', '.join(names)}."


# ---------------------------------------------------------------- profile and preferences

Seniority = Literal["intern", "junior", "mid", "senior", "lead"]


class ProfileInput(InputModel):
    """Onboarding step 1 and the Memory page's profile tab (stored as memory.store.Profile)."""

    LABELS: ClassVar[dict[str, str]] = {
        "name": "Name",
        "email": "Email",
        "target_roles": "Target roles",
        "seniority": "Seniority",
        "locations": "Locations",
        "work_authorisation": "Work authorisation",
        "languages": "Languages",
        "salary_expectations": "Expected salary",
        "phone": "Phone",
        "linkedin": "LinkedIn",
        "city": "City",
    }

    name: Annotated[str, BeforeValidator(V.person_name)]
    email: Annotated[str, BeforeValidator(V.email)]
    target_roles: Annotated[list[str], _rule(V.text_list, label="target roles", min_items=1)]
    seniority: Seniority = "junior"
    locations: Annotated[
        list[str],
        _rule(V.text_list, label="locations", item=lambda x: V.place(x, label="location")),
    ] = Field(default_factory=list)
    work_authorisation: Annotated[str, _optional_line("your work authorisation")] = ""
    languages: Annotated[list[str], _rule(V.text_list, label="languages")] = Field(
        default_factory=list
    )
    salary_expectations: Annotated[
        str | None, BeforeValidator(lambda v: V.salary_expectation(v) or None)
    ] = None
    phone: Annotated[str, BeforeValidator(V.phone)] = ""
    linkedin: Annotated[str, _rule(V.url, kind="linkedin", label="LinkedIn link")] = ""
    city: Annotated[str, _rule(V.place, label="city")] = ""


class PreferencesInput(InputModel):
    """Onboarding step 3 and the Memory page's preferences tab."""

    LABELS: ClassVar[dict[str, str]] = {
        "tone": "Tone",
        "letter_length": "Cover letter length",
        "letter_language": "Language of letters",
        "deal_breakers": "Deal-breakers",
    }

    tone: Literal["formal", "friendly", "concise"] = "friendly"
    letter_length: Literal["short", "medium", "long"] = "medium"
    letter_language: Annotated[
        str,
        BeforeValidator(
            lambda v: (
                V.free_text(
                    v,
                    label="the language of your letters",
                    min_chars=2,
                    max_chars=C.LETTER_LANGUAGE_MAX_CHARS,
                    required=False,
                    multiline=False,
                )
                or "Same as the job posting"
            )
        ),
    ] = "Same as the job posting"
    deal_breakers: Annotated[list[str], _rule(V.text_list, label="deal-breakers")] = Field(
        default_factory=list
    )


# ---------------------------------------------------------------- jobs


class JobInput(InputModel):
    """The "➕ Add job" dialog: a link to a posting, or the pasted posting text."""

    LABELS: ClassVar[dict[str, str]] = {"raw_input": "Job posting"}

    raw_input: Annotated[str, BeforeValidator(V.job_input)]

    @property
    def is_url(self) -> bool:
        return V.looks_like_url(self.raw_input)


class RenameJobInput(InputModel):
    """The ✏️ popover next to the job title. Empty keeps the current value."""

    LABELS: ClassVar[dict[str, str]] = {"title": "Job title", "company": "Company"}

    title: Annotated[str, _optional_line("the job title", C.JOB_TITLE_MAX_CHARS)] = ""
    company: Annotated[str, _optional_line("the company name", C.COMPANY_MAX_CHARS)] = ""


class NoteInput(InputModel):
    """The Notes tab: notes, the next action and its due date."""

    LABELS: ClassVar[dict[str, str]] = {
        "notes": "Notes",
        "next_action": "Next action",
        "next_action_date": "Due",
    }

    notes: Annotated[
        str, _rule(V.free_text, label="notes", max_chars=C.NOTES_MAX_CHARS, required=False)
    ] = ""
    next_action: Annotated[str, _optional_line("the next action")] = ""
    next_action_date: date | None = None

    def warnings(self, today: date | None = None) -> list[str]:
        """Things worth saying that don't block saving (a due date in the past)."""
        warning = V.past_date_warning(self.next_action_date, today)
        return [warning] if warning else []


class TrackerUpdateInput(InputModel):
    """A tracker change the copilot wants to make (`update_tracker` tool)."""

    job_id: int = Field(ge=1)
    status: Literal["saved", "applied", "interview", "offer", "rejected"] | None = None
    note: Annotated[str, _optional_line("the note", C.TRACKER_NOTE_MAX_CHARS)] = ""
    next_action: Annotated[str, _optional_line("the next action")] = ""

    @model_validator(mode="after")
    def _something_to_change(self) -> "TrackerUpdateInput":
        if not (self.status or self.note or self.next_action):
            raise ValueError("Nothing to change: give a status, note or next action.")
        return self


# ---------------------------------------------------------------- fit review, claims, memory


class ContextInput(InputModel):
    """ "Add context and re-check" on the Fit tab."""

    text: Annotated[
        str,
        _rule(
            V.free_text,
            label="your context",
            min_chars=C.CONTEXT_MIN_CHARS,
            max_chars=C.CONTEXT_MAX_CHARS,
        ),
    ]


class ClaimEdit(InputModel):
    """The new wording of a claim at the claim check."""

    text: Annotated[
        str,
        _rule(
            V.free_text,
            label="the claim",
            min_chars=C.CLAIM_MIN_CHARS,
            max_chars=C.CLAIM_MAX_CHARS,
            multiline=False,
        ),
    ]


class FactInput(InputModel):
    """A confirmed fact (from a claim check, or reworded on the Memory page)."""

    text: Annotated[
        str,
        _rule(
            V.free_text,
            label="the fact",
            min_chars=C.FACT_MIN_CHARS,
            max_chars=C.FACT_MAX_CHARS,
            multiline=False,
        ),
    ]


class StyleRuleInput(InputModel):
    """A writing style rule (typed on the Memory page, or learned from edits)."""

    rule: Annotated[
        str,
        _rule(
            V.free_text,
            label="the rule",
            min_chars=C.STYLE_RULE_MIN_CHARS,
            max_chars=C.STYLE_RULE_MAX_CHARS,
            multiline=False,
        ),
    ]


# ---------------------------------------------------------------- drafts


def _paragraphs(value: object) -> list[str]:
    raw = value if isinstance(value, list | tuple) else [value]
    paragraphs = [V.clean_multiline(p) for p in raw]
    paragraphs = [p for p in paragraphs if p]
    if not paragraphs:
        raise ValueError("The letter needs at least one paragraph.")
    if len(paragraphs) > C.LETTER_MAX_PARAGRAPHS:
        raise ValueError(
            f"Please keep the letter to {C.LETTER_MAX_PARAGRAPHS} paragraphs or fewer."
        )
    for n, paragraph in enumerate(paragraphs, start=1):
        if len(paragraph) > C.PARAGRAPH_MAX_CHARS:
            raise ValueError(
                f"Paragraph {n} is too long ({len(paragraph):,} characters, "
                f"max {C.PARAGRAPH_MAX_CHARS:,})."
            )
    return paragraphs


class CoverLetterInput(InputModel):
    """The cover letter editor. Empty company / subject / salutation / closing are filled in
    from the job when the letter is exported (app/services/export.py `fill_missing`)."""

    LABELS: ClassVar[dict[str, str]] = {
        "cv_bullets": "CV bullets",  # shown in the same summary (applications.validate_drafts)
        "recipient_company": "Company",
        "recipient_department": "Department",
        "recipient_city_country": "City, country",
        "subject": "Subject",
        "salutation": "Salutation",
        "body_paragraphs": "Paragraphs",
        "closing": "Closing",
    }

    recipient_company: Annotated[str, _optional_line("the company", C.COMPANY_MAX_CHARS)] = ""
    recipient_department: Annotated[str, _optional_line("the department")] = ""
    recipient_city_country: Annotated[str, _rule(V.place, label="city and country")] = ""
    subject: Annotated[str, _optional_line("the subject")] = ""
    salutation: Annotated[str, _optional_line("the salutation")] = ""
    body_paragraphs: Annotated[list[str], BeforeValidator(_paragraphs)]
    closing: Annotated[str, _optional_line("the closing", C.CLOSING_MAX_CHARS)] = ""


class CvBulletsInput(InputModel):
    """The CV bullets editor (one bullet per line)."""

    LABELS: ClassVar[dict[str, str]] = {"cv_bullets": "CV bullets"}

    cv_bullets: Annotated[
        list[str],
        _rule(
            V.text_list,
            label="CV bullets",
            min_items=1,
            max_items=C.CV_MAX_BULLETS,
            min_chars=2,
            max_chars=C.CV_BULLET_MAX_CHARS,
        ),
    ]


class FeedbackCommentInput(InputModel):
    """The optional comment after a 👎."""

    comment: Annotated[str, _optional_line("your comment", C.FEEDBACK_COMMENT_MAX_CHARS)] = ""


class InstructionInput(InputModel):
    """ "Regenerate a section: what should change?" (optional)."""

    instruction: Annotated[str, _optional_line("the instruction", C.INSTRUCTION_MAX_CHARS)] = ""


# ---------------------------------------------------------------- research, chat, interview


class CompanyFocusInput(InputModel):
    """The optional focus of company research."""

    focus: Annotated[str, _optional_line("the focus", C.FOCUS_MAX_CHARS)] = ""


class ChatInput(InputModel):
    """A copilot chat message or a help-bot question."""

    text: Annotated[
        str,
        _rule(
            V.free_text,
            label="a question",
            min_chars=C.CHAT_MIN_CHARS,
            max_chars=C.CHAT_MAX_CHARS,
        ),
    ]


class InterviewAnswerInput(InputModel):
    """An answer in the mock interview."""

    text: Annotated[
        str,
        _rule(
            V.free_text,
            label="your answer",
            min_chars=C.ANSWER_MIN_CHARS,
            max_chars=C.ANSWER_MAX_CHARS,
        ),
    ]


# ---------------------------------------------------------------- developer settings


def _password(value: object) -> str:
    # No trimming: spaces may be part of a password. Only "nothing typed" is an error.
    text = "" if value is None else str(value)
    if not text:
        raise ValueError("Please enter the password.")
    if len(text) > C.PASSWORD_MAX_CHARS:
        raise ValueError(f"That's too long for a password (max {C.PASSWORD_MAX_CHARS}).")
    return text


class PasswordInput(InputModel):
    """The developer settings password field (checked against APP_DEV_PASSWORD afterwards)."""

    password: Annotated[str, BeforeValidator(_password)]


def _chat_model(value: object) -> str:
    model = V.clean_line(value)
    if model not in C.CHAT_MODELS:
        raise ValueError("Please pick a model from the list: " + ", ".join(C.CHAT_MODELS) + ".")
    return model


class SettingsInput(InputModel):
    """Model, temperature and max tokens on the developer settings page."""

    LABELS: ClassVar[dict[str, str]] = {
        "default_model": "Model",
        "temperature": "Temperature",
        "max_tokens": "Max tokens",
    }

    default_model: Annotated[str, BeforeValidator(_chat_model)]
    temperature: float = Field(ge=C.TEMPERATURE_MIN, le=C.TEMPERATURE_MAX)
    max_tokens: int = Field(ge=C.MAX_TOKENS_MIN)

    @field_validator("max_tokens")
    @classmethod
    def _within_model_limit(cls, value: int, info: ValidationInfo) -> int:
        model = info.data.get("default_model")  # missing if the model itself was invalid
        limit = C.MODEL_MAX_OUTPUT_TOKENS.get(model or "")
        if limit is not None and value > limit:
            raise ValueError(f"{model} can return at most {limit:,} tokens per answer.")
        return value
