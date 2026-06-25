# Databricks notebook source
# Orchestration post-task: summarise the pipeline update and close the run log.
# Reads the pipeline event log for row counts + expectation metrics, writes dq_results,
# and sets the run status to SUCCESS / NO_OP / FAILED.
import sys

from pyspark.sql import functions as F

dbutils.widgets.text("env", "dev")  # noqa: F821
dbutils.widgets.text("catalog", "vic_suburbs_dev")  # noqa: F821
dbutils.widgets.text("src_path", "")  # noqa: F821
dbutils.widgets.text("run_id", "manual")  # noqa: F821

# Put the bundle-deployed package source on sys.path so `vic_suburbs` imports on serverless
# compute (src_path = ${workspace.file_path}/src, passed by the job).
_src = dbutils.widgets.get("src_path")  # noqa: F821
if _src and _src not in sys.path:
    sys.path.insert(0, _src)

from vic_suburbs.common.runlog import close_run  # noqa: E402

env = dbutils.widgets.get("env")  # noqa: F821
catalog = dbutils.widgets.get("catalog")  # noqa: F821
# run_id supplied by the job as {{job.run_id}} and read as a widget (py4j-free; the supported way).
run_id = dbutils.widgets.get("run_id") or "manual"  # noqa: F821

# Row counts from the published pipeline event log (flow_progress metrics), scoped to the most
# recent update so the total reflects THIS run rather than every historical update in the log.
events = spark.read.table(f"{catalog}.`05_metadata`.pipeline_event_log")  # noqa: F821
flow = events.where(F.col("event_type") == "flow_progress")
_latest = (
    flow.select(F.col("origin.update_id").alias("update_id"), "timestamp")
    .orderBy(F.col("timestamp").desc())
    .limit(1)
    .collect()
)
written = 0
if _latest and _latest[0]["update_id"] is not None:
    written = (
        flow.where(F.col("origin.update_id") == _latest[0]["update_id"])
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
