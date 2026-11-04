# What: tests for `friendly_message()` and the LLM error mapping in app/agent/llm.py.
# Why: users never see a stack trace and get a message that says
#      what to do: "check your internet", "the service is busy", "check your key".
# Used by: pytest.

import httpx
import openai
import pytest
from langchain_core.messages import HumanMessage
from langgraph.errors import GraphRecursionError

from app.agent import llm as llm_module
from app.agent.llm import invoke_chat
from app.utils.errors import (
    KEY_REJECTED_MESSAGE,
    NETWORK_MESSAGE,
    RECURSION_MESSAGE,
    AppError,
    FetchError,
    LLMServiceError,
    friendly_message,
)

REQUEST = httpx.Request("POST", "https://openrouter.ai/api/v1/chat/completions")


def _response(status: int) -> httpx.Response:
    return httpx.Response(status, request=REQUEST)


@pytest.mark.parametrize(
    ("exc", "expected"),
    [
        (openai.APIConnectionError(request=REQUEST), NETWORK_MESSAGE),
        (httpx.ConnectError("no route to host"), NETWORK_MESSAGE),
        (openai.APITimeoutError(request=REQUEST), LLMServiceError.user_message),
        (
            openai.RateLimitError("slow down", response=_response(429), body=None),
            LLMServiceError.user_message,
        ),
        (
            openai.AuthenticationError("bad key", response=_response(401), body=None),
            KEY_REJECTED_MESSAGE,
        ),
        (GraphRecursionError("too many steps"), RECURSION_MESSAGE),
        (FetchError("x"), FetchError.user_message),
        (KeyError("internal detail"), AppError.user_message),
    ],
)
def test_friendly_message_for_known_errors(exc: Exception, expected: str) -> None:
    assert friendly_message(exc) == expected


def test_friendly_message_follows_the_cause_chain() -> None:
    try:
        try:
            raise openai.APIConnectionError(request=REQUEST)
        except openai.APIConnectionError as inner:
            raise RuntimeError("tool failed") from inner
    except RuntimeError as outer:
        assert friendly_message(outer) == NETWORK_MESSAGE


def test_no_internet_becomes_a_network_message_after_retries(monkeypatch) -> None:
    """Killing the network: the call is retried, then fails with a friendly message."""
    attempts = []

    class Offline:
        def invoke(self, messages):
            attempts.append(1)
            raise openai.APIConnectionError(request=REQUEST)

    monkeypatch.setattr(llm_module._invoke_with_retry.retry, "sleep", lambda _: None)

    with pytest.raises(LLMServiceError) as caught:
        invoke_chat(Offline(), [HumanMessage("hi")])

    assert len(attempts) == 3  # tenacity: 3 attempts with backoff
    assert caught.value.user_message == NETWORK_MESSAGE
