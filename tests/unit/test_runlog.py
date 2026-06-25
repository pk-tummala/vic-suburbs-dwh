from unittest.mock import MagicMock

from vic_suburbs.common import runlog


def _sql_text(spark):
    return " ".join(c.args[0] for c in spark.sql.call_args_list)


def test_open_run_creates_tables_and_inserts_running():
    spark = MagicMock()
    runlog.open_run(spark, "cat", "dev", "run1", "vic_suburbs_job", "MANUAL")
    sql = _sql_text(spark)
    assert "CREATE TABLE IF NOT EXISTS cat.`05_metadata`.pipeline_run_log" in sql
    assert "CREATE TABLE IF NOT EXISTS cat.`05_metadata`.dq_results" in sql
    assert "INSERT INTO cat.`05_metadata`.pipeline_run_log" in sql
    assert "'RUNNING'" in sql and "'run1'" in sql


def test_close_run_success_nulls_error_columns():
    spark = MagicMock()
    runlog.close_run(spark, "cat", "run1", "SUCCESS", rows_read=3, rows_written=5)
    sql = spark.sql.call_args_list[-1].args[0]
    assert "status='SUCCESS'" in sql and "rows_written=5" in sql and "rows_read=3" in sql
    assert "error_class=NULL" in sql and "error_message=NULL" in sql


def test_close_run_failed_escapes_single_quotes():
    spark = MagicMock()
    runlog.close_run(
        spark,
        "cat",
        "run1",
        "FAILED",
        0,
        0,
        error_class="PYTHON.TYPE_ERROR",
        error_message="it's bad",
    )
    sql = spark.sql.call_args_list[-1].args[0]
    assert "error_class='PYTHON.TYPE_ERROR'" in sql
    assert "error_message='it''s bad'" in sql
