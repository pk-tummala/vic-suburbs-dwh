"""Integration smoke test for the Silver transforms, using a local Spark session.

Marked 'integration' and skipped automatically when pyspark is unavailable (e.g. plain CI
quality job). Exercises conform + dedup + DQ-expression evaluation on a tiny frame.
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


def test_conform_and_dq_expr_eval(spark):
    from pyspark.sql import functions as F

    from vic_suburbs.common.dq import rule_to_expr
    from vic_suburbs.common.transforms import conform_sal_code

    src = spark.createDataFrame(
        [("St Kilda", "3182", 100), ("Nowhere", "9999", 5)],
        ["suburb", "postcode", "median_house_price"],
    )
    crosswalk = spark.createDataFrame(
        [("ST KILDA", "3182", "SYN20004")], ["_norm_suburb", "postcode", "sal_code"]
    )
    conformed = conform_sal_code(src, crosswalk)
    expr = rule_to_expr({"type": "crosswalk_resolved", "column": "sal_code"})
    passed = conformed.where(F.expr(expr)).count()
    assert passed == 1  # St Kilda resolves; Nowhere does not
