# What: shared pytest fixtures.
# Why: tests must never touch the real database in data/runtime/, so each test gets a fresh
#      in-memory SQLite database that disappears when the test ends.
# Used by: every test module in tests/.

from collections.abc import Iterator

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, create_engine

from app.db.repo import init_db


@pytest.fixture
def session() -> Iterator[Session]:
    """A database session on an empty in-memory database with all tables created."""
    # StaticPool keeps one connection, so the in-memory database lives for the whole test.
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    init_db(engine)
    with Session(engine, expire_on_commit=False) as s:
        yield s
