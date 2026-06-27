"""Integration smoke test for the Silver transforms, using a local Spark session.

Marked 'integration' and skipped automatically when pyspark is unavailable (e.g. plain CI
quality job). Exercises dedup + DQ-expression evaluation on a tiny frame.
"""

import pytest

pyspark = pytest.importorskip("pyspark")
pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def spark():
    from pyspark.sql import SparkSession

    s = SparkSession.builder.master("local[1]").appName("vic-test").getOrCreate()
    yield s
    s.stop()


def test_dedup_latest_and_dq_expr_eval(spark):
    from pyspark.sql import functions as F

    from vic_suburbs.common.dq import rule_to_expr
    from vic_suburbs.common.transforms import dedup_latest

    # Two versions of one grain (sal_code, period): the later ingested_at restatement wins.
    # A third row has a NULL sal_code and is dropped by the not_null DQ expression.
    src = spark.createDataFrame(
        [
            ("SYN20004", 2020, 100, "2024-01-01", "crime.csv"),
            ("SYN20004", 2020, 175, "2024-01-02", "crime_upd.csv"),
            (None, 2020, 5, "2024-01-01", "crime.csv"),
        ],
        ["sal_code", "period", "offence_count_total", "ingested_at", "source_file"],
    )
    deduped = dedup_latest(
        src, keys=["sal_code", "period"], order_col="ingested_at", tiebreak=["source_file"]
    )
    expr = rule_to_expr({"type": "not_null", "column": "sal_code"})
    passed = deduped.where(F.expr(expr)).count()
    assert passed == 1  # one row per grain survives; the NULL-sal_code grain fails the DQ expr
