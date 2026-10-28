# What: turns drafts (CV bullets + cover letter) into a downloadable file: a Word document
#       (.docx) or, if that fails, a plain .txt file.
# Why: the user needs the text in a format they can attach or paste into an application form.
#      The .txt fallback means a broken DOCX export never leaves the user empty-handed (§10).
# Used by: app/services/applications.py (export_drafts), tests/test_export.py.

import io
import re
from dataclasses import dataclass

from docx import Document

from app.agent.state import Drafts
from app.utils.logging import get_logger, log_exception

logger = get_logger(__name__)

DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
TXT_MIME = "text/plain"


@dataclass
class ExportFile:
    """A file ready for `st.download_button`."""

    data: bytes
    file_name: str
    mime: str


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:40] or "job"


def drafts_to_text(drafts: Drafts, job_title: str, company: str) -> str:
    """The drafts as plain text (for the .txt fallback and the copy box)."""
    bullets = "\n".join(f"- {b}" for b in drafts.cv_bullets)
    return (
        f"Application: {job_title} at {company}\n\n"
        f"CV BULLETS\n{bullets}\n\n"
        f"COVER LETTER\n{drafts.cover_letter}\n"
    )


def drafts_to_docx(drafts: Drafts, job_title: str, company: str) -> bytes:
    """A Word document with a "CV bullets" list and the cover letter, one paragraph each.

    Raises:
        Exception: whatever python-docx raises; `export_drafts` catches it.
    """
    doc = Document()
    doc.add_heading(f"Application: {job_title} at {company}", level=1)
    doc.add_heading("CV bullets", level=2)
    for bullet in drafts.cv_bullets:
        doc.add_paragraph(bullet, style="List Bullet")
    doc.add_heading("Cover letter", level=2)
    for paragraph in re.split(r"\n\s*\n", drafts.cover_letter.strip()):
        doc.add_paragraph(paragraph.strip())
    buffer = io.BytesIO()
    doc.save(buffer)
    return buffer.getvalue()


def export_drafts(drafts: Drafts, job_title: str, company: str) -> ExportFile:
    """Export as .docx; fall back to .txt if building the Word file fails."""
    name = f"application-{_slug(company)}-{_slug(job_title)}"
    try:
        return ExportFile(drafts_to_docx(drafts, job_title, company), f"{name}.docx", DOCX_MIME)
    except Exception:
        log_exception(logger, "DOCX export failed, falling back to .txt", company=company)
        text = drafts_to_text(drafts, job_title, company)
        return ExportFile(text.encode("utf-8"), f"{name}.txt", TXT_MIME)
