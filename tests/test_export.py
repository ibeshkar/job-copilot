# What: tests for app/services/export.py (DOCX export with a .txt fallback).
# Why: the exported file is what the user actually sends to an employer, and a failed export
#      must still give them their text (PROJECT_PLAN.md §10).
# Used by: pytest.

import io

from docx import Document

from app.agent.state import Drafts
from app.services import export

DRAFTS = Drafts(
    cv_bullets=["Built a RAG chatbot with LangChain.", "Wrote Python ETL jobs."],
    cover_letter="Dear team,\n\nI'd love to join Lumen Health.\n\nBest regards, Alex",
    used_claim_ids=["C1"],
)


def test_docx_has_bullets_and_one_paragraph_per_letter_paragraph() -> None:
    file = export.export_drafts(DRAFTS, "Junior AI Engineer", "Lumen Health")

    assert file.file_name == "application-lumen-health-junior-ai-engineer.docx"
    assert file.mime == export.DOCX_MIME
    paragraphs = [p for p in Document(io.BytesIO(file.data)).paragraphs]
    texts = [p.text for p in paragraphs]
    assert "Built a RAG chatbot with LangChain." in texts
    bullet = next(p for p in paragraphs if p.text == "Wrote Python ETL jobs.")
    assert bullet.style.name == "List Bullet"
    assert texts[-3:] == ["Dear team,", "I'd love to join Lumen Health.", "Best regards, Alex"]


def test_failed_docx_export_falls_back_to_text(monkeypatch) -> None:
    def broken(*args):
        raise RuntimeError("docx is broken")

    monkeypatch.setattr(export, "drafts_to_docx", broken)

    file = export.export_drafts(DRAFTS, "Junior AI Engineer", "Lumen Health")

    assert file.file_name.endswith(".txt") and file.mime == "text/plain"
    text = file.data.decode()
    assert "- Wrote Python ETL jobs." in text
    assert "I'd love to join Lumen Health." in text
