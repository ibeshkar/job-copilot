# What: the work behind the onboarding wizard: add documents to the knowledge base (and record
#       them in the database), load the demo persona, and report what the knowledge base holds.
# Why: the Streamlit page should only draw widgets. Keeping this logic here makes it testable
#      without a browser and reusable by the Memory page and the copilot later.
# Used by: app/ui/pages/0_onboarding.py, app/main.py, tests/test_onboarding_service.py.

from dataclasses import dataclass, field

from langchain_chroma import Chroma

from app.config import PROJECT_ROOT
from app.db import repo
from app.memory.store import Preferences, Profile, get_profile
from app.rag.ingest import guess_doc_type, ingest_file
from app.utils.errors import AppError, friendly_message
from app.utils.logging import get_logger, log_exception

logger = get_logger(__name__)

MAX_FILES_PER_UPLOAD = 10  # PROJECT_PLAN.md §9
DEMO_PERSONA_DIR = PROJECT_ROOT / "data" / "samples" / "persona"

# Matches data/samples/persona/alex_novak_cv.md.
DEMO_PROFILE = Profile(
    name="Alex Novak",
    target_roles=["Junior AI Engineer", "LLM Application Developer"],
    seniority="junior",
    locations=["Berlin", "Remote (EU)"],
    work_authorisation="EU citizen, no visa needed in the EU",
    languages=["Czech native", "English C1", "German A2"],
    salary_expectations="€50,000 – €60,000",
    email="alex.novak@example.com",
    city="Berlin, Germany",
)
DEMO_PREFERENCES = Preferences(
    tone="friendly",
    letter_length="medium",
    letter_language="Same as the job posting",
    deal_breakers=["Fully on-site outside Berlin"],
)


@dataclass
class IngestReport:
    """What happened when the user added documents."""

    added: dict[str, int] = field(default_factory=dict)  # filename → number of chunks
    errors: dict[str, str] = field(default_factory=dict)  # filename → friendly message

    @property
    def total_chunks(self) -> int:
        return sum(self.added.values())


def add_documents(
    files: list[tuple[str, bytes]], vectorstore: Chroma | None = None
) -> IngestReport:
    """Add uploaded files to the knowledge base and record them in the `Document` table.

    One bad file doesn't stop the others; its problem is reported in `errors`.

    Args:
        files: (filename, bytes) pairs, e.g. from `st.file_uploader`.
        vectorstore: the store to write to; defaults to the app's knowledge base.

    Returns:
        An IngestReport with the chunk count per file and any errors.
    """
    report = IngestReport()
    if len(files) > MAX_FILES_PER_UPLOAD:
        report.errors["(upload)"] = f"Please upload at most {MAX_FILES_PER_UPLOAD} files at once."
        return report

    for filename, data in files:
        try:
            doc_type = guess_doc_type(filename)
            chunks = ingest_file(filename, data, doc_type=doc_type, vectorstore=vectorstore)
            with repo.session_scope() as session:
                repo.upsert_document(session, filename, doc_type, chunks)
            report.added[filename] = chunks
        except AppError as exc:
            logger.warning("Document rejected: %s (%s)", filename, exc)
            report.errors[filename] = exc.user_message
        except Exception as exc:  # e.g. network error while embedding
            log_exception(logger, "Ingest failed", filename=filename)
            report.errors[filename] = friendly_message(exc)
    return report


def demo_persona_files() -> list[tuple[str, bytes]]:
    """The demo persona's documents as (filename, bytes) pairs."""
    return [(path.name, path.read_bytes()) for path in sorted(DEMO_PERSONA_DIR.glob("*.md"))]


def load_demo_documents(vectorstore: Chroma | None = None) -> IngestReport:
    """Add the demo persona's CV, project READMEs and brag doc to the knowledge base."""
    return add_documents(demo_persona_files(), vectorstore=vectorstore)


def knowledge_base_summary() -> tuple[int, int]:
    """Return (number of chunks, number of documents), read from the `Document` table.

    Uses the database instead of Chroma so it works even without an API key.
    """
    with repo.session_scope() as session:
        docs = repo.list_documents(session)
    return sum(d.chunk_count for d in docs), len(docs)


def list_knowledge_base_files() -> list[tuple[str, str, int]]:
    """Return (filename, doc_type, chunk_count) for every document in the knowledge base."""
    with repo.session_scope() as session:
        return [(d.filename, d.doc_type, d.chunk_count) for d in repo.list_documents(session)]


def is_onboarded() -> bool:
    """True once the user has saved a profile (then the wizard is no longer shown first)."""
    return get_profile() is not None


def demo_file_names() -> list[str]:
    """Names of the demo documents, shown next to the "Use the demo persona" button."""
    return [path.name for path in sorted(DEMO_PERSONA_DIR.glob("*.md"))]
