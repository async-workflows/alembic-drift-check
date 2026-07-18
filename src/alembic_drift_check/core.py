"""Core drift-detection logic.

This module wraps Alembic's own autogenerate machinery
(:func:`alembic.autogenerate.compare_metadata`) so the drift we report is exactly
what ``alembic revision --autogenerate`` would produce, nothing more or less.
"""

from __future__ import annotations

import asyncio
import fnmatch
import importlib
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Iterator, Sequence

from alembic.autogenerate import compare_metadata, produce_migrations
from alembic.migration import MigrationContext
from sqlalchemy import MetaData, create_engine
from sqlalchemy.engine import Connection, make_url

try:  # render_python_code is public in modern Alembic; guard just in case.
    from alembic.autogenerate import render_python_code

    _HAS_RENDER = True
except ImportError:  # pragma: no cover - defensive fallback
    render_python_code = None  # type: ignore[assignment]
    _HAS_RENDER = False


# Diff directive names that represent something being dropped from the DB
# (i.e. present in the live database but absent from the models).
_REMOVAL_PREFIX = "remove_"


class DriftError(Exception):
    """Raised for user/config errors (bad import spec, bad URL, etc.)."""


@dataclass
class DriftEntry:
    """A single classified difference between the models and the database."""

    kind: str
    table: str | None
    detail: str | None
    is_removal: bool

    def describe(self) -> str:
        target = self.table or "?"
        if self.detail:
            target = f"{target}.{self.detail}"
        return f"{self.kind}: {target}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "table": self.table,
            "detail": self.detail,
            "is_removal": self.is_removal,
        }


@dataclass
class DriftResult:
    """The outcome of a drift comparison."""

    entries: list[DriftEntry] = field(default_factory=list)
    stub: str | None = None
    ignored_removals: int = 0

    @property
    def has_drift(self) -> bool:
        return bool(self.entries)

    def to_dict(self) -> dict[str, Any]:
        return {
            "has_drift": self.has_drift,
            "entries": [e.to_dict() for e in self.entries],
            "ignored_removals": self.ignored_removals,
            "stub": self.stub,
        }


def load_metadata(spec: str) -> MetaData:
    """Load a :class:`~sqlalchemy.MetaData` from an import spec.

    The spec is ``module.path:attribute`` where ``attribute`` is either a
    :class:`~sqlalchemy.MetaData` instance or a declarative ``Base`` (anything
    exposing a ``.metadata`` attribute), e.g. ``myapp.models:Base.metadata`` or
    ``myapp.models:target_metadata``.
    """
    if ":" not in spec:
        raise DriftError(
            f"invalid --metadata spec {spec!r}; expected 'module.path:attribute' "
            "(e.g. 'myapp.models:Base.metadata')"
        )
    module_name, _, attr_path = spec.partition(":")
    try:
        module = importlib.import_module(module_name)
    except ImportError as exc:
        raise DriftError(f"could not import module {module_name!r}: {exc}") from exc

    obj: Any = module
    for part in attr_path.split("."):
        try:
            obj = getattr(obj, part)
        except AttributeError as exc:
            raise DriftError(
                f"attribute {attr_path!r} not found on module {module_name!r}"
            ) from exc

    if isinstance(obj, MetaData):
        return obj
    metadata = getattr(obj, "metadata", None)
    if isinstance(metadata, MetaData):
        return metadata
    raise DriftError(
        f"{spec!r} did not resolve to a MetaData or a declarative Base "
        f"(got {type(obj).__name__})"
    )


def _make_include_object(
    exclude: Sequence[str],
) -> Callable[[Any, str, str, bool, Any], bool]:
    """Build an Alembic ``include_object`` filter honouring ``--exclude`` globs."""

    patterns = list(exclude)

    def include_object(
        obj: Any, name: str, type_: str, reflected: bool, compare_to: Any
    ) -> bool:
        if not patterns:
            return True
        if type_ == "table":
            table_name = name
        else:
            table = getattr(obj, "table", None)
            table_name = getattr(table, "name", None)
        if table_name is not None:
            for pattern in patterns:
                if fnmatch.fnmatch(table_name, pattern):
                    return False
        return True

    return include_object


def _flatten(diffs: Iterable[Any]) -> Iterator[tuple]:
    """Alembic groups column-level diffs into sublists; flatten to plain tuples."""
    for diff in diffs:
        if isinstance(diff, list):
            yield from _flatten(diff)
        else:
            yield diff


def _classify(diff: tuple) -> DriftEntry:
    kind = diff[0]
    is_removal = kind.startswith(_REMOVAL_PREFIX)
    table: str | None = None
    detail: str | None = None

    if kind in ("add_table", "remove_table"):
        table = getattr(diff[1], "name", None)
    elif kind in ("add_column", "remove_column"):
        # (kind, schema, table_name, column)
        table = diff[2]
        detail = getattr(diff[3], "name", None)
    elif kind in ("add_index", "remove_index"):
        index = diff[1]
        table = getattr(getattr(index, "table", None), "name", None)
        detail = getattr(index, "name", None)
    elif kind in (
        "add_constraint",
        "remove_constraint",
        "add_fk",
        "remove_fk",
        "add_unique_constraint",
        "remove_unique_constraint",
    ):
        constraint = diff[1]
        table = getattr(getattr(constraint, "table", None), "name", None)
        detail = getattr(constraint, "name", None)
    elif kind.startswith("modify_"):
        # (kind, schema, table_name, col_name, existing_kw, old, new)
        if len(diff) >= 4:
            table = diff[2]
            detail = diff[3]
    else:  # pragma: no cover - forward-compatibility for new directive kinds
        # Best effort: try to find a name somewhere in the tuple.
        for item in diff[1:]:
            named = getattr(item, "name", None)
            if named:
                table = named
                break

    return DriftEntry(kind=kind, table=table, detail=detail, is_removal=is_removal)


def _render_stub(migration_context: MigrationContext, metadata: MetaData) -> str:
    """Render an Alembic upgrade() body for the detected operations."""
    migrations = produce_migrations(migration_context, metadata)
    upgrade_ops = migrations.upgrade_ops
    if upgrade_ops is None or upgrade_ops.is_empty():
        return ""
    if _HAS_RENDER:
        try:
            return render_python_code(
                upgrade_ops, migration_context=migration_context
            )
        except Exception:  # pragma: no cover - fall through to best effort
            pass
    # Best-effort fallback if render_python_code is unavailable. Follow the same
    # convention as render_python_code: first line flush-left, rest indented by 4.
    lines = [repr(op) for op in upgrade_ops.ops]
    return "\n    ".join(lines)


def _wrap_stub(body: str) -> str:
    """Wrap a rendered upgrade body in a copy-pasteable revision-script skeleton.

    ``render_python_code`` emits the ``upgrade()`` body with the first line
    flush-left and every following line already indented by 4 spaces (a Mako
    template convention). Prepending 4 spaces to the whole string therefore
    indents only that first line, yielding a uniformly indented function body.
    """
    body = body.rstrip("\n")
    indented = "    " + body if body else "    pass"
    return (
        '"""drift detected by alembic-drift-check\n\n'
        "Revision stub for operations present in your models but missing from the "
        'database.\n"""\n'
        "from alembic import op\n"
        "import sqlalchemy as sa\n\n\n"
        "def upgrade() -> None:\n"
        f"{indented}\n"
    )


def _do_compare(
    connection: Connection,
    metadata: MetaData,
    exclude: Sequence[str],
    generate_stub: bool,
) -> tuple[list[tuple], str | None]:
    context = MigrationContext.configure(
        connection,
        opts={
            "compare_type": True,
            "compare_server_default": True,
            "target_metadata": metadata,
            "include_object": _make_include_object(exclude),
        },
    )
    diffs = list(_flatten(compare_metadata(context, metadata)))
    stub: str | None = None
    if generate_stub:
        body = _render_stub(context, metadata)
        stub = _wrap_stub(body) if body else None
    return diffs, stub


def _url_is_async(url: str) -> bool:
    try:
        dialect = make_url(url).get_dialect()
    except Exception as exc:  # noqa: BLE001
        raise DriftError(f"invalid database URL: {exc}") from exc
    return bool(getattr(dialect, "is_async", False))


def compute_drift(
    metadata: MetaData,
    database_url: str,
    *,
    exclude: Sequence[str] = (),
    ignore_removed: bool = False,
    generate_stub: bool = False,
) -> DriftResult:
    """Compare ``metadata`` against the live schema at ``database_url``.

    Chooses a sync or async engine automatically based on the URL's driver.
    Returns a :class:`DriftResult`; a truthy ``result.has_drift`` means the
    models and the database disagree.
    """
    if _url_is_async(database_url):
        diffs, stub = _compare_async(metadata, database_url, exclude, generate_stub)
    else:
        diffs, stub = _compare_sync(metadata, database_url, exclude, generate_stub)

    entries = [_classify(diff) for diff in diffs]
    ignored = 0
    if ignore_removed:
        kept = [e for e in entries if not e.is_removal]
        ignored = len(entries) - len(kept)
        entries = kept

    return DriftResult(entries=entries, stub=stub, ignored_removals=ignored)


def _compare_sync(
    metadata: MetaData,
    database_url: str,
    exclude: Sequence[str],
    generate_stub: bool,
) -> tuple[list[tuple], str | None]:
    engine = create_engine(database_url)
    try:
        with engine.connect() as connection:
            return _do_compare(connection, metadata, exclude, generate_stub)
    finally:
        engine.dispose()


def _compare_async(
    metadata: MetaData,
    database_url: str,
    exclude: Sequence[str],
    generate_stub: bool,
) -> tuple[list[tuple], str | None]:
    from sqlalchemy.ext.asyncio import create_async_engine

    async def _run() -> tuple[list[tuple], str | None]:
        engine = create_async_engine(database_url)
        try:
            async with engine.connect() as connection:
                return await connection.run_sync(
                    lambda sync_conn: _do_compare(
                        sync_conn, metadata, exclude, generate_stub
                    )
                )
        finally:
            await engine.dispose()

    return asyncio.run(_run())
