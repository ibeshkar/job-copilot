# What: tests for learning the user's writing style (PROJECT_PLAN.md §6.5): the sentence diff,
#       rule proposals and de-duplication, the Python check for banned words, the
#       `learn_from_edits` node, learning from 👎 comments, undo, and the Phase 8 "done when":
#       removing "passionate" from a letter creates a rule, and the next letter doesn't use it.
# Why: a learned rule changes every future letter, so it must be general, not duplicated, easy
#      to undo, and really enforced. All offline, with the scripted LLM from graph_fakes.
# Used by: pytest.

from langgraph.types import Command

from app.agent.nodes.drafting import find_avoided_phrases
from app.agent.nodes.learning import edit_diff, is_duplicate, propose_rules
from app.agent.state import (
    Drafts,
    ProposedStyleRule,
    StyleRule,
    StyleRuleProposal,
)
from app.db import repo
from app.memory import store
from app.services import applications, learning, my_data
from app.utils.errors import StructuredOutputError
from tests.fakes import FakeStructuredLLM
from tests.graph_fakes import (
    PASSIONATE,
    POSTING,
    FakeMemory,
    answer_drafts,
    make_deps,
    make_llm,
    passionate_writer,
    run,
)

KEEP_TWO = [
    {"id": "C1", "action": "approve"},
    {"id": "C2", "action": "approve"},
    {"id": "C3", "action": "remove"},
]


def _drafts(letter: str, bullets: list[str] | None = None) -> Drafts:
    return Drafts(
        cv_bullets=bullets or ["Built a RAG chatbot."], cover_letter=letter, used_claim_ids=[]
    )


def _rule(text: str, phrases: list[str] | None = None) -> StyleRule:
    return StyleRule(rule=text, source="explicit", avoid_phrases=phrases or [])


# ---------------------------------------------------------------- diff and rules (pure)


def test_edit_diff_lists_changed_sentences_only() -> None:
    before = _drafts(f"Dear team. {PASSIONATE} Best regards, Alex")
    after = _drafts("Dear team. I enjoy building AI products. Best regards, Alex")

    diff = edit_diff(before, after)

    assert diff == (
        "Cover letter:\n- I am passionate about building AI products.\n"
        "+ I enjoy building AI products."
    )
    assert edit_diff(before, before) == ""


def test_edit_diff_covers_bullets() -> None:
    diff = edit_diff(_drafts("Hi.", ["Led a team."]), _drafts("Hi.", ["Helped a team."]))

    assert diff == "CV bullets:\n- Led a team.\n+ Helped a team."


def test_proposed_rules_keep_only_words_the_user_really_deleted() -> None:
    llm = FakeStructuredLLM(
        {
            StyleRuleProposal: StyleRuleProposal(
                rules=[
                    ProposedStyleRule(
                        rule="Avoid 'passionate' and 'synergy'",
                        avoid_phrases=["passionate", "synergy", "building"],
                    )
                ]
            )
        }
    )
    before = _drafts(f"Dear team. {PASSIONATE}")
    after = _drafts("Dear team. I enjoy building AI products.")

    [rule] = propose_rules(llm, before, after, [])

    # "synergy" was never in the letter; "building" is still in it.
    assert rule.avoid_phrases == ["passionate"]
    assert rule.source == "edit_diff"


def test_a_rule_against_a_word_the_user_only_added_is_dropped() -> None:
    # Seen live: the user ADDED "passionate" and the model proposed "Avoid 'passionate'".
    llm = FakeStructuredLLM(
        {
            StyleRuleProposal: StyleRuleProposal(
                rules=[
                    ProposedStyleRule(rule="Avoid 'passionate'", avoid_phrases=["passionate"]),
                    ProposedStyleRule(rule="Keep paragraphs short", avoid_phrases=[]),
                ]
            )
        }
    )
    before = _drafts("Dear team. I build AI products.")
    after = _drafts(f"Dear team. {PASSIONATE}")

    rules = propose_rules(llm, before, after, [])

    assert [r.rule for r in rules] == ["Keep paragraphs short"]


def test_no_llm_call_when_nothing_changed() -> None:
    llm = make_llm()
    drafts = _drafts("Same letter.")

    assert propose_rules(llm, drafts, drafts, []) == []
    assert llm.calls == []


def test_duplicates_are_recognised() -> None:
    known = [_rule("Avoid the word 'passionate'", ["passionate"])]

    assert is_duplicate(_rule("Never use 'Passionate'", ["Passionate"]), known)
    assert is_duplicate(_rule("Avoid the word 'passionate'."), known)
    assert not is_duplicate(_rule("Keep sentences under 20 words"), known)


def test_avoided_phrases_match_whole_words_ignoring_case() -> None:
    rules = [_rule("Avoid 'passion'", ["passion"]), _rule("No 'team player'", ["team player"])]

    issues = find_avoided_phrases("My PASSION is AI. I am a team\nplayer.", rules)

    assert [i.quote for i in issues] == ["passion", "team player"]
    assert "Avoid 'passion'" in issues[0].reason
    assert find_avoided_phrases("I am compassionate.", rules) == []


# ---------------------------------------------------------------- the graph node


def _accept(graph, config, edited: Drafts) -> None:
    graph.invoke(Command(resume={"action": "accept", "edited": edited.model_dump()}), config)


def _to_draft_review(deps):
    graph, config = run(deps)
    graph.invoke(Command(resume={"action": "continue"}), config)
    graph.invoke(Command(resume={"decisions": KEEP_TWO}), config)
    return graph, config


def test_accepting_an_edit_learns_and_remembers_a_rule() -> None:
    memory = FakeMemory()
    llm = make_llm({Drafts: passionate_writer})
    graph, config = _to_draft_review(make_deps(llm, memory=memory))
    drafts = graph.get_state(config).values["drafts"]
    assert PASSIONATE in drafts.cover_letter

    edited = drafts.model_copy(
        update={
            "cover_letter": drafts.cover_letter.replace(PASSIONATE, "I enjoy building AI products.")
        }
    )
    _accept(graph, config, edited)

    state = graph.get_state(config)
    assert state.next == ()  # finished
    assert state.values["learned_rules"] == [
        {"key": "rule-1", "rule": "Avoid the word 'passionate'"}
    ]
    assert [(r.rule, r.avoid_phrases, r.source) for r in memory.rules] == [
        ("Avoid the word 'passionate'", ["passionate"], "edit_diff")
    ]


def test_accept_without_edits_asks_the_llm_nothing() -> None:
    llm = make_llm()
    graph, config = _to_draft_review(make_deps(llm))

    graph.invoke(Command(resume={"action": "accept"}), config)

    assert "StyleRuleProposal" not in llm.schemas_called()
    assert graph.get_state(config).values["learned_rules"] == []


def test_a_failed_learning_call_does_not_block_acceptance() -> None:
    memory = FakeMemory()
    llm = make_llm({StyleRuleProposal: ValueError("bad json")})
    graph, config = _to_draft_review(make_deps(llm, memory=memory))
    drafts = graph.get_state(config).values["drafts"]

    _accept(graph, config, drafts.model_copy(update={"cover_letter": "My own letter."}))

    assert graph.get_state(config).next == ()
    assert memory.rules == []
    assert memory.drafts[-1][1].cover_letter == "My own letter."  # still saved


def test_banned_word_in_a_new_draft_is_rewritten_once_then_highlighted() -> None:
    def stubborn_writer(messages) -> Drafts:
        drafts = answer_drafts(messages)
        drafts.cover_letter += "\nI am Passionate about it."
        return drafts

    memory = FakeMemory(rules=[])
    memory.rules.append(_rule("Avoid the word 'passionate'", ["passionate"]))
    llm = make_llm({Drafts: stubborn_writer})
    graph, config = _to_draft_review(make_deps(llm, memory=memory))

    state = graph.get_state(config)
    assert llm.schemas_called().count("Drafts") == 2  # first draft + one rewrite
    issue = state.values["guard_issues"][0]
    assert issue.quote == "passionate" and "style rule" in issue.reason
    rewrite_prompt = [m for name, m in llm.calls if name == "Drafts"][1][-1].content
    assert "passionate" in rewrite_prompt


# ---------------------------------------------------------------- done when (service + Store)


def test_done_when_removing_passionate_creates_a_rule_the_next_letter_follows(fake_app) -> None:
    fake_app.llm.responders[Drafts] = passionate_writer

    first = applications.add_job(POSTING).job_id
    applications.submit_fit_review(first, "continue")
    applications.submit_claim_review(first, KEEP_TWO)
    drafts = applications.get_workflow_view(first).drafts
    assert PASSIONATE in drafts.cover_letter
    edited = drafts.model_copy(
        update={
            "cover_letter": drafts.cover_letter.replace(PASSIONATE, "I enjoy building AI products.")
        }
    )
    progress: list[str] = []
    assert applications.submit_draft_review(
        first, "accept", edited=edited, on_progress=progress.append
    ).ok

    assert "Learned 1 style rule(s) from your edits" in progress
    rules = list(store.list_style_rules().values())
    assert [(r.rule, r.avoid_phrases) for r in rules] == [
        ("Avoid the word 'passionate'", ["passionate"])
    ]
    learned = learning.still_saved(applications.get_workflow_view(first).learned_rules)
    assert [r.rule for r in learned] == ["Avoid the word 'passionate'"]

    second = applications.add_job(POSTING).job_id
    applications.submit_fit_review(second, "continue")
    applications.submit_claim_review(second, KEEP_TWO)
    view = applications.get_workflow_view(second)
    assert "passionate" not in view.drafts.cover_letter.lower()
    assert view.guard_issues == []


# ---------------------------------------------------------------- service: other feedback


def test_edit_after_acceptance_learns_and_can_be_undone(app_db) -> None:
    llm = make_llm()
    before = _drafts(f"Dear team. {PASSIONATE}")
    after = _drafts("Dear team. I enjoy building AI products.")

    [learned] = learning.learn_from_edit(1, before, after, llm=llm)

    assert learned.rule == "Avoid the word 'passionate'"
    assert learning.still_saved([{"key": learned.key, "rule": learned.rule}]) == [learned]
    learning.undo(learned.key)
    assert store.list_style_rules() == {}
    assert learning.still_saved([{"key": learned.key, "rule": learned.rule}]) == []


def test_same_rule_is_not_learned_twice(app_db) -> None:
    llm = make_llm()
    before = _drafts(f"Dear team. {PASSIONATE}")
    after = _drafts("Dear team. I enjoy building AI products.")

    learning.learn_from_edit(1, before, after, llm=llm)
    assert learning.learn_from_edit(2, before, after, llm=llm) == []
    assert len(store.list_style_rules()) == 1


def test_repeated_thumbs_down_comments_become_one_rule(app_db) -> None:
    llm = FakeStructuredLLM(
        {
            StyleRuleProposal: StyleRuleProposal(
                rules=[
                    ProposedStyleRule(rule="Keep the letter under 200 words", avoid_phrases=[]),
                    ProposedStyleRule(rule="A second rule is ignored", avoid_phrases=[]),
                ]
            )
        }
    )
    with repo.session_scope() as session:
        repo.add_feedback(session, "letter", -1, "far too long")

    assert learning.learn_from_ratings("letter", llm=llm) == []  # one comment isn't a pattern
    assert llm.calls == []

    with repo.session_scope() as session:
        repo.add_feedback(session, "letter", -1, "too long again")
        repo.add_feedback(session, "letter", 1, "")
    [learned] = learning.learn_from_ratings("letter", llm=llm)

    assert learned.rule == "Keep the letter under 200 words"
    assert store.list_style_rules()[learned.key].source == "rating"
    prompt = llm.calls[0][1][-1].content
    assert "far too long" in prompt and "too long again" in prompt


def test_learning_failures_are_swallowed(app_db) -> None:
    llm = make_llm({StyleRuleProposal: StructuredOutputError("broken")})
    before, after = _drafts(f"Hi. {PASSIONATE}"), _drafts("Hi. I enjoy AI.")

    assert learning.learn_from_edit(1, before, after, llm=llm) == []
    assert store.list_style_rules() == {}


# ---------------------------------------------------------------- Memory page helpers


def test_rewording_a_rule_drops_banned_words_it_no_longer_mentions(app_db) -> None:
    key = store.add_style_rule(_rule("Avoid the word 'passionate'", ["passionate"]))

    my_data.update_style_rule(key, "Never say 'passionate' or 'eager'")
    assert store.list_style_rules()[key].avoid_phrases == ["passionate"]

    my_data.update_style_rule(key, "Keep sentences short")
    assert store.list_style_rules()[key].avoid_phrases == []


def test_user_can_add_a_rule_and_set_the_tone(app_db) -> None:
    store.save_preferences(store.Preferences(letter_length="short"))

    key = my_data.add_style_rule("  Keep the letter under 200 words ")
    my_data.set_tone("concise")

    assert store.list_style_rules()[key].source == "explicit"
    assert my_data.add_style_rule("   ") is None
    prefs = store.get_preferences()
    assert (prefs.tone, prefs.letter_length) == ("concise", "short")  # only the tone changed
