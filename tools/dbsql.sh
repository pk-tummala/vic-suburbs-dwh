#!/usr/bin/env bash
#
# Run a SQL statement on a Databricks SQL warehouse from the terminal, via the
# Statement Execution API (there is no `databricks sql query` command; this is the
# scriptable equivalent). Requires the Databricks CLI (authenticated) and jq.
#
# Usage:
#   ./tools/dbsql.sh "SELECT * FROM vic_suburbs_dev.04_reporting.vw_q6_most_expensive LIMIT 10"
#   ./tools/dbsql.sh --warehouse-id <id> "DESCRIBE HISTORY vic_suburbs_dev.03_gold.fact_suburb_property"
#
# Warehouse selection: --warehouse-id, else $DATABRICKS_WAREHOUSE_ID, else the first warehouse.
#
set -euo pipefail

WID="${DATABRICKS_WAREHOUSE_ID:-}"
SQL=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --warehouse-id) WID="$2"; shift 2 ;;
    *) SQL="$1"; shift ;;
  esac
done
[[ -z "$SQL" ]] && { echo "usage: $0 [--warehouse-id <id>] \"<SQL>\"" >&2; exit 2; }
[[ -z "$WID" ]] && WID=$(databricks warehouses list -o json | jq -r '.[0].id')
[[ -z "$WID" || "$WID" == "null" ]] && { echo "No SQL warehouse found; create one or pass --warehouse-id." >&2; exit 1; }

databricks api post /api/2.0/sql/statements --json \
  "$(jq -n --arg w "$WID" --arg s "$SQL" '{warehouse_id:$w, statement:$s, wait_timeout:"50s"}')" \
  | jq -r 'if .status.state=="SUCCEEDED" then (.result.data_array // [["(no rows)"]] | .[] | @tsv) else (.status|tostring) end'
