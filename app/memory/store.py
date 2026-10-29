# What: long-term memory: the user's profile, preferences, confirmed facts, style rules and
#       cached company research, saved in a LangGraph Store (`data/runtime/memory.db`).
# Why: these facts must survive restarts and be shared by every job and chat. The checkpointer
#      only remembers one conversation (thread); the Store remembers the user across all of them.
# Used by: app/services/onboarding.py, app/services/my_data.py (Memory page: edit, delete,
#          delete all), app/main.py, the graphs (style rules, facts),
#          app/services/company.py (research cache),
#          the copilot's system prompt and the Memory page.

import sqlite3
import uuid
from datetime import UTC, datetime
from functools import lru_cache
from typing import Literal, TypeVar

from langgraph.store.base import BaseStore
from langgraph.store.sqlite import SqliteStore
from pydantic import BaseModel, Field

from app.agent.state import StyleRule
from app.config import get_settings

# Single local user for now. Every namespace starts with the user id, so supporting several
# users later means passing a different id, not redesigning the storage.
DEFAULT_USER_ID = "local"
_MAIN_KEY = "main"
_SEARCH_LIMIT = 1000  # store.search() returns only 10 items by default

M = TypeVar("M", bound=BaseModel)


class Profile(BaseModel):
    """Who the user is and what they are looking for (onboarding step 1)."""

    name: str
    target_roles: list[str] = Field(default_factory=list)
    seniority: Literal["intern", "junior", "mid", "senior", "lead"] = "junior"
    locations: list[str] = Field(default_factory=list)
    work_authorisation: str = ""  # e.g. "EU citizen", "Needs visa sponsorship"
    languages: list[str] = Field(default_factory=list)  # e.g. ["English C1", "German A2"]
    salary_expectations: str | None = None


class Preferences(BaseModel):
    """How the user wants their applications written (onboarding step 3)."""

    tone: Literal["formal", "friendly", "concise"] = "friendly"
    letter_length: Literal["short", "medium", "long"] = "medium"
    letter_language: str = "Same as the job posting"
    deal_breakers: list[str] = Field(default_factory=list)


class Fact(BaseModel):
    """A fact about the user they confirmed in a claim check, e.g. "Led a 3-person team"."""

    text: str
    job_id: int | None = None  # the job whose claim check produced it
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


@lru_cache
def get_store() -> BaseStore:
    """The app's long-term memory store (opened once per process)."""
    conn = sqlite3.connect(
        get_settings().memory_db_path,
        check_same_thread=False,  # Streamlit may call from different threads
        isolation_level=None,  # autocommit, as SqliteStore expects
    )
    store = SqliteStore(conn)
    store.setup()  # creates the tables on first use
    return store


def _ns(kind: str, user_id: str) -> tuple[str, ...]:
    """Namespace for one kind of memory, e.g. ("local", "profile")."""
    return (user_id, kind)


# ---------------------------------------------------------------- profile and preferences


def get_profile(store: BaseStore | None = None, user_id: str = DEFAULT_USER_ID) -> Profile | None:
    """Return the saved profile, or None if onboarding hasn't been done yet."""
    item = (store or get_store()).get(_ns("profile", user_id), _MAIN_KEY)
    return Profile.model_validate(item.value) if item else None


def save_profile(
    profile: Profile, store: BaseStore | None = None, user_id: str = DEFAULT_USER_ID
) -> None:
    """Save (or overwrite) the user's profile."""
    (store or get_store()).put(_ns("profile", user_id), _MAIN_KEY, profile.model_dump(mode="json"))


def delete_profile(store: BaseStore | None = None, user_id: str = DEFAULT_USER_ID) -> None:
    """Forget the profile (the onboarding wizard is shown again)."""
    (store or get_store()).delete(_ns("profile", user_id), _MAIN_KEY)


def get_preferences(store: BaseStore | None = None, user_id: str = DEFAULT_USER_ID) -> Preferences:
    """Return the saved preferences, or the defaults if none are saved."""
    item = (store or get_store()).get(_ns("preferences", user_id), _MAIN_KEY)
    return Preferences.model_validate(item.value) if item else Preferences()


def save_preferences(
    preferences: Preferences, store: BaseStore | None = None, user_id: str = DEFAULT_USER_ID
) -> None:
    """Save (or overwrite) the user's preferences."""
    (store or get_store()).put(
        _ns("preferences", user_id), _MAIN_KEY, preferences.model_dump(mode="json")
    )


def delete_preferences(store: BaseStore | None = None, user_id: str = DEFAULT_USER_ID) -> None:
    """Forget the preferences (the defaults apply again)."""
    (store or get_store()).delete(_ns("preferences", user_id), _MAIN_KEY)


# ---------------------------------------------------------------- facts and style rules


def _add(kind: str, value: BaseModel, store: BaseStore | None, user_id: str) -> str:
    key = str(uuid.uuid4())
    (store or get_store()).put(_ns(kind, user_id), key, value.model_dump(mode="json"))
    return key


def _list(kind: str, model: type[M], store: BaseStore | None, user_id: str) -> dict[str, M]:
    items = (store or get_store()).search(_ns(kind, user_id), limit=_SEARCH_LIMIT)
    parsed = {item.key: model.model_validate(item.value) for item in items}
    # Oldest first, so the Memory page shows them in the order they were learned.
    return dict(sorted(parsed.items(), key=lambda kv: kv[1].created_at))


def add_fact(fact: Fact, store: BaseStore | None = None, user_id: str = DEFAULT_USER_ID) -> str:
    """Remember a confirmed fact. Returns its key (needed to delete it)."""
    return _add("facts", fact, store, user_id)


def list_facts(store: BaseStore | None = None, user_id: str = DEFAULT_USER_ID) -> dict[str, Fact]:
    """Return all confirmed facts as {key: Fact}, oldest first."""
    return _list("facts", Fact, store, user_id)


def update_fact(
    key: str, text: str, store: BaseStore | None = None, user_id: str = DEFAULT_USER_ID
) -> None:
    """Change the wording of a fact; keeps its job and date. Raises KeyError if it's gone."""
    store = store or get_store()
    item = store.get(_ns("facts", user_id), key)
    if item is None:
        raise KeyError(key)
    fact = Fact.model_validate(item.value).model_copy(update={"text": text})
    store.put(_ns("facts", user_id), key, fact.model_dump(mode="json"))


def delete_fact(key: str, store: BaseStore | None = None, user_id: str = DEFAULT_USER_ID) -> None:
    """Forget one fact."""
    (store or get_store()).delete(_ns("facts", user_id), key)


def add_style_rule(
    rule: StyleRule, store: BaseStore | None = None, user_id: str = DEFAULT_USER_ID
) -> str:
    """Remember a writing style rule. Returns its key (needed to undo or delete it)."""
    return _add("style_rules", rule, store, user_id)


def list_style_rules(
    store: BaseStore | None = None, user_id: str = DEFAULT_USER_ID
) -> dict[str, StyleRule]:
    """Return all style rules as {key: StyleRule}, oldest first."""
    return _list("style_rules", StyleRule, store, user_id)


def update_style_rule(
    key: str, text: str, store: BaseStore | None = None, user_id: str = DEFAULT_USER_ID
) -> None:
    """Change the wording of a style rule. Raises KeyError if it's gone.

    Banned words the new wording no longer mentions are dropped, so rewording "Avoid
    'passionate'" into "Keep sentences short" stops the guard from checking "passionate".
    """
    store = store or get_store()
    item = store.get(_ns("style_rules", user_id), key)
    if item is None:
        raise KeyError(key)
    rule = StyleRule.model_validate(item.value)
    kept = [p for p in rule.avoid_phrases if p.lower() in text.lower()]
    rule = rule.model_copy(update={"rule": text, "avoid_phrases": kept})
    store.put(_ns("style_rules", user_id), key, rule.model_dump(mode="json"))


def delete_style_rule(
    key: str, store: BaseStore | None = None, user_id: str = DEFAULT_USER_ID
) -> None:
    """Forget one style rule."""
    (store or get_store()).delete(_ns("style_rules", user_id), key)


def delete_all_memories(store: BaseStore | None = None, user_id: str = DEFAULT_USER_ID) -> None:
    """Delete everything remembered about the user ("Delete all my data")."""
    store = store or get_store()
    while items := store.search((user_id,), limit=_SEARCH_LIMIT):
        for item in items:
            store.delete(item.namespace, item.key)


# ---------------------------------------------------------------- company research cache
# Not a memory about the user, but it belongs to the user's data, is shared by all pages, and
# must survive restarts. Keeping it here means "Delete all my data" removes it too.


def save_company_research(
    job_id: int, research: dict, store: BaseStore | None = None, user_id: str = DEFAULT_USER_ID
) -> None:
    """Remember the company research for one job (JSON-safe dict)."""
    (store or get_store()).put(_ns("company_research", user_id), str(job_id), research)


def get_company_research(
    job_id: int, store: BaseStore | None = None, user_id: str = DEFAULT_USER_ID
) -> dict | None:
    """The saved company research for one job, or None."""
    item = (store or get_store()).get(_ns("company_research", user_id), str(job_id))
    return item.value if item else None


def delete_company_research(
    job_id: int, store: BaseStore | None = None, user_id: str = DEFAULT_USER_ID
) -> None:
    """Forget the company research for one job (when the job is deleted)."""
    (store or get_store()).delete(_ns("company_research", user_id), str(job_id))
