# What: tests that the service and storage layers validate again, whatever page (or no page)
#       the data comes from: memory store, app database, tracker tool (the copilot's write
#       path), application services, developer settings, guards and document uploads. Also: a
#       saved profile that breaks today's rules still loads and reports what to fix.
# Why: defence in depth (docs/DECISIONS.md D91). The UI checks first for good messages, but
#      data must not get in through the copilot, a test, a future API or a skipped form check.
# Used by: pytest.

from datetime import date, timedelta

import pytest
from langgraph.store.memory import InMemoryStore

from app.agent.state import CoverLetter, Drafts, StyleRule
from app.config import NOTES_MAX_CHARS
from app.db import repo
from app.guards.input_guard import check_chat_message, check_interview_answer, check_job_text
from app.memory import store as memory
from app.memory.store import Fact, Preferences, Profile
from app.rag.ingest import load_text
from app.services import applications, dev_settings, export, onboarding
from app.tools.tracker import apply_update
from app.utils.errors import DocumentError, GuardBlockedError, InvalidInputError

GOOD = {"name": "Zoë O'Brien", "email": "Zoe@Example.com", "target_roles": ["AI Engineer"]}


@pytest.fixture
def mem() -> InMemoryStore:
    return InMemoryStore()


# ---------------------------------------------------------------- memory store


def test_store_normalises_the_profile_it_saves(mem) -> None:
    saved = memory.save_profile(Profile(**GOOD, phone="0151 12345678", city=" Berlin "), mem)

    loaded = memory.get_profile(mem)
    assert loaded == saved
    assert (loaded.email, loaded.phone, loaded.city) == (
        "zoe@example.com",
        "+4915112345678",
        "Berlin",
    )


@pytest.mark.parametrize(
    ("change", "field"),
    [
        ({"name": "R2D2"}, "name"),
        ({"email": "not-an-email"}, "email"),
        ({"email": ""}, "email"),
        ({"target_roles": []}, "target_roles"),
        ({"phone": "12"}, "phone"),
        ({"linkedin": "https://evil.example/in/x"}, "linkedin"),
        ({"salary_expectations": "5 EUR"}, "salary_expectations"),
        ({"locations": ["Berlin", "berlin"]}, "locations"),
    ],
)
def test_store_refuses_an_invalid_profile(mem, change, field) -> None:
    with pytest.raises(InvalidInputError) as caught:
        memory.save_profile(Profile(**{**GOOD, **change}), mem)

    assert field in caught.value.errors
    assert memory.get_profile(mem) is None  # nothing was saved


def test_old_invalid_profile_still_loads_and_lists_its_problems(mem) -> None:
    # Saved before the rules existed: written straight into the store, bypassing save_profile.
    mem.put(("local", "profile"), "main", {"name": "Bob1", "email": "bob@", "phone": "12"})

    profile = memory.get_profile(mem)

    assert profile.name == "Bob1"  # no crash
    assert set(memory.profile_problems(profile)) == {"name", "email", "phone", "target_roles"}
    assert memory.profile_problems(memory.save_profile(Profile(**GOOD), mem)) == {}
    assert memory.profile_problems(None) == {}


def test_store_refuses_invalid_preferences(mem) -> None:
    with pytest.raises(InvalidInputError):
        memory.save_preferences(Preferences(deal_breakers=["x" * 200]), mem)
    assert memory.get_preferences(mem) == Preferences()


def test_store_checks_facts_and_style_rules(mem) -> None:
    key = memory.add_fact(Fact(text="  Led a   3-person team "), mem)
    assert memory.list_facts(mem)[key].text == "Led a 3-person team"

    for bad in ("", "   ", "abc", "x" * 501):
        with pytest.raises(InvalidInputError):
            memory.add_fact(Fact(text=bad), mem)
        with pytest.raises(InvalidInputError):
            memory.update_fact(key, bad, mem)
        with pytest.raises(InvalidInputError):
            memory.add_style_rule(StyleRule(rule=bad, source="explicit"), mem)
    assert memory.list_facts(mem)[key].text == "Led a 3-person team"  # unchanged


# ---------------------------------------------------------------- app database


def test_repo_cleans_and_checks_job_fields(session) -> None:
    job = repo.create_job(session, title="  AI\x00 Engineer ", company="Acme")
    assert job.title == "AI Engineer"

    for fields in (
        {"title": "   "},
        {"company": "x" * 201},
        {"notes": "x" * (NOTES_MAX_CHARS + 1)},
        {"next_action": "x" * 201},
        {"url": "javascript:alert(1)"},
    ):
        with pytest.raises(InvalidInputError):
            repo.update_job(session, job.id, **fields)
    with pytest.raises(InvalidInputError):
        repo.create_job(session, title="", company="Acme")
    with pytest.raises(InvalidInputError):
        repo.add_feedback(session, "letter", -1, "x" * 301, job_id=job.id)


# ---------------------------------------------------------------- copilot write path


def _job() -> int:
    with repo.session_scope() as session:
        return repo.create_job(session, title="AI Engineer", company="Acme").id


def test_tracker_tool_uses_the_same_rules_as_the_notes_tab(app_db) -> None:
    job_id = _job()

    with pytest.raises(InvalidInputError):
        apply_update(job_id, note="x" * 501)
    with pytest.raises(InvalidInputError):
        apply_update(job_id, status="hired")
    with pytest.raises(GuardBlockedError):
        apply_update(job_id, note="   ")  # whitespace only = nothing to change
    assert "Done" in apply_update(job_id, note="Called\x07 the  recruiter")
    with repo.session_scope() as session:
        assert repo.get_job(session, job_id).notes.endswith("Called the recruiter")


# ---------------------------------------------------------------- application services


def test_add_job_rejects_bad_input_before_creating_a_card(app_db) -> None:
    for raw in ("", "   ", "Junior AI Engineer", "https://localhost/jobs/1"):
        result = applications.add_job(raw)
        assert not result.ok and result.message and result.job_id is None
    assert not any(applications.list_board().values())


def test_save_notes_validates_and_warns_about_a_past_date(app_db) -> None:
    job_id = _job()

    warnings = applications.save_notes(
        job_id, " Notes ", "Follow up", date.today() - timedelta(days=3)
    )

    assert "in the past" in warnings[0]
    assert applications.save_notes(job_id, "", "", None) == []  # empty optional fields are fine
    with pytest.raises(InvalidInputError):
        applications.save_notes(job_id, "x" * (NOTES_MAX_CHARS + 1), "", None)


def test_rename_job_rejects_a_too_long_name(app_db) -> None:
    job_id = _job()

    with pytest.raises(InvalidInputError):
        applications.rename_job(job_id, "x" * 201, "")
    assert applications.rename_job(job_id, "  Senior AI Engineer ", "")


def _drafts(bullets: list[str], paragraphs: list[str]) -> Drafts:
    letter = CoverLetter(
        recipient_company="Acme",
        recipient_department=None,
        recipient_city_country=None,
        recipient_name=None,
        subject="Application",
        salutation="Dear Hiring Team,",
        body_paragraphs=paragraphs,
        closing="Kind regards,",
        sender_name="Zoë",
    )
    return Drafts(cv_bullets=bullets, cover_letter=letter, used_claim_ids=[])


def test_validate_drafts_cleans_and_rejects() -> None:
    clean = applications.validate_drafts(_drafts(["  Built a RAG app "], [" Hello\x00 "]))
    assert clean.cv_bullets == ["Built a RAG app"]
    assert clean.cover_letter.body_paragraphs == ["Hello"]

    with pytest.raises(InvalidInputError) as caught:
        applications.validate_drafts(_drafts([], ["   "]))
    assert set(caught.value.errors) == {"cv_bullets", "body_paragraphs"}


def test_save_edited_drafts_refuses_invalid_drafts(app_db) -> None:
    job_id = _job()

    with pytest.raises(InvalidInputError):
        applications.save_edited_drafts(job_id, _drafts(["ok bullet"], ["x" * 2001]))
    assert applications.list_draft_versions(job_id) == []


# ---------------------------------------------------------------- developer settings


@pytest.mark.parametrize(
    ("model", "temperature", "max_tokens"),
    [("evil/model", 0.2, 900), ("openai/gpt-4o-mini", 2.1, 900), ("openai/gpt-4o", 0.2, 99_999)],
)
def test_dev_settings_refuse_values_outside_the_rules(app_db, model, temperature, max_tokens):
    with pytest.raises(InvalidInputError):
        dev_settings.save_llm_overrides(model, temperature, max_tokens)
    assert not dev_settings.has_overrides()


def test_empty_password_never_unlocks(app_db) -> None:
    assert not dev_settings.check_password("")


# ---------------------------------------------------------------- guards (graph input)


def test_guards_strip_control_characters_before_checking() -> None:
    assert not check_chat_message("\x00\x01  ").allowed
    assert not check_interview_answer("​​").allowed
    assert not check_interview_answer("Yes").allowed  # too short to be an answer
    assert not check_job_text("\x00" * 500).allowed


# ---------------------------------------------------------------- uploads


@pytest.mark.parametrize(
    ("filename", "data", "message"),
    [
        ("cv.pdf", b"", "is empty"),
        ("cv.pdf", b"MZ\x90\x00 this is an exe", "isn't really a PDF"),
        ("cv.docx", b"%PDF-1.4 not a zip", "isn't really a DOCX"),
        ("cv.txt", b"\x00\x01\x02binary", "doesn't look like a text file"),
        ("cv.md", b"   \n\n  ", "no readable text"),
        ("cv.md", b"Hi", "no readable text"),
    ],
)
def test_upload_must_be_a_real_non_empty_document(filename, data, message) -> None:
    with pytest.raises(DocumentError) as caught:
        load_text(filename, data)
    assert message in caught.value.user_message


def test_no_files_is_an_error(app_db) -> None:
    assert onboarding.add_documents([]).errors


# ---------------------------------------------------------------- display


def test_letter_header_shows_the_phone_formatted() -> None:
    profile = Profile(**GOOD, phone="+4915112345678", linkedin="https://www.linkedin.com/in/zoe")

    contact = export.letter_blocks(_drafts(["b"], ["p"]).cover_letter, profile)[1].text

    assert "+49 1511 2345678" in contact
    assert "linkedin.com/in/zoe" in contact and "https://" not in contact
