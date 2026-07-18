"""Command-line interface for alembic-drift-check."""

from __future__ import annotations

import argparse
import configparser
import json
import os
import sys
from pathlib import Path
from typing import Sequence

from alembic_drift_check import __version__
from alembic_drift_check.core import DriftError, DriftResult, compute_drift, load_metadata

EXIT_OK = 0
EXIT_DRIFT = 1
EXIT_USAGE = 2

_ATTRIBUTION = "checked by alembic-drift-check"


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="alembic-drift-check",
        description=(
            "Detect schema drift between your SQLAlchemy models and a live "
            "database using Alembic's autogenerate engine."
        ),
    )
    parser.add_argument(
        "--metadata",
        required=True,
        metavar="MODULE:ATTR",
        help=(
            "Import spec for the target MetaData or declarative Base, e.g. "
            "'myapp.models:Base.metadata' or 'myapp.models:target_metadata'."
        ),
    )
    parser.add_argument(
        "--database-url",
        default=None,
        metavar="URL",
        help=(
            "SQLAlchemy database URL. Falls back to $DATABASE_URL, then to "
            "sqlalchemy.url in alembic.ini."
        ),
    )
    parser.add_argument(
        "--alembic-ini",
        default="alembic.ini",
        metavar="PATH",
        help="Path to alembic.ini used to resolve sqlalchemy.url (default: alembic.ini).",
    )
    parser.add_argument(
        "--exclude",
        action="append",
        default=[],
        metavar="GLOB",
        help="Table-name glob to ignore (repeatable), e.g. --exclude 'tmp_*'.",
    )
    parser.add_argument(
        "--ignore-removed",
        action="store_true",
        help="Do not treat objects present in the DB but absent from the models as drift.",
    )
    parser.add_argument(
        "--generate-stub",
        action="store_true",
        help="Render an Alembic revision-script stub for the detected operations.",
    )
    parser.add_argument(
        "--stub-out",
        default=None,
        metavar="FILE",
        help="Write the generated stub to FILE instead of stdout (implies --generate-stub).",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit machine-readable JSON instead of a human-readable report.",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )
    return parser


def _resolve_database_url(args: argparse.Namespace) -> str:
    if args.database_url:
        return args.database_url
    env_url = os.environ.get("DATABASE_URL")
    if env_url:
        return env_url
    ini_path = Path(args.alembic_ini)
    if ini_path.is_file():
        parser = configparser.ConfigParser()
        parser.read(ini_path)
        url = parser.get("alembic", "sqlalchemy.url", fallback=None)
        if url:
            return url
    raise DriftError(
        "no database URL: pass --database-url, set $DATABASE_URL, or provide "
        "sqlalchemy.url in alembic.ini"
    )


def _print_human(result: DriftResult, url: str, out) -> None:
    if not result.has_drift:
        print("No schema drift detected: models and database are in sync.", file=out)
    else:
        count = len(result.entries)
        noun = "difference" if count == 1 else "differences"
        print(f"Schema drift detected: {count} {noun}.", file=out)
        print("", file=out)
        for entry in result.entries:
            marker = "-" if entry.is_removal else "+"
            print(f"  {marker} {entry.describe()}", file=out)
    if result.ignored_removals:
        print(
            f"\n({result.ignored_removals} removal(s) ignored via --ignore-removed)",
            file=out,
        )
    print(f"\n-- {_ATTRIBUTION}", file=out)


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    # Make the invocation directory importable (like Alembic's prepend_sys_path),
    # so specs such as 'myapp.models:Base.metadata' resolve when run from a project root.
    cwd = os.getcwd()
    if cwd not in sys.path:
        sys.path.insert(0, cwd)

    generate_stub = args.generate_stub or bool(args.stub_out)

    try:
        metadata = load_metadata(args.metadata)
        database_url = _resolve_database_url(args)
        result = compute_drift(
            metadata,
            database_url,
            exclude=args.exclude,
            ignore_removed=args.ignore_removed,
            generate_stub=generate_stub,
        )
    except DriftError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_USAGE

    if args.json:
        payload = result.to_dict()
        payload["database_url"] = database_url
        payload["attribution"] = _ATTRIBUTION
        print(json.dumps(payload, indent=2))
    else:
        _print_human(result, database_url, sys.stdout)

    if generate_stub and result.stub:
        if args.stub_out:
            Path(args.stub_out).write_text(result.stub, encoding="utf-8")
            if not args.json:
                print(f"\nStub written to {args.stub_out}", file=sys.stdout)
        elif not args.json:
            print("\n--- revision stub ---")
            print(result.stub)

    return EXIT_DRIFT if result.has_drift else EXIT_OK


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
