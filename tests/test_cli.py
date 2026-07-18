"""Tests for the CLI: exit codes, JSON shape, stub emission, metadata specs."""

from __future__ import annotations

import json

import pytest

from alembic_drift_check.cli import EXIT_DRIFT, EXIT_OK, EXIT_USAGE, main

META = "tests.conftest:Base.metadata"


def test_cli_in_sync_exit_zero(in_sync_db, capsys):
    code = main(["--metadata", META, "--database-url", in_sync_db])
    assert code == EXIT_OK
    out = capsys.readouterr().out
    assert "in sync" in out
    assert "checked by alembic-drift-check" in out


def test_cli_drift_exit_one(missing_column_db, capsys):
    code = main(["--metadata", META, "--database-url", missing_column_db])
    assert code == EXIT_DRIFT
    out = capsys.readouterr().out
    assert "drift detected" in out.lower()


def test_cli_json_shape(missing_column_db, capsys):
    code = main(["--metadata", META, "--database-url", missing_column_db, "--json"])
    assert code == EXIT_DRIFT
    payload = json.loads(capsys.readouterr().out)
    assert payload["has_drift"] is True
    assert isinstance(payload["entries"], list)
    assert payload["entries"]
    entry = payload["entries"][0]
    assert {"kind", "table", "detail", "is_removal"} <= set(entry)
    assert payload["attribution"] == "checked by alembic-drift-check"


def test_cli_ignore_removed(extra_table_db, capsys):
    code = main(
        ["--metadata", META, "--database-url", extra_table_db, "--ignore-removed"]
    )
    assert code == EXIT_OK


def test_cli_generate_stub_stdout(missing_column_db, capsys):
    code = main(
        ["--metadata", META, "--database-url", missing_column_db, "--generate-stub"]
    )
    assert code == EXIT_DRIFT
    out = capsys.readouterr().out
    assert "revision stub" in out
    assert "op." in out


def test_cli_stub_out_file(missing_column_db, tmp_path, capsys):
    stub_file = tmp_path / "stub.py"
    code = main(
        [
            "--metadata",
            META,
            "--database-url",
            missing_column_db,
            "--stub-out",
            str(stub_file),
        ]
    )
    assert code == EXIT_DRIFT
    content = stub_file.read_text()
    assert "def upgrade()" in content
    assert "op." in content


def test_cli_metadata_import_spec(in_sync_db, capsys):
    # Load via the standalone fixture module; DB lacks 'widgets' -> drift.
    code = main(["--metadata", "tests.fixture_models:target_metadata",
                 "--database-url", in_sync_db])
    assert code == EXIT_DRIFT


def test_cli_bad_url_is_usage_error(capsys):
    code = main(["--metadata", META, "--database-url", "not a url"])
    assert code == EXIT_USAGE
    err = capsys.readouterr().err
    assert "error:" in err


def test_cli_no_url_is_usage_error(monkeypatch, tmp_path, capsys):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.chdir(tmp_path)  # no alembic.ini here
    code = main(["--metadata", META])
    assert code == EXIT_USAGE
