# What: tests for long-term memory (app/memory/store.py).
# Why: the profile must survive a restart, and facts/style rules must be addable, listable and
#      deletable, because the user controls their own data.
# Used by: pytest.

import sqlite3
from pathlib import Path

import pytest
from langgraph.store.memory import InMemoryStore
from langgraph.store.sqlite import SqliteStore

from app.agent.state import StyleRule
from app.memory import store as memory
from app.memory.store import Fact, Preferences, Profile


@pytest.fixture
def mem() -> InMemoryStore:
    return InMemoryStore()


def test_profile_is_none_before_onboarding(mem: InMemoryStore) -> None:
    assert memory.get_profile(mem) is None


def test_profile_round_trip(mem: InMemoryStore) -> None:
    profile = Profile(name="Alex Novak", languages=["English C1", "German A2"])
    memory.save_profile(profile, mem)

    assert memory.get_profile(mem) == profile


def test_preferences_default_and_save(mem: InMemoryStore) -> None:
    assert memory.get_preferences(mem).tone == "friendly"

    memory.save_preferences(Preferences(tone="concise", deal_breakers=["No relocation"]), mem)

    assert memory.get_preferences(mem).tone == "concise"


def test_facts_add_list_delete(mem: InMemoryStore) -> None:
    key_a = memory.add_fact(Fact(text="Led a team of 3 dispatchers"), mem)
    memory.add_fact(Fact(text="Built DocuMind", job_id=1), mem)

    assert [f.text for f in memory.list_facts(mem).values()] == [
        "Led a team of 3 dispatchers",
        "Built DocuMind",
    ]
    memory.delete_fact(key_a, mem)
    assert [f.text for f in memory.list_facts(mem).values()] == ["Built DocuMind"]


def test_style_rules_and_delete_all(mem: InMemoryStore) -> None:
    memory.save_profile(Profile(name="Alex"), mem)
    memory.add_style_rule(StyleRule(rule="Avoid the word 'passionate'", source="edit_diff"), mem)

    assert [r.rule for r in memory.list_style_rules(mem).values()] == [
        "Avoid the word 'passionate'"
    ]
    memory.delete_all_memories(mem)
    assert memory.get_profile(mem) is None
    assert memory.list_style_rules(mem) == {}


def test_users_are_kept_apart(mem: InMemoryStore) -> None:
    memory.save_profile(Profile(name="Alex"), mem, user_id="alex")

    assert memory.get_profile(mem, user_id="sam") is None


def test_profile_survives_restart(tmp_path: Path) -> None:
    db = tmp_path / "memory.db"

    def open_store() -> SqliteStore:
        s = SqliteStore(sqlite3.connect(db, check_same_thread=False, isolation_level=None))
        s.setup()
        return s

    first = open_store()
    memory.save_profile(Profile(name="Alex Novak"), first)
    first.conn.close()  # "restart" the app

    assert memory.get_profile(open_store()).name == "Alex Novak"
