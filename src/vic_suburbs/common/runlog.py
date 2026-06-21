"""Helpers for writing the business-level run log to ``05_metadata.pipeline_run_log``.

Kept deliberately small: the platform records job runs, pipeline events, and lineage in
system tables; this only adds a human-readable per-run summary keyed by the Lakeflow job
run id so it joins back to ``system.lakeflow.job_run_timeline``.
"""

from __future__ import annotations

RUN_LOG_DDL = """
CREATE TABLE IF NOT EXISTS {catalog}.`05_metadata`.pipeline_run_log (
  run_id STRING, pipeline_name STRING, env STRING,
  status STRING, trigger STRING,
  batch_ids ARRAY<STRING>,
  rows_read BIGINT, rows_written BIGINT,
  started_at TIMESTAMP, ended_at TIMESTAMP,
  error_class STRING, error_message STRING
) USING DELTA
"""

DQ_RESULTS_DDL = """
CREATE TABLE IF NOT EXISTS {catalog}.`05_metadata`.dq_results (
  run_id STRING, batch_id STRING, entity STRING, rule_name STRING, severity STRING,
  rows_evaluated BIGINT, rows_passed BIGINT, rows_failed BIGINT,
  pass_rate DOUBLE, evaluated_at TIMESTAMP
) USING DELTA
"""


def open_run(spark, catalog: str, env: str, run_id: str, pipeline_name: str, trigger: str) -> None:
    spark.sql(RUN_LOG_DDL.format(catalog=catalog))
    spark.sql(DQ_RESULTS_DDL.format(catalog=catalog))
    spark.sql(f"""
        INSERT INTO {catalog}.`05_metadata`.pipeline_run_log
        SELECT '{run_id}','{pipeline_name}','{env}','RUNNING','{trigger}',
               array(), NULL, NULL, current_timestamp(), NULL, NULL, NULL
        """)


def close_run(
    spark,
    catalog: str,
    run_id: str,
    status: str,
    rows_read: int,
    rows_written: int,
    error_class: str | None = None,
    error_message: str | None = None,
) -> None:
    ec = "NULL" if error_class is None else f"'{error_class}'"
    em = "NULL" if error_message is None else "'" + error_message.replace("'", "''") + "'"
    spark.sql(f"""
        UPDATE {catalog}.`05_metadata`.pipeline_run_log
        SET status='{status}', rows_read={rows_read}, rows_written={rows_written},
            ended_at=current_timestamp(), error_class={ec}, error_message={em}
        WHERE run_id='{run_id}'
        """)
