"""alembic-drift-check: detect schema drift between SQLAlchemy models and a live database."""

from alembic_drift_check.core import (
    DriftEntry,
    DriftResult,
    compute_drift,
    load_metadata,
)

__all__ = [
    "DriftEntry",
    "DriftResult",
    "compute_drift",
    "load_metadata",
    "__version__",
]

__version__ = "0.1.0"
