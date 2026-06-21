"""Entity-agnostic Silver transforms: typing, suburb-name conforming, dedup.

The PySpark-dependent functions import ``pyspark`` lazily so this module loads in a
plain Python environment (tests, generator). The pure helpers — ``build_cast_plan`` and
``normalize_suburb_name`` — are unit-testable without Spark.
"""

from __future__ import annotations

import re
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


_WS = re.compile(r"\s+")


def normalize_suburb_name(name: str | None) -> str:
    """Normalise a free-text suburb name for crosswalk matching."""
    if name is None:
        return ""
    n = _WS.sub(" ", name.strip()).upper()
    # common source noise
    n = n.replace(".", "").replace("’", "'")
    return n


# ── Spark transforms (imported lazily) ───────────────────────────────────────


def cast_to_schema(df, schema: dict[str, Any]):
    """Select + cast a DataFrame to the configured schema (extra columns dropped)."""
    from pyspark.sql import functions as F  # noqa: N812

    plan = build_cast_plan(schema)
    return df.select(*[F.col(c).cast(t).alias(c) for c, t in plan])


def conform_sal_code(df, crosswalk_df):
    """Resolve ``sal_code`` for free-text sources by joining a (norm_name, postcode) crosswalk.

    Rows already carrying ``sal_code`` (e.g. ABS sources, synthetic spine) pass through.
    Unresolved rows keep ``sal_code = NULL`` and are caught by the ``crosswalk_resolved`` DQ rule.
    """
    from pyspark.sql import functions as F  # noqa: N812

    if "sal_code" in df.columns:
        return df
    norm = F.upper(F.trim(F.regexp_replace(F.col("suburb"), r"\s+", " ")))
    joined = df.withColumn("_norm_suburb", norm).join(
        crosswalk_df, on=["_norm_suburb", "postcode"], how="left"
    )
    return joined.drop("_norm_suburb")


def dedup_latest(df, keys: list[str], order_col: str):
    """Keep one row per key set — the latest by ``order_col``."""
    from pyspark.sql import Window
    from pyspark.sql import functions as F  # noqa: N812

    w = Window.partitionBy(*keys).orderBy(F.col(order_col).desc())
    return df.withColumn("_rn", F.row_number().over(w)).where(F.col("_rn") == 1).drop("_rn")
