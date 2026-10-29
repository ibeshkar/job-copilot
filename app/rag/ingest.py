# What: turns an uploaded file into searchable chunks: load text → split into chunks →
#       embed → save in the persistent Chroma vector store, with metadata for citations.
# Also: delete one document's chunks or the whole collection ("Delete all my data").
# Why: the fit analysis must quote evidence from the user's own documents. Chunks are small, so
#      a search returns the exact paragraph that proves a skill, not a whole CV.
# Used by: app/services/onboarding.py, app/rag/retriever.py (get_vectorstore), tests/test_rag.py.

import io
from functools import lru_cache
from pathlib import Path

import chromadb
import docx
from langchain_chroma import Chroma
from langchain_core.documents import Document as Chunk
from langchain_core.embeddings import Embeddings
from langchain_text_splitters import RecursiveCharacterTextSplitter
from pypdf import PdfReader

from app.agent.llm import get_embeddings
from app.config import get_settings
from app.utils.errors import DocumentError

COLLECTION_NAME = "knowledge_base"
SUPPORTED_EXTENSIONS = {".pdf", ".docx", ".md", ".txt"}
MAX_FILE_BYTES = 5 * 1024 * 1024  # 5 MB (PROJECT_PLAN.md §9)

# ~500 characters is about one paragraph or a few CV bullets: small enough that a match is
# precise, big enough to keep context. The 80-character overlap stops a sentence that
# crosses a chunk border from being lost.
CHUNK_SIZE = 500
CHUNK_OVERLAP = 80


# ---------------------------------------------------------------- loading


def load_text(filename: str, data: bytes) -> str:
    """Extract plain text from a PDF, DOCX, MD or TXT file.

    Args:
        filename: original file name; the extension decides how the file is read.
        data: the raw file bytes.

    Returns:
        The text of the document.

    Raises:
        DocumentError: unsupported type, file too large, unreadable, or no text inside.
    """
    ext = Path(filename).suffix.lower()
    if ext not in SUPPORTED_EXTENSIONS:
        raise DocumentError(f"Unsupported file type: {filename}")
    if len(data) > MAX_FILE_BYTES:
        raise DocumentError(f"File too large: {filename} ({len(data)} bytes)")

    try:
        if ext == ".pdf":
            reader = PdfReader(io.BytesIO(data))
            text = "\n\n".join(page.extract_text() or "" for page in reader.pages)
        elif ext == ".docx":
            document = docx.Document(io.BytesIO(data))
            text = "\n".join(paragraph.text for paragraph in document.paragraphs)
        else:
            text = data.decode("utf-8", errors="replace")
    except Exception as exc:  # parsers raise many different error types on broken files
        raise DocumentError(
            f"Could not read {filename}: {exc}",
            user_message=f"I couldn't read {filename}. Is the file damaged?",
        ) from exc

    if not text.strip():
        raise DocumentError(
            f"No text in {filename}",
            user_message=f"{filename} has no readable text (is it a scanned image?).",
        )
    return text


def guess_doc_type(filename: str) -> str:
    """Guess the kind of document from its name, e.g. "alex_cv.md" → "cv".

    The type is stored as chunk metadata so later searches can focus on, say, CVs only.
    """
    name = filename.lower()
    rules = [
        ("cv", ("cv", "resume", "lebenslauf")),
        ("brag_doc", ("brag",)),
        ("project", ("readme", "project")),
        ("letter", ("letter", "anschreiben")),
        ("certificate", ("certificate", "cert")),
    ]
    for doc_type, keywords in rules:
        if any(keyword in name for keyword in keywords):
            return doc_type
    return "other"


# ---------------------------------------------------------------- chunking


def split_into_chunks(text: str, doc_name: str, doc_type: str) -> list[Chunk]:
    """Split text into ~500-character chunks with citation metadata.

    Each chunk gets `doc_name`, `doc_type` and a stable `chunk_id` like "cv.md#3", so the
    same file always produces the same ids.
    """
    splitter = RecursiveCharacterTextSplitter(chunk_size=CHUNK_SIZE, chunk_overlap=CHUNK_OVERLAP)
    pieces = splitter.split_text(text)
    return [
        Chunk(
            page_content=piece,
            metadata={"doc_name": doc_name, "doc_type": doc_type, "chunk_id": f"{doc_name}#{i}"},
        )
        for i, piece in enumerate(pieces)
    ]


# ---------------------------------------------------------------- vector store


def make_vectorstore(embeddings: Embeddings | None, persist_dir: Path) -> Chroma:
    """Open (or create) the Chroma collection in `persist_dir`.

    Tests call this directly with fake embeddings and a temporary folder. `embeddings=None`
    gives a store that can only list and delete (used to clean up without an API key).
    """
    persist_dir.mkdir(parents=True, exist_ok=True)
    return Chroma(
        collection_name=COLLECTION_NAME,
        embedding_function=embeddings,
        persist_directory=str(persist_dir),
        client_settings=chromadb.config.Settings(anonymized_telemetry=False),
        # Cosine distance turns into a 0..1 relevance score, which is easy to explain.
        collection_configuration={"hnsw": {"space": "cosine"}},
    )


@lru_cache
def get_vectorstore() -> Chroma:
    """The app's knowledge base (opened once per process).

    Raises:
        ConfigError: no OpenRouter key, so texts can't be embedded.
    """
    settings = get_settings()
    return make_vectorstore(get_embeddings(settings), settings.chroma_dir)


def _maintenance_store() -> Chroma:
    """The knowledge base for deleting: works without an API key (deleting needs no embeddings)."""
    settings = get_settings()
    if settings.has_llm_key:
        return get_vectorstore()
    return make_vectorstore(None, settings.chroma_dir)


def delete_document_chunks(doc_name: str, vectorstore: Chroma | None = None) -> int:
    """Remove all chunks of one document. Returns how many were removed."""
    vectorstore = vectorstore or _maintenance_store()
    ids = vectorstore.get(where={"doc_name": doc_name}, include=[])["ids"]
    if ids:
        vectorstore.delete(ids=ids)
    return len(ids)


def ingest_file(
    filename: str,
    data: bytes,
    doc_type: str | None = None,
    vectorstore: Chroma | None = None,
) -> int:
    """Add one file to the knowledge base. Re-adding a file replaces its old chunks.

    Args:
        filename: used as `doc_name` in citations.
        data: the raw file bytes.
        doc_type: "cv", "project", ...; guessed from the file name if not given.
        vectorstore: the store to write to; defaults to the app's knowledge base.

    Returns:
        The number of chunks stored.

    Raises:
        DocumentError: the file can't be used (see `load_text`).
        ConfigError: no API key for embeddings.
    """
    vectorstore = vectorstore or get_vectorstore()
    text = load_text(filename, data)
    chunks = split_into_chunks(text, filename, doc_type or guess_doc_type(filename))

    # Delete first so a shorter new version doesn't leave old chunks (e.g. "cv.md#9") behind.
    delete_document_chunks(filename, vectorstore)
    vectorstore.add_documents(chunks, ids=[c.metadata["chunk_id"] for c in chunks])
    return len(chunks)


def clear_knowledge_base(vectorstore: Chroma | None = None) -> None:
    """Delete every chunk of every document ("Delete all my data")."""
    (vectorstore or _maintenance_store()).delete_collection()
    # The cached store points at the deleted collection; the next use creates a fresh one.
    get_vectorstore.cache_clear()


def count_chunks(vectorstore: Chroma | None = None) -> int:
    """Total number of chunks in the knowledge base."""
    vectorstore = vectorstore or get_vectorstore()
    return len(vectorstore.get(include=[])["ids"])
