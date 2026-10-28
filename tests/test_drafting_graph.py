# What: tests of the second half of the application graph: plan_claims → claim_check →
#       remember_confirmed_facts → draft_materials ⇄ output_guard → draft_review → save.
# Why: these are the Phase 5 guarantees: the drafting prompt only ever sees approved claims,
#      unsupported claims are marked, the output guard catches an injected fake claim (rewrite
#      once, then highlight), and every draft version is saved. All offline, with fakes.
# Used by: pytest.

from langgraph.types import Command

from app.agent.nodes.drafting import find_rejected_claims
from app.agent.state import Claim, Drafts
from tests.graph_fakes import (
    UNSUPPORTED_CLAIM,
    FakeMemory,
    answer_drafts,
    make_deps,
    make_llm,
    run,
)

PYTHON_CLAIM = "Two years of Python and SQL."
RAG_CLAIM = "Built a RAG chatbot with LangChain and ChromaDB."
FAKE_CLAIM = "I led a team of 50 engineers at Google."

APPROVE_TWO_REMOVE_ONE = {
    "decisions": [
        {"id": "C1", "action": "approve"},
        {"id": "C2", "action": "approve"},
        {"id": "C3", "action": "remove"},
    ]
}


def to_claim_check(deps):
    """Run to the fit review and choose "continue"."""
    graph, config = run(deps)
    graph.invoke(Command(resume={"action": "continue"}), config)
    return graph, config


def to_draft_review(deps, decisions=APPROVE_TWO_REMOVE_ONE):
    graph, config = to_claim_check(deps)
    graph.invoke(Command(resume=decisions), config)
    return graph, config


def drafting_prompts(llm) -> list[str]:
    return [msgs[-1].content for name, msgs in llm.calls if name == "Drafts"]


# ---------------------------------------------------------------- claim check


def test_continue_plans_claims_and_pauses_at_claim_check() -> None:
    graph, config = to_claim_check(make_deps())

    state = graph.get_state(config)
    assert state.next == ("claim_check",)
    payload = state.tasks[0].interrupts[0].value
    assert payload["type"] == "claim_check"
    claims = state.values["claims"]
    assert [(c.id, c.text, c.supported) for c in claims] == [
        ("C1", PYTHON_CLAIM, True),
        ("C2", RAG_CLAIM, True),
        ("C3", UNSUPPORTED_CLAIM, False),  # it cited an id that doesn't exist
    ]
    assert claims[1].evidence[0].doc_name == "documind.md"
    assert claims[2].evidence_chunk_ids == []


def test_drafting_prompt_receives_only_approved_claims() -> None:
    llm = make_llm()
    graph, config = to_draft_review(make_deps(llm))

    prompts = drafting_prompts(llm)
    assert len(prompts) == 1
    assert PYTHON_CLAIM in prompts[0] and RAG_CLAIM in prompts[0]
    assert UNSUPPORTED_CLAIM not in prompts[0]
    drafts = graph.get_state(config).values["drafts"]
    assert UNSUPPORTED_CLAIM not in drafts.cover_letter
    assert drafts.used_claim_ids == ["C1", "C2"]


def test_undecided_claims_count_as_removed() -> None:
    llm = make_llm()
    graph, config = to_draft_review(
        make_deps(llm), {"decisions": [{"id": "C1", "action": "approve"}]}
    )

    decisions = [c.user_decision for c in graph.get_state(config).values["claims"]]
    assert decisions == ["approved", "removed", "removed"]
    assert RAG_CLAIM not in drafting_prompts(llm)[0]


def test_edited_claim_is_used_with_the_new_wording() -> None:
    llm = make_llm()
    edited = "Built a RAG chatbot for 40 classmates with LangChain."
    graph, config = to_draft_review(
        make_deps(llm),
        {
            "decisions": [
                {"id": "C1", "action": "approve"},
                {"id": "C2", "action": "edit", "text": edited},
            ]
        },
    )

    assert edited in drafting_prompts(llm)[0]
    assert RAG_CLAIM not in drafting_prompts(llm)[0]
    assert graph.get_state(config).values["claims"][1].user_decision == "edited"


def test_confirmed_facts_are_remembered_once_and_reused_as_evidence() -> None:
    memory = FakeMemory()
    keep_all = {
        "decisions": [{"id": f"C{i}", "action": "approve"} for i in (1, 2, 3)],
    }
    to_draft_review(make_deps(memory=memory), keep_all)

    # Only the claim without document evidence needed the user's word for it.
    assert memory.facts == [UNSUPPORTED_CLAIM]

    # Next job: the remembered fact is evidence now, so the same claim is "supported".
    graph, config = to_claim_check(make_deps(memory=memory))
    claims = graph.get_state(config).values["claims"]
    fact_claim = next(c for c in claims if c.evidence_chunk_ids == ["fact-1"])
    assert fact_claim.supported and fact_claim.text == UNSUPPORTED_CLAIM

    graph.invoke(Command(resume=keep_all), config)
    assert memory.facts == [UNSUPPORTED_CLAIM]  # not saved twice


# ---------------------------------------------------------------- drafting and output guard


def test_clean_draft_passes_the_guard_and_is_saved() -> None:
    memory = FakeMemory()
    llm = make_llm()
    graph, config = to_draft_review(make_deps(llm, memory=memory))

    state = graph.get_state(config)
    assert state.next == ("draft_review",)
    assert state.values["guard_issues"] == []
    assert llm.schemas_called()[-3:] == ["ClaimPlan", "Drafts", "GuardReport"]
    assert [(job_id, edited) for job_id, _, edited in memory.drafts] == [(7, False)]


def test_output_guard_flags_injected_fake_claim_and_draft_is_rewritten() -> None:
    calls = {"n": 0}

    def writer_that_invents_once(messages) -> Drafts:
        drafts = answer_drafts(messages)
        calls["n"] += 1
        if calls["n"] == 1:
            drafts.cover_letter = drafts.cover_letter.replace(
                "Best regards", f"{FAKE_CLAIM}\nBest regards"
            )
        return drafts

    llm = make_llm({Drafts: writer_that_invents_once})
    graph, config = to_draft_review(make_deps(llm))

    assert llm.schemas_called()[-4:] == ["Drafts", "GuardReport", "Drafts", "GuardReport"]
    # The rewrite prompt told the writer exactly what to drop.
    assert FAKE_CLAIM in drafting_prompts(llm)[1]
    state = graph.get_state(config)
    assert FAKE_CLAIM not in state.values["drafts"].cover_letter
    assert state.values["guard_issues"] == []


def test_persistent_fake_claim_is_highlighted_after_one_rewrite() -> None:
    def stubborn_writer(messages) -> Drafts:
        drafts = answer_drafts(messages)
        drafts.cv_bullets.append(FAKE_CLAIM)
        return drafts

    llm = make_llm({Drafts: stubborn_writer})
    graph, config = to_draft_review(make_deps(llm))

    state = graph.get_state(config)
    assert state.next == ("draft_review",)  # no endless loop: 2 attempts, then the user decides
    assert [i.quote for i in state.values["guard_issues"]] == [FAKE_CLAIM]
    payload = state.tasks[0].interrupts[0].value
    assert payload["guard_issues"][0]["quote"] == FAKE_CLAIM
    assert len(drafting_prompts(llm)) == 2


def test_removed_claim_wording_is_caught_without_the_llm() -> None:
    claims = [
        Claim(
            id="C1",
            text="Led a team",
            evidence_chunk_ids=[],
            supported=False,
            user_decision="removed",
        ),
        Claim(
            id="C2",
            text="Knows Python",
            evidence_chunk_ids=[],
            supported=True,
            user_decision="approved",
        ),
    ]

    issues = find_rejected_claims("In my last job I LED A  TEAM of three.", claims)

    assert [i.quote for i in issues] == ["Led a team"]
    assert find_rejected_claims("I know Python.", claims) == []


def test_style_rules_and_tone_reach_the_drafting_prompt() -> None:
    llm = make_llm()
    to_draft_review(make_deps(llm, memory=FakeMemory(rules=["Avoid the word 'passionate'"])))

    system = next(msgs[0].content for name, msgs in llm.calls if name == "Drafts")
    assert "- Avoid the word 'passionate'" in system
    assert "Warm and natural" in system  # the default "friendly" tone


# ---------------------------------------------------------------- draft review


def test_regenerate_cover_letter_keeps_edited_bullets() -> None:
    llm = make_llm()
    graph, config = to_draft_review(make_deps(llm))
    drafts = graph.get_state(config).values["drafts"]
    my_bullets = Drafts(
        cv_bullets=["My own bullet."],
        cover_letter=drafts.cover_letter,
        used_claim_ids=drafts.used_claim_ids,
    )

    graph.invoke(
        Command(
            resume={
                "action": "regenerate",
                "section": "cover_letter",
                "instruction": "Make it shorter",
                "edited": my_bullets.model_dump(),
            }
        ),
        config,
    )

    state = graph.get_state(config)
    assert state.next == ("draft_review",)
    assert state.values["drafts"].cv_bullets == ["My own bullet."]
    assert "Make it shorter" in drafting_prompts(llm)[-1]
    # Only the new section was fact-checked, so the user's own bullet isn't flagged.
    assert state.values["guard_issues"] == []


def test_accept_with_edits_saves_an_edited_version_and_finishes() -> None:
    memory = FakeMemory()
    graph, config = to_draft_review(make_deps(memory=memory))
    drafts = graph.get_state(config).values["drafts"]
    edited = drafts.model_copy(update={"cover_letter": drafts.cover_letter + "\nP.S. Thanks!"})

    graph.invoke(Command(resume={"action": "accept", "edited": edited.model_dump()}), config)

    state = graph.get_state(config)
    assert state.next == ()
    assert state.values["user_edits"].cover_letter.endswith("P.S. Thanks!")
    assert [edited for _, _, edited in memory.drafts] == [False, True]


def test_accept_without_edits_saves_no_extra_version() -> None:
    memory = FakeMemory()
    graph, config = to_draft_review(make_deps(memory=memory))

    graph.invoke(Command(resume={"action": "accept"}), config)

    assert graph.get_state(config).next == ()
    assert len(memory.drafts) == 1
