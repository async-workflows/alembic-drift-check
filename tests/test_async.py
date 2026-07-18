"""Async-engine path: uses aiosqlite so no external DB is required."""

from __future__ import annotations

import pytest

pytest.importorskip("aiosqlite")

from sqlalchemy.ext.asyncio import create_async_engine  # noqa: E402

from alembic_drift_check.core import compute_drift  # noqa: E402

from .conftest import Base  # noqa: E402


def _async_url(tmp_path, name="async.db"):
    return f"sqlite+aiosqlite:///{tmp_path / name}"


async def _create_all(url):
    engine = create_async_engine(url)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    await engine.dispose()


def test_async_url_in_sync(tmp_path):
    import asyncio

    url = _async_url(tmp_path)
    asyncio.run(_create_all(url))
    result = compute_drift(Base.metadata, url)
    assert not result.has_drift


def test_async_url_drift(tmp_path):
    # Empty async DB (nothing created) vs. models -> add_table drift.
    url = _async_url(tmp_path, "empty.db")
    # Touch the file by opening a connection so sqlite creates it.
    import asyncio

    async def _touch():
        engine = create_async_engine(url)
        async with engine.connect():
            pass
        await engine.dispose()

    asyncio.run(_touch())
    result = compute_drift(Base.metadata, url, generate_stub=True)
    assert result.has_drift
    assert any(e.kind == "add_table" for e in result.entries)
    assert result.stub is not None and "op." in result.stub
