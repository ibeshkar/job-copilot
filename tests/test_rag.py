# What: tests for loading, chunking, storing and searching documents (app/rag/).
# Why: the fit analysis is only as good as the evidence it finds. These tests use the demo
#      persona and offline fake embeddings; one optional test uses the real embedding model.
# Used by: pytest. Run the live test with `uv run pytest -m live`.

import io

import docx
import pytest

from app.config import get_settings
from app.rag.ingest import (
    CHUNK_SIZE,
    count_chunks,
    delete_document_chunks,
    guess_doc_type,
    ingest_file,
    load_text,
    make_vectorstore,
    split_into_chunks,
)
from app.rag.retriever import search_evidence
from app.services.onboarding import demo_persona_files
from app.utils.errors import DocumentError

EXPECTED_DOC = "project_documind_readme.md"


def _ingest_demo(vectorstore) -> dict[str, int]:
    return {
        name: ingest_file(name, data, vectorstore=vectorstore)
        for name, data in demo_persona_files()
    }


def test_load_text_reads_docx() -> None:
    document = docx.Document()
    document.add_paragraph("Built a RAG chatbot with LangChain.")
    buffer = io.BytesIO()
    document.save(buffer)

    assert "RAG chatbot" in load_text("cv.docx", buffer.getvalue())


@pytest.mark.parametrize(
    ("filename", "data"),
    [
        ("photo.png", b"\x89PNG"),  # unsupported type
        ("empty.md", b"   \n"),  # no text
        ("huge.txt", b"a" * (5 * 1024 * 1024 + 1)),  # over 5 MB
        ("broken.pdf", b"not really a pdf"),  # unreadable
    ],
)
def test_load_text_rejects_bad_files(filename: str, data: bytes) -> None:
    with pytest.raises(DocumentError):
        load_text(filename, data)


def test_guess_doc_type() -> None:
    assert guess_doc_type("alex_novak_cv.md") == "cv"
    assert guess_doc_type("project_documind_readme.md") == "project"
    assert guess_doc_type("alex_novak_brag_doc.md") == "brag_doc"
    assert guess_doc_type("notes.txt") == "other"


def test_chunks_have_size_limit_and_citation_metadata() -> None:
    text = "Python and SQL. " * 200
    chunks = split_into_chunks(text, "cv.md", "cv")

    assert len(chunks) > 1
    assert all(len(c.page_content) <= CHUNK_SIZE for c in chunks)
    assert chunks[2].metadata == {"doc_name": "cv.md", "doc_type": "cv", "chunk_id": "cv.md#2"}


def test_ingest_sample_docs(vectorstore) -> None:
    added = _ingest_demo(vectorstore)

    assert len(added) == 4
    assert all(n > 0 for n in added.values())
    assert count_chunks(vectorstore) == sum(added.values())


def test_reingesting_a_file_replaces_its_chunks(vectorstore) -> None:
    long_text = ("Led a team of 3 dispatchers. " * 60).encode()
    first = ingest_file("cv.md", long_text, vectorstore=vectorstore)
    second = ingest_file("cv.md", b"Short new CV: Python developer.", vectorstore=vectorstore)

    assert first > 1
    assert second == 1
    assert count_chunks(vectorstore) == 1

    assert delete_document_chunks("cv.md", vectorstore) == 1
    assert count_chunks(vectorstore) == 0


def test_retrieval_finds_rag_chatbot_project(vectorstore) -> None:
    _ingest_demo(vectorstore)

    results = search_evidence("RAG chatbot", k=4, vectorstore=vectorstore)

    assert EXPECTED_DOC in [e.doc_name for e in results]
    assert all(0.0 <= e.relevance <= 1.0 and len(e.snippet) <= 300 for e in results)
    assert results == sorted(results, key=lambda e: e.relevance, reverse=True)


def test_retrieval_can_filter_by_doc_type(vectorstore) -> None:
    _ingest_demo(vectorstore)

    results = search_evidence("Python", k=10, doc_types=["cv"], vectorstore=vectorstore)

    assert results
    assert {e.doc_name for e in results} == {"alex_novak_cv.md"}


def test_retrieval_on_empty_knowledge_base_returns_nothing(vectorstore) -> None:
    assert search_evidence("Python", vectorstore=vectorstore) == []


@pytest.mark.live
def test_live_retrieval_with_real_embeddings(tmp_path) -> None:
    from app.agent.llm import get_embeddings

    settings = get_settings()
    if not settings.has_llm_key:
        pytest.skip("OPENROUTER_API_KEY not set")
    store = make_vectorstore(get_embeddings(settings), tmp_path / "chroma")
    _ingest_demo(store)

    results = search_evidence("RAG chatbot", k=4, vectorstore=store)

    assert results[0].doc_name == EXPECTED_DOC
