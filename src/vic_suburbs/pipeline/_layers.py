"""Per-layer schema placement for the multi-schema Lakeflow pipeline.

One pipeline publishes to several schemas (default UC publishing mode): Bronze ``raw_*`` land in
``01_bronze``, Silver tables/changes in ``02_silver``, and Gold dims+facts in ``03_gold``. Tables
are defined and referenced by their fully-qualified name; the leading-digit schema names are
backtick-quoted.
"""

from __future__ import annotations

LAYER_SCHEMA = {
    "bronze": "01_bronze",
    "silver": "02_silver",
    "gold": "03_gold",
    "reporting": "04_reporting",
    "metadata": "05_metadata",
}


def fqn(catalog: str, layer: str, table: str) -> str:
    """Backtick-quoted ``catalog.schema.table`` for the given medallion layer."""
    return f"`{catalog}`.`{LAYER_SCHEMA[layer]}`.`{table}`"
