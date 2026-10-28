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
