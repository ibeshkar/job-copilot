# What: unit tests for the database functions in app/db/repo.py.
# Why: the tracker board and the copilot's tracker tool rely on these; they must work
#      without any network or LLM.
# Used by: pytest.

import json

import pytest
from sqlmodel import Session

from app.db import repo
from app.utils.errors import NotFoundError


def test_create_job_assigns_id_and_thread_id(session: Session) -> None:
    job = repo.create_job(session, title="Junior AI Engineer", company="Lumen Health")

    assert job.id is not None
    assert job.thread_id == f"job-{job.id}"
    assert job.status == "saved"


def test_get_job_missing_raises_not_found(session: Session) -> None:
    with pytest.raises(NotFoundError):
        repo.get_job(session, 999)


def test_list_jobs_filters_by_status_and_sorts_newest_first(session: Session) -> None:
    first = repo.create_job(session, title="A", company="X")
    second = repo.create_job(session, title="B", company="Y")
    repo.update_job(session, first.id, status="applied")

    assert [j.id for j in repo.list_jobs(session)] == [second.id, first.id]
    assert [j.id for j in repo.list_jobs(session, status="applied")] == [first.id]
    assert repo.list_jobs(session, status="offer") == []


def test_update_job_changes_fields_and_timestamp(session: Session) -> None:
    job = repo.create_job(session, title="A", company="X")
    before = job.updated_at

    updated = repo.update_job(session, job.id, status="interview", notes="Call on Monday")

    assert updated.status == "interview"
    assert updated.notes == "Call on Monday"
    assert updated.updated_at >= before


def test_update_job_rejects_bad_status_and_unknown_fields(session: Session) -> None:
    job = repo.create_job(session, title="A", company="X")

    with pytest.raises(ValueError, match="Unknown status"):
        repo.update_job(session, job.id, status="hired")
    with pytest.raises(ValueError, match="Cannot update"):
        repo.update_job(session, job.id, thread_id="hacked")


def test_delete_job_removes_its_drafts(session: Session) -> None:
    job = repo.create_job(session, title="A", company="X")
    repo.add_draft(session, job.id, ["Built X"], "Dear team, ...")

    repo.delete_job(session, job.id)

    assert repo.list_jobs(session) == []
    assert repo.list_drafts(session, job.id) == []


def test_drafts_are_versioned_per_job(session: Session) -> None:
    job_a = repo.create_job(session, title="A", company="X")
    job_b = repo.create_job(session, title="B", company="Y")

    v1 = repo.add_draft(session, job_a.id, ["Bullet 1"], "Letter v1")
    v2 = repo.add_draft(session, job_a.id, ["Bullet 1", "Bullet 2"], "Letter v2", True)
    other = repo.add_draft(session, job_b.id, [], "Other letter")

    assert (v1.version, v2.version, other.version) == (1, 2, 1)
    assert json.loads(v2.cv_bullets_json) == ["Bullet 1", "Bullet 2"]
    assert repo.latest_draft(session, job_a.id).cover_letter == "Letter v2"
    assert [d.version for d in repo.list_drafts(session, job_a.id)] == [1, 2]


def test_add_draft_for_missing_job_raises(session: Session) -> None:
    with pytest.raises(NotFoundError):
        repo.add_draft(session, 42, [], "")


def test_upsert_document_replaces_same_filename(session: Session) -> None:
    repo.upsert_document(session, "cv.md", "cv", chunk_count=5)
    repo.upsert_document(session, "cv.md", "cv", chunk_count=8)
    repo.upsert_document(session, "brag.md", "brag_doc", chunk_count=3)

    docs = repo.list_documents(session)
    assert [(d.filename, d.chunk_count) for d in docs] == [("brag.md", 3), ("cv.md", 8)]

    repo.delete_document(session, "cv.md")
    assert [d.filename for d in repo.list_documents(session)] == ["brag.md"]
    with pytest.raises(NotFoundError):
        repo.delete_document(session, "cv.md")


def test_usage_is_summed_per_action(session: Session) -> None:
    repo.log_usage(session, "draft", "gpt-4o-mini", 1000, 200, 0.0003)
    repo.log_usage(session, "draft", "gpt-4o-mini", 500, 100, 0.0002)
    repo.log_usage(session, "chat", "gpt-4o-mini", 100, 50, 0.00005)

    totals = repo.usage_by_action(session)

    assert totals["draft"]["tokens"] == 1800
    assert totals["draft"]["cost_usd"] == pytest.approx(0.0005)
    assert totals["chat"]["tokens"] == 150


def test_feedback_validates_target_and_rating(session: Session) -> None:
    repo.add_feedback(session, "letter", -1, "Too formal")

    assert [f.comment for f in repo.list_feedback(session, target="letter")] == ["Too formal"]
    with pytest.raises(ValueError):
        repo.add_feedback(session, "salary", 1)
    with pytest.raises(ValueError):
        repo.add_feedback(session, "letter", 5)


def test_add_interview_session(session: Session) -> None:
    job = repo.create_job(session, title="A", company="X")

    row = repo.add_interview_session(session, job.id, [{"q": "Why us?", "score": 4}], 4.0)

    assert row.id is not None
    assert json.loads(row.qa_json)[0]["score"] == 4
