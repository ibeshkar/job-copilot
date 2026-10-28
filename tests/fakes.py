# What: test doubles that replace paid, networked services (here: the embedding model).
# Why: unit tests must run offline, fast and for free (PROJECT_PLAN.md §14).
# Used by: tests/test_rag.py, tests/test_onboarding_service.py.

import hashlib
import math
import re

from langchain_core.embeddings import Embeddings


class KeywordEmbeddings(Embeddings):
    """Bag-of-words embeddings: texts that share words get similar vectors.

    Not as smart as a real model (no synonyms), but deterministic and good enough to check
    that "RAG chatbot" finds the document that talks about a RAG chatbot.
    """

    def __init__(self, dims: int = 512) -> None:
        self.dims = dims

    def _embed(self, text: str) -> list[float]:
        vector = [0.0] * self.dims
        for word in re.findall(r"[a-z0-9]+", text.lower()):
            bucket = int(hashlib.md5(word.encode()).hexdigest(), 16) % self.dims
            vector[bucket] += 1.0
        norm = math.sqrt(sum(v * v for v in vector)) or 1.0
        return [v / norm for v in vector]

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self._embed(t) for t in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._embed(text)


class FakeStructuredLLM:
    """Stands in for the chat model in graph tests: no network, fully scripted.

    `responders` maps an output schema (e.g. RequirementList) to either a fixed answer or a
    function that builds the answer from the prompt messages. Every call is recorded in
    `calls` as (schema name, messages), so tests can check what the LLM was asked.
    """

    def __init__(self, responders: dict) -> None:
        self.responders = responders
        self.calls: list[tuple[str, list]] = []

    def with_structured_output(self, schema, include_raw: bool = False):
        llm = self

        class _Runnable:
            def invoke(self, messages):
                llm.calls.append((schema.__name__, messages))
                responder = llm.responders[schema]
                answer = responder(messages) if callable(responder) else responder
                if isinstance(answer, Exception):
                    return {"raw": "bad", "parsed": None, "parsing_error": answer}
                return {"raw": "ok", "parsed": answer, "parsing_error": None}

        return _Runnable()

    def schemas_called(self) -> list[str]:
        return [name for name, _ in self.calls]
