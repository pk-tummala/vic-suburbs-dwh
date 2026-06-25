# Databricks notebook source
# Orchestration pre-task: open a RUNNING row in the run log before the pipeline starts.
import sys

dbutils.widgets.text("env", "dev")  # noqa: F821
dbutils.widgets.text("catalog", "vic_suburbs_dev")  # noqa: F821
dbutils.widgets.text("src_path", "")  # noqa: F821
dbutils.widgets.text("run_id", "manual")  # noqa: F821

# Put the bundle-deployed package source on sys.path so `vic_suburbs` imports on serverless
# compute. src_path is passed by the job as ${workspace.file_path}/src — a /Workspace path that
# is FUSE-mounted on the cluster. Harmless no-op anywhere the package is already installed.
_src = dbutils.widgets.get("src_path")  # noqa: F821
if _src and _src not in sys.path:
    sys.path.insert(0, _src)

from vic_suburbs.common.runlog import open_run  # noqa: E402

env = dbutils.widgets.get("env")  # noqa: F821
catalog = dbutils.widgets.get("catalog")  # noqa: F821
# run_id is supplied by the job as {{job.run_id}} (a task parameter) and read as a widget — the
# supported, py4j-free way to get the run context. Falls back to "manual" for interactive runs.
run_id = dbutils.widgets.get("run_id") or "manual"  # noqa: F821
trigger = "scheduled" if env != "dev" else "manual"

open_run(spark, catalog, env, str(run_id), "vic_suburbs_pipeline", trigger)  # noqa: F821
print(f"opened run_log row run_id={run_id} env={env}")
