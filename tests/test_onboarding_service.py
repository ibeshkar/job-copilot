# What: tests for app/services/onboarding.py (what the wizard calls).
# Why: adding documents must record them in the database, report bad files without failing
#      the whole upload, and enforce the upload limits.
# Used by: pytest.

from app.services import onboarding


def test_load_demo_documents_records_them(app_db, vectorstore) -> None:
    report = onboarding.load_demo_documents(vectorstore=vectorstore)

    assert report.errors == {}
    assert len(report.added) == 4
    chunks, docs = onboarding.knowledge_base_summary()
    assert (chunks, docs) == (report.total_chunks, 4)


def test_bad_file_is_reported_but_others_are_added(app_db, vectorstore) -> None:
    report = onboarding.add_documents(
        [("cv.md", b"Python developer with SQL skills."), ("virus.exe", b"MZ")],
        vectorstore=vectorstore,
    )

    assert list(report.added) == ["cv.md"]
    assert "virus.exe" in report.errors
    assert onboarding.knowledge_base_summary() == (1, 1)


def test_too_many_files_are_rejected(app_db, vectorstore) -> None:
    files = [(f"doc{i}.md", b"text") for i in range(onboarding.MAX_FILES_PER_UPLOAD + 1)]

    report = onboarding.add_documents(files, vectorstore=vectorstore)

    assert report.added == {}
    assert report.errors
