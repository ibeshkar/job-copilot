# What: tests for app/services/export.py (cover letter as PDF and DOCX with a .txt fallback)
#       and for the letter clean-up in app/agent/nodes/drafting.py (finalise_letter).
# Why: the exported file is what the user actually sends to an employer, so its layout must be
#      reliable, umlauts must work, and a failed export must still give them their text
#      (PROJECT_PLAN.md §10).
# Used by: pytest.

import io
from datetime import date

import pytest
from docx import Document
from docx.shared import Pt
from pypdf import PdfReader

from app.agent.nodes.drafting import FALLBACK_SALUTATION, FALLBACK_SALUTATION_DE, finalise_letter
from app.agent.state import UNKNOWN_COMPANY, CoverLetter, Drafts, JobPosting
from app.memory.store import Profile
from app.services import export

DAY = date(2026, 10, 22)
PROFILE = Profile(
    name="Alex Novak", email="alex.novak@example.com", phone="+49 151 2345678", city="Berlin"
)
BODY = [
    "I'd like to apply for the Junior AI Engineer role at Lumen Health. Your work on clinical "
    "document search is exactly the kind of applied AI I want to build.",
    "In my capstone project I built DocuMind, a RAG chatbot with LangChain and ChromaDB that "
    "answers questions about long PDF manuals, with citations for every answer.",
    "Before moving into AI, I spent four years running logistics operations, where I led a "
    "team of two and automated weekly reports with Python and SQL.",
    "I'm based in Berlin and can start in May. I'd be glad to talk about how I could help your "
    "team ship reliable LLM features.",
]
LETTER = CoverLetter(
    recipient_company="Lumen Health GmbH",
    recipient_department="Recruiting – AI Platform team",
    recipient_city_country="Berlin, Germany",
    recipient_name=None,
    subject="Application: Junior AI Engineer – Lumen Health",
    salutation="Dear Hiring Team,",
    body_paragraphs=BODY,
    closing="Kind regards,",
    sender_name="Alex Novak",
)


def _pdf(data: bytes) -> PdfReader:
    return PdfReader(io.BytesIO(data))


# ---------------------------------------------------------------- PDF


def test_pdf_is_a_one_page_pdf_in_the_reference_order() -> None:
    data = export.export_cover_letter_pdf(LETTER, PROFILE, DAY)

    assert data.startswith(b"%PDF")
    reader = _pdf(data)
    assert len(reader.pages) == 1
    text = reader.pages[0].extract_text()
    order = [
        "Alex Novak",
        "alex.novak@example.com · +49 151 2345678 · Berlin",
        "Lumen Health GmbH",
        "Recruiting – AI Platform team",
        "Berlin, Germany",
        "22 October 2026",
        "Application: Junior AI Engineer – Lumen Health",
        "Dear Hiring Team,",
        "I'd like to apply",
        "Kind regards,",
    ]
    positions = [text.index(part) for part in order]
    assert positions == sorted(positions)
    assert text.rstrip().endswith("Alex Novak")  # signed with the profile's name


def test_umlauts_and_dashes_render_with_the_embedded_font() -> None:
    letter = LETTER.model_copy(
        update={
            "recipient_company": "Müller & Söhne GmbH",
            "recipient_city_country": "Köln, Deutschland",
            "body_paragraphs": ["Ich würde gern bei Ihnen anfangen – größte Grüße, Jürgen."],
        }
    )
    reader = _pdf(export.export_cover_letter_pdf(letter, PROFILE, DAY))

    text = reader.pages[0].extract_text()
    assert "Müller & Söhne GmbH" in text and "Köln" in text and "größte" in text
    fonts = reader.pages[0]["/Resources"]["/Font"].get_object().values()
    names = [str(f.get_object()["/BaseFont"]) for f in fonts]
    assert names and all("LiberationSans" in n for n in names)  # embedded, not Helvetica


def test_a_long_letter_loses_spacing_before_font_size() -> None:
    long_body = [f"{p} {p}" for p in (BODY * 2)[:8]]  # about 430 words
    letter = LETTER.model_copy(update={"body_paragraphs": long_body})
    blocks = export.letter_blocks(letter, PROFILE)

    layout, data = export._fit_one_page(blocks, "t")

    assert len(_pdf(data).pages) == 1
    assert layout != export.LAYOUT  # it had to tighten
    assert layout.body_pt == export.LAYOUT.body_pt  # but only the spacing


# ---------------------------------------------------------------- Word


def test_docx_has_the_same_blocks_and_layout() -> None:
    doc = Document(io.BytesIO(export.export_cover_letter_docx(LETTER, PROFILE, DAY)))

    texts = [p.text for p in doc.paragraphs]
    blocks = export.letter_blocks(LETTER, PROFILE, DAY)
    assert texts == [b.text for b in blocks]
    assert texts[2] == "Lumen Health GmbH\nRecruiting – AI Platform team\nBerlin, Germany"
    name, subject = doc.paragraphs[0].runs[0], doc.paragraphs[4].runs[0]
    assert name.bold and name.font.size == Pt(export.LAYOUT.name_pt)
    assert subject.bold and subject.text.startswith("Application:")
    assert doc.paragraphs[6].runs[0].font.size == Pt(export.LAYOUT.body_pt)
    margin = doc.sections[0].left_margin.cm
    assert margin == pytest.approx(export.LAYOUT.margin_cm, abs=0.01)


# ---------------------------------------------------------------- old drafts, fallback, names


def test_old_plain_text_drafts_still_display_and_export() -> None:
    old = Drafts(
        cv_bullets=["Built a RAG chatbot."],
        cover_letter="Dear team,\n\nI'd love to join Lumen Health.\n\nBest regards, Alex",
        used_claim_ids=[],
    )
    letter = export.fill_missing(old.cover_letter, "Junior AI Engineer", "Lumen Health")

    assert old.cover_letter.body_paragraphs == [
        "Dear team,",
        "I'd love to join Lumen Health.",
        "Best regards, Alex",
    ]
    assert letter.subject == "Application: Junior AI Engineer – Lumen Health"
    pdf = export.export_cover_letter(letter, PROFILE, "pdf", DAY)
    docx = export.export_cover_letter(letter, PROFILE, "docx", DAY)
    assert not pdf.fell_back and pdf.data.startswith(b"%PDF")
    assert not docx.fell_back
    text = _pdf(pdf.data).pages[0].extract_text()
    assert "Dear team," in text and "Dear Hiring Team" not in text  # no second salutation
    assert text.rstrip().endswith("Best regards, Alex")  # signed once, inside the old text


def test_stored_json_and_plain_text_both_load() -> None:
    assert CoverLetter.from_stored(LETTER.model_dump_json()) == LETTER
    assert CoverLetter.from_stored("{not json\n\nreally").body_paragraphs == [
        "{not json",
        "really",
    ]


@pytest.mark.parametrize("kind", ["pdf", "docx"])
def test_failed_export_falls_back_to_text(monkeypatch, kind) -> None:
    def broken(*args):
        raise RuntimeError("export is broken")

    monkeypatch.setattr(export, f"export_cover_letter_{kind}", broken)

    file = export.export_cover_letter(LETTER, PROFILE, kind, DAY)

    assert file.fell_back and file.mime == "text/plain"
    assert file.file_name == "CoverLetter_Alex_Novak_LumenHealthGmbH.txt"
    text = file.data.decode()
    assert text.startswith("Alex Novak\n\nalex.novak@example.com")
    assert "Dear Hiring Team," in text and BODY[0] in text


def test_file_name_has_no_spaces_or_special_characters() -> None:
    profile = Profile(name="Jürgen Groß-Weber")
    assert export.cover_letter_file_name(profile, "Café & Co. KG") == (
        "CoverLetter_Juergen_GrossWeber_CafeCoKG"
    )
    assert export.cover_letter_file_name(None, "Lumen Health") == "CoverLetter_LumenHealth"


def test_contact_line_skips_missing_details() -> None:
    blocks = export.letter_blocks(LETTER, Profile(name="Alex Novak"), DAY)
    assert [b.kind for b in blocks][:3] == ["name", "recipient", "date"]


# ---------------------------------------------------------------- finalise_letter


def _job(raw_text: str) -> JobPosting:
    return JobPosting(
        title="Junior AI Engineer",
        company="Lumen Health",
        location="Berlin",
        remote_policy="hybrid",
        seniority="junior",
        language_requirements=[],
        visa_sponsorship="unknown",
        salary_range=None,
        summary="",
        is_job_posting=True,
        raw_text=raw_text,
    )


def test_invented_contact_name_falls_back_to_dear_hiring_team() -> None:
    invented = LETTER.model_copy(
        update={"recipient_name": "Anna Weber", "salutation": "Dear Ms Weber,"}
    )

    letter = finalise_letter(invented, _job("We build AI for hospitals."), PROFILE)

    assert letter.recipient_name is None
    assert letter.salutation == FALLBACK_SALUTATION == "Dear Hiring Team,"


def test_contact_name_from_the_posting_is_kept() -> None:
    named = LETTER.model_copy(
        update={"recipient_name": "Anna Weber", "salutation": "Dear Ms Weber,"}
    )

    letter = finalise_letter(named, _job("Questions? Contact Anna Weber."), PROFILE)

    assert letter.recipient_name == "Anna Weber" and letter.salutation == "Dear Ms Weber,"


def test_finalise_fills_sender_subject_and_closing_in_python() -> None:
    sloppy = LETTER.model_copy(
        update={
            "sender_name": "Someone Else",
            "subject": "",
            "closing": " ",
            "recipient_company": "",
            "body_paragraphs": ["  First.  ", "", "Second."],
        }
    )

    letter = finalise_letter(sloppy, _job("..."), PROFILE)

    assert letter.sender_name == "Alex Novak"  # from the profile, not the LLM
    assert letter.subject == "Application: Junior AI Engineer – Lumen Health"
    assert letter.closing == "Kind regards,"
    assert letter.recipient_company == "Lumen Health"
    assert letter.body_paragraphs == ["First.", "Second."]


def test_unknown_company_never_appears_in_the_letter() -> None:
    blank = LETTER.model_copy(update={"subject": "", "recipient_company": ""})
    job = _job("...").model_copy(update={"company": UNKNOWN_COMPANY})

    letter = finalise_letter(blank, job, PROFILE)
    assert (letter.recipient_company, letter.subject) == ("", "Application: Junior AI Engineer")

    # At export the company the user set on the card (✏️) fills the gap.
    named = export.fill_missing(letter, "Junior AI Engineer", "Lumen Health")
    assert named.recipient_company == "Lumen Health"
    still_unknown = export.fill_missing(letter, "Junior AI Engineer", UNKNOWN_COMPANY)
    assert still_unknown.recipient_company == ""


def test_german_letter_gets_a_german_fallback_salutation() -> None:
    german = LETTER.model_copy(
        update={
            "recipient_name": None,
            "body_paragraphs": ["Ich bewerbe mich mit großer Freude für die Stelle bei Ihnen."],
        }
    )
    assert finalise_letter(german, _job("..."), PROFILE).salutation == FALLBACK_SALUTATION_DE
