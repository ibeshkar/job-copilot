# What: test doubles that replace paid, networked services: embeddings, a scripted structured-
#       output LLM and a scripted tool-calling chat model.
# Why: unit tests must run offline, fast and for free (PROJECT_PLAN.md §14).
# Used by: tests/test_rag.py, tests/test_onboarding_service.py, the graph tests (FakeStructuredLLM)
#          and the copilot tests (FakeToolLLM).

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


class FakeToolLLM:
    """Stands in for a tool-calling chat model in copilot tests.

    `script` is a list of answers, one per LLM call: an AIMessage (possibly with tool_calls)
    or a function that builds one from the messages. `bound` records which tool names were
    bound for each call, so tests can check the dynamic tool binding.

    The chat's topic guard asks for structured output (TopicCheck). `on_topic` decides the
    verdict (True, False, or an Exception to simulate a failure); `topic_checks` counts calls.
    """

    def __init__(self, script: list, on_topic: bool | Exception = True) -> None:
        self.script = list(script)
        self.bound: list[list[str]] = []
        self.calls: list[list] = []
        self._tools: list[str] = []
        self.on_topic = on_topic
        self.topic_checks = 0

    def with_structured_output(self, schema, include_raw: bool = False):
        from app.agent.state import TopicCheck

        llm = self

        class _Runnable:
            def invoke(self, messages):
                assert schema is TopicCheck, f"unexpected schema {schema.__name__}"
                llm.topic_checks += 1
                if isinstance(llm.on_topic, Exception):
                    raise llm.on_topic
                verdict = TopicCheck(on_topic=llm.on_topic, reason="fake verdict")
                return {"raw": "ok", "parsed": verdict, "parsing_error": None}

        return _Runnable()

    def bind_tools(self, tools):
        self._tools = [t.name for t in tools]
        return self

    def invoke(self, messages):
        self.bound.append(self._tools)
        self._tools = []
        self.calls.append(messages)
        answer = self.script.pop(0)
        return answer(messages) if callable(answer) else answer
