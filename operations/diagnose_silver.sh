#!/usr/bin/env bash
#
# diagnose_silver.sh — explain why a Silver measure is empty when its Bronze has rows.
#
# Run this when verify_pipeline.sh reports "silver empty though bronze has N rows". It walks the
# same chain we debug by hand, printing spark.show()-style grids:
#   1. localization   bronze vs silver per measure — confirms WHERE the rows vanish
#   2. key health     NULL sal_code (dropped by the FATAL not_null rule) and duplicated grains
#                     (collapsed to one row each by dedup_latest) — the two ways rows reduce
#   3. DQ rules       the WARN/FATAL rules that gate this entity — a broken WARN expectation
#                     (e.g. a regex whose backslash was stripped) drops every row, silently,
#                     while the job stays green
#   4. flow ordering  the event-log sequence — did this entity's Bronze finish before Silver ran?
#
# Usage:   ./operations/diagnose_silver.sh [--warehouse-id <id>] [env] [entity]
#          entity defaults to property; env defaults to dev.
#
set -euo pipefail

WID="${DATABRICKS_WAREHOUSE_ID:-}"
POS=()
while [[ $# -gt 0 ]]; do
  case "$1" in
    --warehouse-id) WID="$2"; shift 2 ;;
    *) POS+=("$1"); shift ;;
  esac
done
ENV="${POS[0]:-dev}"
ENTITY="${POS[1]:-property}"
CAT="vic_suburbs_${ENV}"
[[ -z "$WID" ]] && WID="$(databricks warehouses list -o json | jq -r '.[0].id')"
[[ -z "$WID" || "$WID" == "null" ]] && { echo "No SQL warehouse found; pass --warehouse-id or set DATABRICKS_WAREHOUSE_ID." >&2; exit 1; }

B="${CAT}.\`01_bronze\`"; S="${CAT}.\`02_silver\`"; M="${CAT}.\`05_metadata\`"

# render <sql> : run SQL and print a bordered, left-aligned grid (header + rows, NULL shown).
render() {
  local resp; resp="$(databricks api post /api/2.0/sql/statements --json \
    "$(jq -n --arg w "$WID" --arg s "$1" '{warehouse_id:$w,statement:$s,wait_timeout:"50s"}')")"
  if [[ "$(jq -r '.status.state' <<<"$resp")" != "SUCCEEDED" ]]; then
    jq -r '"  ERROR: " + (.status.error.message // "failed")' <<<"$resp"; return; fi
  { jq -r '[.manifest.schema.columns[].name] | @tsv' <<<"$resp"
    jq -r '(.result.data_array // [])[] | map(if . == null then "NULL" else . end) | @tsv' <<<"$resp"; } \
  | awk -F'\t' '
      { rows=NR; if(NF>m)m=NF; for(i=1;i<=NF;i++){v[NR,i]=$i; if(length($i)>w[i])w[i]=length($i)} }
      END{ if(rows==0){print "  (no rows)";exit}
        b="+"; for(i=1;i<=m;i++){d="";for(j=0;j<w[i];j++)d=d"-"; b=b d"+"}
        for(r=1;r<=rows;r++){l="|"; for(i=1;i<=m;i++){c=v[r,i];p=w[i]-length(c);s=c;for(j=0;j<p;j++)s=s" ";l=l s"|"}
          if(r==1){print "  "b;print "  "l;print "  "b} else print "  "l}
        print "  "b }'
}

echo "== diagnose_silver ($CAT) — focus entity: ${ENTITY} =="

echo "-- 1. bronze -> silver localization (where the rows vanish) --"
render "SELECT e AS entity, b AS bronze, s AS silver, b - s AS dropped,
          CASE WHEN b=0 THEN '-' ELSE concat(cast(round(100.0*(b-s)/b,1) AS string),'%') END AS drop_pct
        FROM (
          SELECT 'property'  e, (SELECT count(*) FROM ${B}.raw_property)  b, (SELECT count(*) FROM ${S}.property)  s
          UNION ALL SELECT 'crime',     (SELECT count(*) FROM ${B}.raw_crime),     (SELECT count(*) FROM ${S}.crime)
          UNION ALL SELECT 'transport', (SELECT count(*) FROM ${B}.raw_transport), (SELECT count(*) FROM ${S}.transport)
          UNION ALL SELECT 'education', (SELECT count(*) FROM ${B}.raw_education), (SELECT count(*) FROM ${S}.education)
        ) ORDER BY e"

echo ""
echo "-- 2. ${ENTITY}: key health in bronze (nulls dropped by DQ; dup grains collapsed by dedup) --"
render "SELECT
          (SELECT count(*) FROM ${B}.raw_${ENTITY})                                          AS bronze_rows,
          (SELECT count(*) FROM ${B}.raw_${ENTITY} WHERE sal_code IS NULL)                    AS null_sal_code,
          (SELECT count(*) FROM (SELECT sal_code, period FROM ${B}.raw_${ENTITY}
                                 GROUP BY sal_code, period HAVING count(*) > 1))              AS duplicated_grains"
echo "   read: null_sal_code -> dropped by the FATAL sal_code_not_null rule;"
echo "         duplicated_grains -> collapsed to one row each by dedup_latest (expected, not a loss);"
echo "         bronze_rows - null_sal_code - dup_overflow ~= silver rows."

echo ""
echo "-- 3. ${ENTITY}: DQ rules that gate this entity (a broken WARN drops every row) --"
if [[ -f "config/dq_rules/${ENTITY}.yaml" ]]; then sed 's/^/   /' "config/dq_rules/${ENTITY}.yaml"
else echo "   (config/dq_rules/${ENTITY}.yaml not found from CWD — run from the repo root to show it)"; fi
echo "   If keys above are healthy but Silver is 0, a WARN expectation is dropping the rows."
echo "   Inspect regex_match / value_range rules first: a metachar or bound that matches nothing"
echo "   sends 100% of rows to the WARN drop while the run still reports SUCCESS."

echo ""
echo "-- 4. flow ordering (did ${ENTITY}'s bronze finish before its silver ran?) --"
render "SELECT date_format(timestamp,'HH:mm:ss.SSS') AS ts, message
        FROM ${M}.pipeline_event_log
        WHERE event_type='flow_progress'
          AND (message LIKE '%raw_${ENTITY}%' OR message LIKE '%.${ENTITY}%')
        ORDER BY timestamp"
