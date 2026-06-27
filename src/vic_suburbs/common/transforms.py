"""Entity-agnostic Silver transforms: typing and dedup.

The PySpark-dependent functions import ``pyspark`` lazily so this module loads in a
plain Python environment (tests, generator). The pure helper ``build_cast_plan`` is
unit-testable without Spark.
"""

from __future__ import annotations

from typing import Any

# ── Pure helpers (testable without Spark) ────────────────────────────────────

_SPARK_TYPES = {"string", "int", "bigint", "double", "boolean", "date", "timestamp"}


def build_cast_plan(schema: dict[str, Any]) -> list[tuple[str, str]]:
    """Return ``[(column, spark_type), ...]`` from a schema config, validating types."""
    plan = []
    for col in schema["columns"]:
        t = col["type"]
        if t not in _SPARK_TYPES:
            raise ValueError(f"Unsupported type {t!r} for column {col['name']!r}")
        plan.append((col["name"], t))
    return plan


# ── Spark transforms (imported lazily) ───────────────────────────────────────


def cast_to_schema(df, schema: dict[str, Any]):  # pragma: no cover
    """Select + cast a DataFrame to the configured schema (extra columns dropped)."""
    from pyspark.sql import functions as F  # noqa: N812

    plan = build_cast_plan(schema)
    return df.select(*[F.col(c).cast(t).alias(c) for c, t in plan])


def dedup_latest(
    df, keys: list[str], order_col: str, tiebreak: list[str] | None = None
):  # pragma: no cover
    """Keep one row per key set — the latest by ``order_col`` (descending).

    ``tiebreak`` columns (also descending) deterministically resolve rows that share the same
    ``order_col`` value — e.g. an original row and its restatement that land in the *same* batch
    with identical ingest timestamps. Without a tiebreak ``row_number`` would pick one arbitrarily.
    """
    from pyspark.sql import Window
    from pyspark.sql import functions as F  # noqa: N812

    order = [F.col(order_col).desc(), *(F.col(c).desc() for c in (tiebreak or []))]
    w = Window.partitionBy(*keys).orderBy(*order)
    return df.withColumn("_rn", F.row_number().over(w)).where(F.col("_rn") == 1).drop("_rn")
