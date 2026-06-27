#!/usr/bin/env bash
#
# verify_pipeline.sh — automated, trust-the-data check across the whole medallion.
#
# SQL-first: all the logic lives in two statements that return result sets — one Row-flow grid
# and one Checks grid (check, result, detail) — which are rendered with the same shared awk grid
# as diagnose_fact_joins.sh. The shell just runs them, renders, tallies the result column, and
# sets the exit code. Exits non-zero if any check FAILs, so it doubles as a CI / post-deploy gate.
#
# Checks (all computed in SQL):
#   • run health          latest pipeline_run_log = SUCCESS and wrote rows (a FATAL expectation
#                         aborts the update, so SUCCESS implies no fatal DQ rule fired)
#   • row flow            every layer with an upstream that has rows must itself have rows
#                         (catches a silently-empty Silver/Gold even when the job is "green")
#   • dimension sanity    surrogate keys unique; exactly one open SCD2 version per business key
#   • fact grain          no duplicate (suburb_sk, year_sk) per fact (catches bloat / fan-out)
#   • referential health  every non-(-1) fact key resolves to a dimension row (no orphans);
#                         warns when the -1 unknown-member rate exceeds 50%
#   • serving layer       each 04_reporting view is queryable; headline ones return rows
#
# Usage:   ./operations/verify_pipeline.sh [--warehouse-id <id>] [env]   (env defaults to dev)
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
CAT="vic_suburbs_${ENV}"
[[ -z "$WID" ]] && WID="$(databricks warehouses list -o json | jq -r '.[0].id')"
[[ -z "$WID" || "$WID" == "null" ]] && { echo "No SQL warehouse found; pass --warehouse-id or set DATABRICKS_WAREHOUSE_ID." >&2; exit 1; }

# Per-layer schema FQNs (settled Pass-2 layout). Defined once with escaped backticks, then reused
# in the SQL below so the large statements stay backtick-free and readable.
B="${CAT}.\`01_bronze\`"; S="${CAT}.\`02_silver\`"; G="${CAT}.\`03_gold\`"
R="${CAT}.\`04_reporting\`"; M="${CAT}.\`05_metadata\`"

# run_sql <sql> -> TSV on stdout (header + rows, NULL rendered blank). Returns 1 on engine error,
# emitting a single "__ERROR__<TAB><message>" line so the caller can surface it.
run_sql() {
  local resp; resp="$(databricks api post /api/2.0/sql/statements --json \
    "$(jq -n --arg w "$WID" --arg s "$1" '{warehouse_id:$w,statement:$s,wait_timeout:"50s"}')")"
  if [[ "$(jq -r '.status.state' <<<"$resp")" != "SUCCEEDED" ]]; then
    printf '__ERROR__\t%s\n' "$(jq -r '.status.error.message // "failed"' <<<"$resp")"; return 1; fi
  { jq -r '[.manifest.schema.columns[].name] | @tsv' <<<"$resp"
    jq -r '(.result.data_array // [])[] | map(if . == null then "" else . end) | @tsv' <<<"$resp"; }
}

# grid : render a spark.show()-style bordered table from TSV on stdin (first line = header).
grid() {
  awk -F'\t' '
    { rows=NR; if(NF>m)m=NF; for(i=1;i<=NF;i++){v[NR,i]=$i; if(length($i)>w[i])w[i]=length($i)} }
    END{ if(rows==0){print "(no rows)";exit}
      b="+"; for(i=1;i<=m;i++){d="";for(j=0;j<w[i];j++)d=d"-"; b=b d"+"}
      for(r=1;r<=rows;r++){l="|"; for(i=1;i<=m;i++){c=v[r,i];p=w[i]-length(c);s=c;for(j=0;j<p;j++)s=s" ";l=l s"|"}
        if(r==1){print b;print l;print b} else print l}
      print b }'
}

# Shared CTE: per-entity bronze/silver/fact counts, in canonical (medallion) order via idx.
FLOW_CTE="flow AS (
  SELECT 'demographics' entity, 1 idx, (SELECT count(*) FROM ${B}.raw_demographics) bronze, (SELECT count(*) FROM ${S}.demographics) silver, (SELECT count(*) FROM ${G}.fact_suburb_demographics) fact
  UNION ALL SELECT 'property',  2, (SELECT count(*) FROM ${B}.raw_property),  (SELECT count(*) FROM ${S}.property),  (SELECT count(*) FROM ${G}.fact_suburb_property)
  UNION ALL SELECT 'crime',     3, (SELECT count(*) FROM ${B}.raw_crime),     (SELECT count(*) FROM ${S}.crime),     (SELECT count(*) FROM ${G}.fact_suburb_crime)
  UNION ALL SELECT 'transport', 4, (SELECT count(*) FROM ${B}.raw_transport), (SELECT count(*) FROM ${S}.transport), (SELECT count(*) FROM ${G}.fact_suburb_transport)
  UNION ALL SELECT 'education', 5, (SELECT count(*) FROM ${B}.raw_education), (SELECT count(*) FROM ${S}.education), (SELECT count(*) FROM ${G}.fact_suburb_education)
)"

FLOW_SQL="WITH ${FLOW_CTE}
SELECT entity, bronze, silver, fact,
       CASE WHEN bronze>0 AND silver=0 THEN 'FAIL'
            WHEN silver>0 AND fact=0   THEN 'FAIL'
            WHEN fact>0                THEN 'OK'
            ELSE 'EMPTY' END AS status
FROM flow ORDER BY idx"

CHECKS_SQL="WITH ${FLOW_CTE},
runlog AS (SELECT status, rows_written FROM ${M}.pipeline_run_log ORDER BY started_at DESC LIMIT 1),
dimd AS (SELECT
   (SELECT count(*) FROM (SELECT suburb_sk FROM ${G}.dim_suburb GROUP BY suburb_sk HAVING count(*)>1)) ssk_dup,
   (SELECT count(*) FROM (SELECT sal_code  FROM ${G}.dim_suburb WHERE __END_AT IS NULL GROUP BY sal_code HAVING count(*)>1)) s_open,
   (SELECT count(*) FROM (SELECT lga_sk    FROM ${G}.dim_lga    GROUP BY lga_sk    HAVING count(*)>1)) lsk_dup,
   (SELECT count(*) FROM (SELECT lga_code  FROM ${G}.dim_lga    WHERE __END_AT IS NULL GROUP BY lga_code HAVING count(*)>1)) l_open),
grain AS (
   SELECT 'fact_suburb_demographics' f, 1 idx, (SELECT count(*) FROM (SELECT suburb_sk,year_sk FROM ${G}.fact_suburb_demographics GROUP BY suburb_sk,year_sk HAVING count(*)>1)) n
   UNION ALL SELECT 'fact_suburb_property',  2, (SELECT count(*) FROM (SELECT suburb_sk,year_sk FROM ${G}.fact_suburb_property  GROUP BY suburb_sk,year_sk HAVING count(*)>1))
   UNION ALL SELECT 'fact_suburb_crime',     3, (SELECT count(*) FROM (SELECT suburb_sk,year_sk FROM ${G}.fact_suburb_crime     GROUP BY suburb_sk,year_sk HAVING count(*)>1))
   UNION ALL SELECT 'fact_suburb_transport', 4, (SELECT count(*) FROM (SELECT suburb_sk,year_sk FROM ${G}.fact_suburb_transport GROUP BY suburb_sk,year_sk HAVING count(*)>1))
   UNION ALL SELECT 'fact_suburb_education', 5, (SELECT count(*) FROM (SELECT suburb_sk,year_sk FROM ${G}.fact_suburb_education GROUP BY suburb_sk,year_sk HAVING count(*)>1))),
orph AS (
   SELECT 'fact_suburb_demographics' f, 1 idx,
     (SELECT count(*) FROM ${G}.fact_suburb_demographics WHERE suburb_sk<>-1 AND suburb_sk NOT IN (SELECT suburb_sk FROM ${G}.dim_suburb)) os,
     (SELECT count(*) FROM ${G}.fact_suburb_demographics WHERE year_sk<>-1   AND year_sk   NOT IN (SELECT year_sk   FROM ${G}.dim_year))   oy,
     (SELECT round(100*avg(CASE WHEN suburb_sk=-1 THEN 1 ELSE 0 END),1) FROM ${G}.fact_suburb_demographics) pct
   UNION ALL SELECT 'fact_suburb_property', 2,
     (SELECT count(*) FROM ${G}.fact_suburb_property WHERE suburb_sk<>-1 AND suburb_sk NOT IN (SELECT suburb_sk FROM ${G}.dim_suburb)),
     (SELECT count(*) FROM ${G}.fact_suburb_property WHERE year_sk<>-1   AND year_sk   NOT IN (SELECT year_sk   FROM ${G}.dim_year)),
     (SELECT round(100*avg(CASE WHEN suburb_sk=-1 THEN 1 ELSE 0 END),1) FROM ${G}.fact_suburb_property)
   UNION ALL SELECT 'fact_suburb_crime', 3,
     (SELECT count(*) FROM ${G}.fact_suburb_crime WHERE suburb_sk<>-1 AND suburb_sk NOT IN (SELECT suburb_sk FROM ${G}.dim_suburb)),
     (SELECT count(*) FROM ${G}.fact_suburb_crime WHERE year_sk<>-1   AND year_sk   NOT IN (SELECT year_sk   FROM ${G}.dim_year)),
     (SELECT round(100*avg(CASE WHEN suburb_sk=-1 THEN 1 ELSE 0 END),1) FROM ${G}.fact_suburb_crime)
   UNION ALL SELECT 'fact_suburb_transport', 4,
     (SELECT count(*) FROM ${G}.fact_suburb_transport WHERE suburb_sk<>-1 AND suburb_sk NOT IN (SELECT suburb_sk FROM ${G}.dim_suburb)),
     (SELECT count(*) FROM ${G}.fact_suburb_transport WHERE year_sk<>-1   AND year_sk   NOT IN (SELECT year_sk   FROM ${G}.dim_year)),
     (SELECT round(100*avg(CASE WHEN suburb_sk=-1 THEN 1 ELSE 0 END),1) FROM ${G}.fact_suburb_transport)
   UNION ALL SELECT 'fact_suburb_education', 5,
     (SELECT count(*) FROM ${G}.fact_suburb_education WHERE suburb_sk<>-1 AND suburb_sk NOT IN (SELECT suburb_sk FROM ${G}.dim_suburb)),
     (SELECT count(*) FROM ${G}.fact_suburb_education WHERE year_sk<>-1   AND year_sk   NOT IN (SELECT year_sk   FROM ${G}.dim_year)),
     (SELECT round(100*avg(CASE WHEN suburb_sk=-1 THEN 1 ELSE 0 END),1) FROM ${G}.fact_suburb_education)),
serv AS (
   SELECT 'vw_q1_population_growth' v, 1 idx, (SELECT count(*) FROM ${R}.vw_q1_population_growth) n
   UNION ALL SELECT 'vw_q2_transport_connectivity', 2, (SELECT count(*) FROM ${R}.vw_q2_transport_connectivity)
   UNION ALL SELECT 'vw_q3_low_crime',              3, (SELECT count(*) FROM ${R}.vw_q3_low_crime)
   UNION ALL SELECT 'vw_q4_top_schools',            4, (SELECT count(*) FROM ${R}.vw_q4_top_schools)
   UNION ALL SELECT 'vw_q5_affordable_growth',      5, (SELECT count(*) FROM ${R}.vw_q5_affordable_growth)
   UNION ALL SELECT 'vw_q6_most_expensive',         6, (SELECT count(*) FROM ${R}.vw_q6_most_expensive))
SELECT chk AS \`check\`, result, detail FROM (
   SELECT 10 ord, 0 sub, 'run health: latest run status' chk,
          CASE WHEN status='SUCCESS' THEN 'PASS' ELSE 'FAIL' END result,
          CASE WHEN status='SUCCESS' THEN 'SUCCESS (no FATAL expectation fired)' ELSE concat('status=', coalesce(status,'<none>')) END detail FROM runlog
   UNION ALL SELECT 11, 0, 'run health: rows written',
          CASE WHEN rows_written>0 THEN 'PASS' ELSE 'WARN' END, concat('rows_written=', cast(coalesce(rows_written,0) AS string)) FROM runlog
   UNION ALL SELECT 20, idx, concat('row flow: ', entity),
          CASE WHEN bronze>0 AND silver=0 THEN 'FAIL' WHEN silver>0 AND fact=0 THEN 'FAIL' WHEN fact>0 THEN 'PASS' ELSE 'WARN' END,
          CASE WHEN bronze>0 AND silver=0 THEN concat('silver empty though bronze has ', cast(bronze AS string),' rows')
               WHEN silver>0 AND fact=0   THEN concat('fact empty though silver has ', cast(silver AS string),' rows')
               WHEN fact>0                THEN concat('rows reach fact (', cast(fact AS string),')')
               ELSE 'no rows anywhere' END FROM flow
   UNION ALL SELECT 30, 0, 'dim sanity: dim_suburb.suburb_sk unique', CASE WHEN ssk_dup=0 THEN 'PASS' ELSE 'FAIL' END, CASE WHEN ssk_dup=0 THEN '' ELSE concat(cast(ssk_dup AS string),' duplicated key(s)') END FROM dimd
   UNION ALL SELECT 31, 0, 'dim sanity: dim_suburb one open version per sal_code', CASE WHEN s_open=0 THEN 'PASS' ELSE 'FAIL' END, CASE WHEN s_open=0 THEN '' ELSE concat(cast(s_open AS string),' sal_code(s) with >1 open version') END FROM dimd
   UNION ALL SELECT 32, 0, 'dim sanity: dim_lga.lga_sk unique', CASE WHEN lsk_dup=0 THEN 'PASS' ELSE 'FAIL' END, CASE WHEN lsk_dup=0 THEN '' ELSE concat(cast(lsk_dup AS string),' duplicated key(s)') END FROM dimd
   UNION ALL SELECT 33, 0, 'dim sanity: dim_lga one open version per lga_code', CASE WHEN l_open=0 THEN 'PASS' ELSE 'FAIL' END, CASE WHEN l_open=0 THEN '' ELSE concat(cast(l_open AS string),' lga_code(s) with >1 open version') END FROM dimd
   UNION ALL SELECT 40, idx, concat('fact grain: ', f), CASE WHEN n=0 THEN 'PASS' ELSE 'FAIL' END, CASE WHEN n=0 THEN '(suburb_sk, year_sk) unique' ELSE concat(cast(n AS string),' duplicated grain row(s)') END FROM grain
   UNION ALL SELECT 50, idx, concat('integrity: ', f), CASE WHEN os=0 AND oy=0 THEN 'PASS' ELSE 'FAIL' END, CASE WHEN os=0 AND oy=0 THEN 'no orphan keys' ELSE concat('orphan suburb=', cast(os AS string),' year=', cast(oy AS string)) END FROM orph
   UNION ALL SELECT 55, idx, concat('integrity: ', f, ' unknown rate'), 'WARN', concat(cast(pct AS string),'% on the -1 unknown member') FROM orph WHERE pct > 50
   UNION ALL SELECT 60, idx, concat('serving: ', v), CASE WHEN n=0 THEN 'WARN' ELSE 'PASS' END, concat(cast(n AS string),' rows') FROM serv
) ORDER BY ord, sub, chk"

echo "== verify_pipeline ($CAT) =="
echo
echo "Row flow:"
flow_tsv="$(run_sql "$FLOW_SQL")" || { printf '%s\n' "$flow_tsv" | grid; echo "ERROR running row-flow query." >&2; exit 1; }
printf '%s\n' "$flow_tsv" | grid

echo
echo "Checks:"
checks_tsv="$(run_sql "$CHECKS_SQL")" || { printf '%s\n' "$checks_tsv" | grid; echo "ERROR running checks query." >&2; exit 1; }
printf '%s\n' "$checks_tsv" | grid

read -r PASS WARN FAIL < <(printf '%s\n' "$checks_tsv" | awk -F'\t' 'NR>1{c[$2]++} END{printf "%d %d %d\n", c["PASS"], c["WARN"], c["FAIL"]}')
echo
echo "Summary: PASS=${PASS:-0}  WARN=${WARN:-0}  FAIL=${FAIL:-0}"
[[ "${FAIL:-0}" -eq 0 ]] || exit 1
