"""Shared pytest fixtures: an in-model declarative Base plus DB builders."""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import (
    Column,
    Integer,
    MetaData,
    String,
    Table,
    create_engine,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """The 'source of truth' models the DB is compared against."""


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email: Mapped[str] = mapped_column(String(255), nullable=False)
    full_name: Mapped[str] = mapped_column(String(120), nullable=True)


class Post(Base):
    __tablename__ = "posts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    title: Mapped[str] = mapped_column(String(200), nullable=False)


def _sqlite_url(path: Path) -> str:
    return f"sqlite:///{path}"


@pytest.fixture
def in_sync_db(tmp_path) -> str:
    """A sqlite DB whose schema exactly matches the models -> no drift."""
    db_path = tmp_path / "in_sync.db"
    engine = create_engine(_sqlite_url(db_path))
    Base.metadata.create_all(engine)
    engine.dispose()
    return _sqlite_url(db_path)


@pytest.fixture
def missing_column_db(tmp_path) -> str:
    """A sqlite DB where 'users' lacks the 'full_name' column and 'posts' is absent."""
    db_path = tmp_path / "drifted.db"
    engine = create_engine(_sqlite_url(db_path))
    metadata = MetaData()
    # users without full_name; posts table missing entirely.
    Table(
        "users",
        metadata,
        Column("id", Integer, primary_key=True),
        Column("email", String(255), nullable=False),
    )
    metadata.create_all(engine)
    engine.dispose()
    return _sqlite_url(db_path)


@pytest.fixture
def extra_table_db(tmp_path) -> str:
    """A sqlite DB matching the models plus one extra table not in the models."""
    db_path = tmp_path / "extra.db"
    engine = create_engine(_sqlite_url(db_path))
    Base.metadata.create_all(engine)
    metadata = MetaData()
    Table(
        "legacy_audit",
        metadata,
        Column("id", Integer, primary_key=True),
        Column("note", String(50)),
    )
    metadata.create_all(engine)
    engine.dispose()
    return _sqlite_url(db_path)
