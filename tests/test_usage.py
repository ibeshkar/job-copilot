# What: tests for token and cost tracking (app/services/usage.py) and its wiring into the
#       services (each action is logged under its name).
# Why: every action's tokens and cost must be counted and priced
#      correctly, and the session badge and budget warning must add up.
# Used by: pytest.

from datetime import UTC, datetime, timedelta

import pytest
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage

from app.services import usage
from app.services.usage import Usage, budget_warning, cost_usd, price_for, track_usage


def _reply(model: str = "openai/gpt-4o-mini", tokens_in: int = 1000, tokens_out: int = 500):
    return AIMessage(
        "ok",
        usage_metadata={
            "input_tokens": tokens_in,
            "output_tokens": tokens_out,
            "total_tokens": tokens_in + tokens_out,
        },
        response_metadata={"model_name": model},
    )


def _llm(*replies: AIMessage) -> GenericFakeChatModel:
    return GenericFakeChatModel(messages=iter(replies))


@pytest.mark.parametrize(
    ("model", "expected"),
    [
        ("openai/gpt-4o-mini", "openai/gpt-4o-mini"),
        ("openai/gpt-4o-mini-2024-07-18", "openai/gpt-4o-mini"),  # dated name from the API
        ("gpt-4o-mini", "openai/gpt-4o-mini"),  # without the provider prefix
        ("openai/gpt-4o-2024-08-06", "openai/gpt-4o"),
    ],
)
def test_price_lookup_matches_dated_and_short_names(model: str, expected: str) -> None:
    from app.config import MODEL_PRICING

    assert price_for(model) == MODEL_PRICING[expected]


def test_cost_is_priced_per_million_tokens() -> None:
    # gpt-4o-mini: $0.15 per 1M input, $0.60 per 1M output
    assert cost_usd("openai/gpt-4o-mini", 1_000_000, 1_000_000) == pytest.approx(0.75)
    assert cost_usd("some/unknown-model", 1000, 1000) == 0.0


def test_track_usage_counts_only_calls_inside_the_block(app_db) -> None:
    llm = _llm(_reply(), _reply(), _reply())

    with track_usage("draft") as counted:
        llm.invoke("one")
        llm.invoke("two")
    llm.invoke("outside")

    assert (counted.input_tokens, counted.output_tokens) == (2000, 1000)
    assert counted.cost_usd == pytest.approx(2 * (1000 * 0.15 + 500 * 0.60) / 1e6)
    report = usage.usage_report()
    assert [(r["action"], r["input tokens"], r["output tokens"]) for r in report] == [
        ("draft", 2000, 1000)
    ]


def test_usage_is_saved_even_when_the_action_fails(app_db) -> None:
    llm = _llm(_reply())

    with pytest.raises(RuntimeError), track_usage("analyse_fit"):
        llm.invoke("spent tokens")
        raise RuntimeError("then the network died")

    assert usage.usage_since(None).total_tokens == 1500


def test_session_totals_and_last_action(app_db) -> None:
    start = datetime.now(UTC) - timedelta(seconds=1)
    with track_usage("analyse_fit"):
        _llm(_reply(tokens_in=3000, tokens_out=1000)).invoke("x")
    with track_usage("chat"):
        _llm(_reply(tokens_in=100, tokens_out=50)).invoke("y")

    assert usage.usage_since(start).total_tokens == 4150
    action, last = usage.last_action()
    assert (action, last.total_tokens) == ("chat", 150)
    assert usage.usage_since(datetime.now(UTC) + timedelta(hours=1)).total_tokens == 0


def test_label_is_short_and_readable() -> None:
    assert Usage(12_000, 400, 0.0062).label() == "12.4k tokens · $0.0062"
    assert Usage(120, 30, 0.0).label() == "150 tokens · $0.0000"


def test_budget_warning_starts_at_80_percent() -> None:
    assert budget_warning(Usage(7_000, 0), 10_000) is None
    assert "80%" in budget_warning(Usage(8_000, 0), 10_000)
    assert "over its budget" in budget_warning(Usage(10_000, 1), 10_000)


def test_each_service_action_is_logged_under_its_name(fake_app, monkeypatch) -> None:
    """The application service names every run, so the dev page can show cost per action."""
    from app.services import applications
    from tests.graph_fakes import POSTING

    names = []

    class Recorder:
        def __init__(self, action, **extra):
            names.append((action, extra))

        def __enter__(self):
            return Usage()

        def __exit__(self, *exc):
            return False

    monkeypatch.setattr(applications, "track_run", Recorder)
    job_id = applications.add_job(POSTING).job_id
    applications.submit_fit_review(job_id, "continue")

    assert names == [("analyse_fit", {"job_id": job_id}), ("plan_claims", {"job_id": job_id})]
