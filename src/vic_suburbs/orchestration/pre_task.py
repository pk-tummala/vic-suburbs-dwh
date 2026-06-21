# Databricks notebook source
# Orchestration pre-task: open a RUNNING row in the run log before the pipeline starts.
from vic_suburbs.common.runlog import open_run

dbutils.widgets.text("env", "dev")  # noqa: F821
dbutils.widgets.text("catalog", "vic_suburbs_dev")  # noqa: F821

env = dbutils.widgets.get("env")  # noqa: F821
catalog = dbutils.widgets.get("catalog")  # noqa: F821
ctx = dbutils.notebook.entry_point.getDbutils().notebook().getContext()  # noqa: F821
run_id = ctx.jobRunId().getOrElse(lambda: "manual")
trigger = "scheduled" if env != "dev" else "manual"

open_run(spark, catalog, env, str(run_id), "vic_suburbs_pipeline", trigger)  # noqa: F821
print(f"opened run_log row run_id={run_id} env={env}")
