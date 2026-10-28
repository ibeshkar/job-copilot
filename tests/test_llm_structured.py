# What: tests for invoke_structured(), the one way nodes call the LLM for data.
# Why: a malformed answer must get exactly one retry with the error shown to the model, and then
#      fail with a friendly StructuredOutputError (PROJECT_PLAN.md §10).
# Used by: pytest.

import pytest
from langchain_core.messages import HumanMessage
from pydantic import ValidationError

from app.agent.llm import invoke_structured
from app.agent.state import RequirementList
from app.utils.errors import StructuredOutputError
from tests.fakes import FakeStructuredLLM

GOOD = RequirementList(requirements=[])


def _validation_error() -> ValidationError:
    try:
        RequirementList.model_validate({"requirements": "nope"})
    except ValidationError as exc:
        return exc
    raise AssertionError


def test_returns_parsed_answer() -> None:
    llm = FakeStructuredLLM({RequirementList: GOOD})

    assert invoke_structured(llm, RequirementList, [HumanMessage("hi")]) == GOOD
    assert len(llm.calls) == 1


def test_retries_once_with_the_error() -> None:
    answers = iter([_validation_error(), GOOD])
    llm = FakeStructuredLLM({RequirementList: lambda messages: next(answers)})

    assert invoke_structured(llm, RequirementList, [HumanMessage("hi")]) == GOOD
    retry_messages = llm.calls[1][1]
    assert len(retry_messages) == 2 and "did not match" in retry_messages[-1].content


def test_gives_up_after_one_retry() -> None:
    llm = FakeStructuredLLM({RequirementList: lambda messages: _validation_error()})

    with pytest.raises(StructuredOutputError):
        invoke_structured(llm, RequirementList, [HumanMessage("hi")])
    assert len(llm.calls) == 2


def test_answer_cut_off_by_token_limit_fails_cleanly() -> None:
    import openai
    from openai.types.chat import ChatCompletion
    from openai.types.completion_usage import CompletionUsage

    cut_off = openai.LengthFinishReasonError(
        completion=ChatCompletion(
            id="x",
            choices=[],
            created=0,
            model="m",
            object="chat.completion",
            usage=CompletionUsage(completion_tokens=1500, prompt_tokens=1, total_tokens=1501),
        )
    )

    class CutOffLLM:
        calls = 0

        def with_structured_output(self, schema, include_raw=False):
            llm = self

            class _Runnable:
                def invoke(self, messages):
                    llm.calls += 1
                    raise cut_off

            return _Runnable()

    llm = CutOffLLM()
    with pytest.raises(StructuredOutputError):
        invoke_structured(llm, RequirementList, [HumanMessage("hi")])
    assert llm.calls == 1  # asking again would be cut off the same way
