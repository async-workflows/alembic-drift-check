"""Tests for the drift-detection core."""

from __future__ import annotations

import pytest

from alembic_drift_check.core import DriftError, compute_drift, load_metadata

from .conftest import Base


def test_in_sync_reports_no_drift(in_sync_db):
    result = compute_drift(Base.metadata, in_sync_db)
    assert not result.has_drift
    assert result.entries == []


def test_missing_column_and_table_detected(missing_column_db):
    result = compute_drift(Base.metadata, missing_column_db)
    assert result.has_drift
    kinds = {e.kind for e in result.entries}
    # posts table is in the models but not the DB.
    assert "add_table" in kinds
    # users.full_name is in the models but not the DB.
    assert "add_column" in kinds

    add_columns = [e for e in result.entries if e.kind == "add_column"]
    assert any(e.table == "users" and e.detail == "full_name" for e in add_columns)


def test_extra_table_is_a_removal(extra_table_db):
    result = compute_drift(Base.metadata, extra_table_db)
    assert result.has_drift
    remove_tables = [e for e in result.entries if e.kind == "remove_table"]
    assert any(e.table == "legacy_audit" for e in remove_tables)
    assert all(e.is_removal for e in remove_tables)


def test_ignore_removed_drops_removals(extra_table_db):
    result = compute_drift(Base.metadata, extra_table_db, ignore_removed=True)
    # The only diff was an extra table (a removal), so ignoring removals clears drift.
    assert not result.has_drift
    assert result.ignored_removals == 1


def test_exclude_glob_skips_table(extra_table_db):
    result = compute_drift(Base.metadata, extra_table_db, exclude=["legacy_*"])
    assert not result.has_drift


def test_generate_stub_mentions_drifted_object(missing_column_db):
    result = compute_drift(Base.metadata, missing_column_db, generate_stub=True)
    assert result.stub is not None
    assert "def upgrade()" in result.stub
    assert "op." in result.stub
    # The missing table/column should be referenced in the stub.
    assert "posts" in result.stub or "full_name" in result.stub


def test_load_metadata_from_base_attr():
    md = load_metadata("tests.fixture_models:Base.metadata")
    assert "widgets" in md.tables


def test_load_metadata_from_target_metadata_alias():
    md = load_metadata("tests.fixture_models:target_metadata")
    assert "widgets" in md.tables


def test_load_metadata_bad_spec_raises():
    with pytest.raises(DriftError):
        load_metadata("no_colon_here")


def test_load_metadata_missing_attr_raises():
    with pytest.raises(DriftError):
        load_metadata("tests.fixture_models:DoesNotExist")
