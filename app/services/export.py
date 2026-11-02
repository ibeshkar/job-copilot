# What: turns a cover letter into a downloadable file: a PDF (reportlab) or a Word document
#       (python-docx) with the same layout, or, if that fails, a plain .txt file. Also the
#       plain-text version of all drafts for the "Copy as text" box.
# Why: the letter is what the user actually sends to an employer, so it must look the same
#      every time: `letter_blocks` decides the order and text of every line once, and both
#      exporters (and the UI preview) only style those blocks. The .txt fallback means a broken
#      export never leaves the user empty-handed (§10).
# Used by: app/services/applications.py (export_cover_letter), app/ui/pages/2_job_workspace.py,
#          tests/test_export.py.

import io
import re
import unicodedata
from dataclasses import dataclass, replace
from datetime import date
from functools import lru_cache
from typing import Literal
from xml.sax.saxutils import escape

from docx import Document
from docx.enum.text import WD_LINE_SPACING
from docx.shared import Cm, Pt, RGBColor
from reportlab.lib.colors import HexColor
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import cm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import Paragraph, SimpleDocTemplate

from app.agent.nodes.drafting import letter_subject
from app.agent.state import UNKNOWN_COMPANY, CoverLetter, Drafts
from app.config import PROJECT_ROOT
from app.memory.store import Profile
from app.utils.logging import get_logger, log_exception

logger = get_logger(__name__)

PDF_MIME = "application/pdf"
DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
TXT_MIME = "text/plain"

# Liberation Sans has the same letter widths as Arial, so the Word file (set in Arial) breaks
# lines like the PDF does. Embedding it in the PDF makes ä, ö, ü, ß and "–" render everywhere.
FONT_DIR = PROJECT_ROOT / "app" / "assets" / "fonts"
PDF_FONT, PDF_FONT_BOLD = "LiberationSans", "LiberationSans-Bold"
DOCX_FONT = "Arial"
GREY = "#666666"
MONTHS = [
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
]  # fmt: skip

BlockKind = Literal[
    "name", "contact", "recipient", "date", "subject", "salutation", "body", "closing",
    "signature",
]  # fmt: skip


@dataclass(frozen=True)
class LetterLayout:
    """Sizes in points (margins in cm), shared by the PDF and the Word export."""

    margin_cm: float = 2.5
    body_pt: float = 11.0
    name_pt: float = 18.0
    contact_pt: float = 9.5
    line_spacing: float = 1.3  # line height as a multiple of the font size
    paragraph_gap_pt: float = 8.0  # between body paragraphs
    block_gap_pt: float = 18.0  # between the sender header and the recipient block

    def size(self, kind: BlockKind) -> float:
        return {"name": self.name_pt, "contact": self.contact_pt}.get(kind, self.body_pt)

    def gap_before(self, kind: BlockKind) -> float:
        """Space above a block, so both exports leave the same gaps."""
        return {
            "name": 0.0,
            "contact": 2.0,
            "recipient": self.block_gap_pt,
            "date": self.paragraph_gap_pt,
            "subject": self.paragraph_gap_pt,
            "salutation": self.paragraph_gap_pt,
            "body": self.paragraph_gap_pt,
            "closing": self.paragraph_gap_pt,
            "signature": 0.0,  # right under "Kind regards,", as in the reference letter
        }[kind]


LAYOUT = LetterLayout()
# Tried in order until the letter fits on one page: less spacing first, then smaller text.
LAYOUTS = (
    LAYOUT,
    replace(LAYOUT, paragraph_gap_pt=6, block_gap_pt=14),
    replace(LAYOUT, paragraph_gap_pt=4, block_gap_pt=10, line_spacing=1.2),
    replace(LAYOUT, paragraph_gap_pt=4, block_gap_pt=10, line_spacing=1.2, body_pt=10.5),
    replace(LAYOUT, paragraph_gap_pt=3, block_gap_pt=8, line_spacing=1.15, body_pt=10),
)
BOLD: set[BlockKind] = {"name", "subject"}


@dataclass
class ExportFile:
    """A file ready for `st.download_button`. `fell_back` is True when it is the .txt file."""

    data: bytes
    file_name: str
    mime: str
    fell_back: bool = False


@dataclass(frozen=True)
class LetterBlock:
    """One paragraph of the finished letter. `text` may hold line breaks (recipient block)."""

    kind: BlockKind
    text: str


# ---------------------------------------------------------------- content (shared)


def format_date(day: date) -> str:
    """ "22 October 2026", independent of the computer's locale."""
    return f"{day.day} {MONTHS[day.month - 1]} {day.year}"


def fill_missing(letter: CoverLetter, job_title: str, company: str) -> CoverLetter:
    """Give letters saved as plain text (before structured letters) a company and subject."""
    company_name = letter.recipient_company.strip() or company
    if company_name == UNKNOWN_COMPANY:
        company_name = ""  # the posting named no company and the user hasn't set one yet
    return letter.model_copy(
        update={
            "recipient_company": company_name,
            "subject": letter.subject.strip() or letter_subject(job_title, company_name),
        }
    )


def letter_blocks(
    letter: CoverLetter, profile: Profile | None, today: date | None = None
) -> list[LetterBlock]:
    """Every line of the letter, in order: sender, recipient, date, subject, text, sign-off.

    The sender's name and contact details come from the profile, never from the LLM.
    """
    sender = (profile.name if profile else "") or letter.sender_name
    contact = [profile.email, profile.phone, profile.linkedin, profile.city] if profile else []
    # The contact person only appears in the salutation ("Dear Sylvie,"), as in the reference.
    recipient = [
        letter.recipient_company,
        letter.recipient_department,
        letter.recipient_city_country,
    ]
    blocks = [
        LetterBlock("name", sender),
        LetterBlock("contact", " · ".join(c.strip() for c in contact if c and c.strip())),
        LetterBlock("recipient", "\n".join(r.strip() for r in recipient if r and r.strip())),
        LetterBlock("date", format_date(today or date.today())),
        LetterBlock("subject", letter.subject),
        LetterBlock("salutation", letter.salutation),
        *(LetterBlock("body", p) for p in letter.body_paragraphs),
        LetterBlock("closing", letter.closing),
        # Old plain-text letters are signed inside the body and have no closing of their own.
        LetterBlock("signature", sender if letter.closing.strip() else ""),
    ]
    return [LetterBlock(b.kind, b.text.strip()) for b in blocks if b.text.strip()]


def cover_letter_to_text(
    letter: CoverLetter, profile: Profile | None, today: date | None = None
) -> str:
    """The letter as plain text, same order as the PDF (for the .txt fallback)."""
    return "\n\n".join(b.text for b in letter_blocks(letter, profile, today)) + "\n"


def drafts_to_text(drafts: Drafts, job_title: str, company: str) -> str:
    """The drafts as plain text (for the copy box)."""
    bullets = "\n".join(f"- {b}" for b in drafts.cv_bullets)
    at = f" at {company}" if company != UNKNOWN_COMPANY else ""
    return (
        f"Application: {job_title}{at}\n\n"
        f"CV BULLETS\n{bullets}\n\n"
        f"COVER LETTER\n{drafts.cover_letter.text}\n"
    )


def _ascii_word(text: str) -> str:
    text = text.translate(str.maketrans({"ä": "ae", "ö": "oe", "ü": "ue", "Ä": "Ae", "Ö": "Oe",
                                         "Ü": "Ue", "ß": "ss"}))  # fmt: skip
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return re.sub(r"[^A-Za-z0-9]", "", text)


def cover_letter_file_name(profile: Profile | None, company: str) -> str:
    """E.g. "CoverLetter_Alex_Novak_LumenHealth" (no extension, no spaces or special characters)."""
    names = profile.name.split() if profile else []
    parts = ["CoverLetter", *(_ascii_word(n) for n in names), _ascii_word(company)]
    return "_".join(p for p in parts if p)


# ---------------------------------------------------------------- PDF


@lru_cache
def _register_fonts() -> None:
    pdfmetrics.registerFont(TTFont(PDF_FONT, str(FONT_DIR / "LiberationSans-Regular.ttf")))
    pdfmetrics.registerFont(TTFont(PDF_FONT_BOLD, str(FONT_DIR / "LiberationSans-Bold.ttf")))


def _pdf_style(kind: BlockKind, layout: LetterLayout) -> ParagraphStyle:
    size = layout.size(kind)
    return ParagraphStyle(
        kind,
        fontName=PDF_FONT_BOLD if kind in BOLD else PDF_FONT,
        fontSize=size,
        leading=size * (1.2 if kind == "name" else layout.line_spacing),
        textColor=HexColor(GREY if kind == "contact" else "#000000"),
        spaceBefore=layout.gap_before(kind),
    )


def _build_pdf(blocks: list[LetterBlock], layout: LetterLayout, title: str) -> tuple[bytes, int]:
    """The PDF and its number of pages."""
    _register_fonts()
    buffer = io.BytesIO()
    margin = layout.margin_cm * cm
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=margin,
        rightMargin=margin,
        topMargin=margin,
        bottomMargin=margin,
        title=title,
        initialFontName=PDF_FONT,  # else reportlab also lists Helvetica in the PDF
        author=blocks[0].text if blocks and blocks[0].kind == "name" else "",
    )
    story = [
        Paragraph(escape(b.text).replace("\n", "<br/>"), _pdf_style(b.kind, layout)) for b in blocks
    ]
    doc.build(story)
    return buffer.getvalue(), doc.page


def _fit_one_page(blocks: list[LetterBlock], title: str) -> tuple[LetterLayout, bytes]:
    """The first layout in LAYOUTS whose PDF is one page (the last one if none fits)."""
    for layout in LAYOUTS:
        data, pages = _build_pdf(blocks, layout, title)
        if pages == 1:
            return layout, data
    return layout, data  # a very long letter: two pages at the tightest layout


def export_cover_letter_pdf(
    letter: CoverLetter, profile: Profile | None, today: date | None = None
) -> bytes:
    """The letter as an A4 PDF, on one page if it is of normal length.

    Raises:
        Exception: whatever reportlab raises; `export_cover_letter` catches it.
    """
    blocks = letter_blocks(letter, profile, today)
    return _fit_one_page(blocks, letter.subject)[1]


# ---------------------------------------------------------------- Word


def export_cover_letter_docx(
    letter: CoverLetter, profile: Profile | None, today: date | None = None
) -> bytes:
    """The letter as a Word document with the PDF's layout (same sizes, gaps and line height).

    Word can't tell us where its page ends, so it uses the layout that made the PDF fit.

    Raises:
        Exception: whatever python-docx or reportlab raises; `export_cover_letter` catches it.
    """
    blocks = letter_blocks(letter, profile, today)
    layout, _ = _fit_one_page(blocks, letter.subject)
    doc = Document()
    section = doc.sections[0]
    section.page_width, section.page_height = Cm(21.0), Cm(29.7)
    for side in ("left_margin", "right_margin", "top_margin", "bottom_margin"):
        setattr(section, side, Cm(layout.margin_cm))
    doc.styles["Normal"].font.name = DOCX_FONT
    doc.core_properties.title = letter.subject
    grey = RGBColor.from_string(GREY.lstrip("#"))

    for block in blocks:
        size = layout.size(block.kind)
        paragraph = doc.add_paragraph()
        fmt = paragraph.paragraph_format
        fmt.space_before = Pt(layout.gap_before(block.kind))
        fmt.space_after = Pt(0)
        fmt.line_spacing_rule = WD_LINE_SPACING.EXACTLY
        fmt.line_spacing = Pt(size * (1.2 if block.kind == "name" else layout.line_spacing))
        lines = block.text.split("\n")
        for i, line in enumerate(lines):
            run = paragraph.add_run(line)
            run.font.name = DOCX_FONT
            run.font.size = Pt(size)
            run.bold = block.kind in BOLD
            if block.kind == "contact":
                run.font.color.rgb = grey
            if i < len(lines) - 1:
                run.add_break()
    buffer = io.BytesIO()
    doc.save(buffer)
    return buffer.getvalue()


# ---------------------------------------------------------------- download


def export_cover_letter(
    letter: CoverLetter,
    profile: Profile | None,
    kind: Literal["pdf", "docx"],
    today: date | None = None,
) -> ExportFile:
    """Export as PDF or .docx; fall back to .txt (with `fell_back=True`) if that fails."""
    build, mime = {
        "pdf": (export_cover_letter_pdf, PDF_MIME),
        "docx": (export_cover_letter_docx, DOCX_MIME),
    }[kind]
    name = cover_letter_file_name(profile, letter.recipient_company)
    try:
        return ExportFile(build(letter, profile, today), f"{name}.{kind}", mime)
    except Exception:
        log_exception(logger, f"{kind.upper()} export failed, falling back to .txt", name=name)
        text = cover_letter_to_text(letter, profile, today)
        return ExportFile(text.encode("utf-8"), f"{name}.txt", TXT_MIME, fell_back=True)
