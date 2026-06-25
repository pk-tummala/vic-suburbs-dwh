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
# Output is a bordered, left-aligned grid with a header row — the same shape as
# spark.sql(...).show(n, False) — with NULLs printed as NULL and the row count on stderr.
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

RESP="$(databricks api post /api/2.0/sql/statements --json \
  "$(jq -n --arg w "$WID" --arg s "$SQL" '{warehouse_id:$w, statement:$s, wait_timeout:"50s"}')")"

STATE="$(jq -r '.status.state // "UNKNOWN"' <<<"$RESP")"
if [[ "$STATE" != "SUCCEEDED" ]]; then
  jq -r '"ERROR (" + (.status.state // "UNKNOWN") + "): " + (.status.error.message // (.status|tostring))' <<<"$RESP" >&2
  exit 1
fi

NCOLS="$(jq -r '(.manifest.schema.columns // []) | length' <<<"$RESP")"
NROWS="$(jq -r '(.result.data_array // []) | length' <<<"$RESP")"
if [[ "$NCOLS" == "0" ]]; then echo "OK (no result set)." >&2; exit 0; fi

# Header row + data rows as TSV (NULLs -> literal NULL), then render a Spark-style bordered grid.
{
  jq -r '[.manifest.schema.columns[].name] | @tsv' <<<"$RESP"
  jq -r '(.result.data_array // [])[] | map(if . == null then "NULL" else . end) | @tsv' <<<"$RESP"
} | awk -F'\t' '
  { rows=NR; if (NF>maxnf) maxnf=NF
    for (i=1;i<=NF;i++) { v[NR,i]=$i; if (length($i)>w[i]) w[i]=length($i) } }
  END {
    if (rows==0) exit
    b="+"; for (i=1;i<=maxnf;i++) { d=""; for (j=0;j<w[i];j++) d=d"-"; b=b d"+" }
    for (r=1;r<=rows;r++) {
      l="|"; for (i=1;i<=maxnf;i++) { c=v[r,i]; p=w[i]-length(c); s=c; for (j=0;j<p;j++) s=s" "; l=l s"|" }
      if (r==1) { print b; print l; print b } else print l
    }
    print b
  }'

echo "($NROWS row$([[ "$NROWS" == "1" ]] || printf 's'))" >&2
