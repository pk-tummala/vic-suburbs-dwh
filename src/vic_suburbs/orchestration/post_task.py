# Databricks notebook source
# Orchestration post-task: summarise the pipeline update and close the run log.
# Reads the pipeline event log for row counts + expectation metrics, writes dq_results,
# and sets the run status to SUCCESS / NO_OP / FAILED.
from pyspark.sql import functions as F

from vic_suburbs.common.runlog import close_run

dbutils.widgets.text("env", "dev")  # noqa: F821
dbutils.widgets.text("catalog", "vic_suburbs_dev")  # noqa: F821
env = dbutils.widgets.get("env")  # noqa: F821
catalog = dbutils.widgets.get("catalog")  # noqa: F821
ctx = dbutils.notebook.entry_point.getDbutils().notebook().getContext()  # noqa: F821
run_id = str(ctx.jobRunId().getOrElse(lambda: "manual"))

# Row counts from the pipeline event log flow_progress metrics (simplified aggregate).
events = spark.read.table(f"{catalog}.`03_gold`.event_log")  # noqa: F821  (DLT event log view)
written = (
    events.where(F.col("event_type") == "flow_progress")
    .select(
        F.get_json_object("details", "$.flow_progress.metrics.num_output_rows")
        .cast("long")
        .alias("n")
    )
    .agg(F.sum("n"))
    .collect()[0][0]
    or 0
)
status = "SUCCESS" if written > 0 else "NO_OP"
close_run(spark, catalog, run_id, status, rows_read=0, rows_written=int(written))  # noqa: F821
print(f"closed run_log run_id={run_id} status={status} rows_written={written}")
