"""Lineage helpers shared by the generator and the pipeline."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

# Lineage columns carried from landing through to Gold facts.
LINEAGE_COLUMNS = ("source_system", "batch_id", "ingested_at")

VALID_SOURCE_SYSTEMS = {"SYNTHETIC"}


def new_batch_id() -> str:
    """Mint a fresh batch id (UUID4) for one generation batch."""
    return str(uuid.uuid4())


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def validate_source_system(source_system: str) -> str:
    if source_system not in VALID_SOURCE_SYSTEMS:
        raise ValueError(f"source_system {source_system!r} not in {sorted(VALID_SOURCE_SYSTEMS)}")
    return source_system
