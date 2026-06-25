#!/usr/bin/env bash
#
# verify_pipeline.sh — automated, trust-the-data check across the whole medallion.
#
# Output is two spark.show()-style grids — a Row-flow matrix and a Checks results table — plus a
# summary line. Exits non-zero if any check FAILs, so it doubles as a CI / post-deploy gate.
#
# Checks:
#   • run health          latest pipeline_run_log = SUCCESS and wrote rows (a FATAL expectation
#                         would have aborted the run, so SUCCESS implies no fatal DQ rule fired)
#   • row flow            every layer with an upstream that has rows must itself have rows
#                         (catches a silently-empty Silver/Gold even when the job is "green")
#   • dimension sanity    surrogate keys unique; exactly one open SCD2 version per business key
#   • fact grain          no duplicate (suburb_sk, year_sk) per fact (catches bloat / fan-out)
#   • referential health  every fact key resolves to a dimension row (no orphans)
#   • serving layer        each 04_reporting view is queryable; headline ones return rows
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

MEASURES=(demographics property crime transport education)
PASS=0; WARN=0; FAIL=0
RESULTS=""   # TSV rows for the Checks grid: check<TAB>result<TAB>detail
FLOW=""      # TSV rows for the Row-flow grid: entity<TAB>bronze<TAB>silver<TAB>fact<TAB>status

# scalar <sql> -> first column of first row (or empty / ERR:<msg>)
scalar() {
  databricks api post /api/2.0/sql/statements --json \
    "$(jq -n --arg w "$WID" --arg s "$1" '{warehouse_id:$w,statement:$s,wait_timeout:"50s"}')" \
    | jq -r 'if .status.state=="SUCCEEDED" then (.result.data_array[0][0] // "") else "ERR:"+(.status.error.message//"failed") end'
}

# Resolve where each table lives, so this works whether tables are split across
# 01_bronze/02_silver/03_gold or consolidated into one schema.
declare -A LOC
while IFS=$'\t' read -r s t; do [[ -n "$t" ]] && LOC["$t"]="$s"; done < <(
  databricks api post /api/2.0/sql/statements --json \
    "$(jq -n --arg w "$WID" --arg s "SELECT table_schema, table_name FROM ${CAT}.information_schema.tables WHERE table_schema IN ('01_bronze','02_silver','03_gold','04_reporting')" '{warehouse_id:$w,statement:$s,wait_timeout:"50s"}')" \
    | jq -r '.result.data_array[]? | @tsv')

fqn() { local t="$1" s="${LOC[$1]:-}"; [[ -z "$s" ]] && { echo ""; return; }; echo "${CAT}.\`${s}\`.\`${t}\`"; }
cnt() { local f; f="$(fqn "$1")"; [[ -z "$f" ]] && { echo "MISSING"; return; }; scalar "SELECT count(*) FROM $f"; }

# add <PASS|WARN|FAIL> <check> <detail> -> append to the Checks grid and bump counters
add() {
  RESULTS+="$2"$'\t'"$1"$'\t'"$3"$'\n'
  case "$1" in PASS) PASS=$((PASS+1));; WARN) WARN=$((WARN+1));; FAIL) FAIL=$((FAIL+1));; esac
}

# render a Spark.show()-style grid from TSV on stdin (first line = header; NULL/empty rendered blank)
render_tsv() {
  awk -F'\t' '
    { rows=NR; if(NF>m)m=NF; for(i=1;i<=NF;i++){v[NR,i]=$i; if(length($i)>w[i])w[i]=length($i)} }
    END{ if(rows==0){print "(no rows)";exit}
      b="+"; for(i=1;i<=m;i++){d="";for(j=0;j<w[i];j++)d=d"-"; b=b d"+"}
      for(r=1;r<=rows;r++){l="|"; for(i=1;i<=m;i++){c=v[r,i];p=w[i]-length(c);s=c;for(j=0;j<p;j++)s=s" ";l=l s"|"}
        if(r==1){print b;print l;print b} else print l}
      print b }'
}

# ── run health ───────────────────────────────────────────────────────────────
st="$(scalar "SELECT status FROM ${CAT}.\`05_metadata\`.pipeline_run_log ORDER BY started_at DESC LIMIT 1")"
rw="$(scalar "SELECT rows_written FROM ${CAT}.\`05_metadata\`.pipeline_run_log ORDER BY started_at DESC LIMIT 1")"
if   [[ "$st" == "SUCCESS" ]]; then add PASS "run health: latest run status" "SUCCESS (no FATAL expectation fired)"
elif [[ -z "$st" || "$st" =~ ^ERR ]]; then add WARN "run health: latest run status" "no pipeline_run_log row yet"
else add FAIL "run health: latest run status" "status=$st"; fi
if [[ "$rw" =~ ^[0-9]+$ && "$rw" -gt 0 ]]; then add PASS "run health: rows written" "rows_written=$rw"
else add WARN "run health: rows written" "rows_written=${rw:-<none>}"; fi

# ── row flow (bronze -> silver -> fact) ──────────────────────────────────────
FLOW="entity"$'\t'"bronze"$'\t'"silver"$'\t'"fact"$'\t'"status"$'\n'
for e in "${MEASURES[@]}"; do
  b="$(cnt "raw_${e}")"; s="$(cnt "${e}")"; g="$(cnt "fact_suburb_${e}")"
  if   [[ "$b" =~ ^[0-9]+$ && "$b" -gt 0 && "$s" == "0" ]]; then status="FAIL"; add FAIL "row flow: ${e}" "silver empty though bronze has $b rows"
  elif [[ "$s" =~ ^[0-9]+$ && "$s" -gt 0 && "$g" == "0" ]]; then status="FAIL"; add FAIL "row flow: ${e}" "fact empty though silver has $s rows"
  elif [[ "$g" =~ ^[0-9]+$ && "$g" -gt 0 ]];                  then status="OK";   add PASS "row flow: ${e}" "rows reach fact ($g)"
  else status="EMPTY"; add WARN "row flow: ${e}" "no rows anywhere"; fi
  FLOW+="${e}"$'\t'"${b}"$'\t'"${s}"$'\t'"${g}"$'\t'"${status}"$'\n'
done

# ── dimension sanity ─────────────────────────────────────────────────────────
for dim in dim_suburb:suburb_sk:sal_code dim_lga:lga_sk:lga_code; do
  IFS=: read -r d sk bk <<<"$dim"; f="$(fqn "$d")"; [[ -z "$f" ]] && { add WARN "dim sanity: ${d}" "table not found"; continue; }
  dup="$(scalar "SELECT count(*) FROM (SELECT $sk FROM $f GROUP BY $sk HAVING count(*)>1)")"
  [[ "$dup" == "0" ]] && add PASS "dim sanity: ${d}.${sk} unique" "" || add FAIL "dim sanity: ${d}.${sk} unique" "$dup duplicated key(s)"
  open="$(scalar "SELECT count(*) FROM (SELECT $bk FROM $f WHERE __END_AT IS NULL GROUP BY $bk HAVING count(*)>1)")"
  [[ "$open" == "0" ]] && add PASS "dim sanity: ${d} one open version per ${bk}" "" || add FAIL "dim sanity: ${d} one open version per ${bk}" "$open ${bk}(s) with >1 open version"
done

# ── fact grain (no bloat / fan-out) ──────────────────────────────────────────
for e in "${MEASURES[@]}"; do
  f="$(fqn "fact_suburb_${e}")"; [[ -z "$f" ]] && continue
  dup="$(scalar "SELECT count(*) FROM (SELECT suburb_sk, year_sk FROM $f GROUP BY suburb_sk, year_sk HAVING count(*)>1)")"
  [[ "$dup" == "0" ]] && add PASS "fact grain: fact_suburb_${e}" "(suburb_sk, year_sk) unique" || add FAIL "fact grain: fact_suburb_${e}" "$dup duplicated grain row(s)"
done

# ── referential integrity ────────────────────────────────────────────────────
for e in "${MEASURES[@]}"; do
  f="$(fqn "fact_suburb_${e}")"; ds="$(fqn dim_suburb)"; dy="$(fqn dim_year)"; [[ -z "$f" ]] && continue
  os="$(scalar "SELECT count(*) FROM $f f LEFT JOIN $ds d ON f.suburb_sk=d.suburb_sk WHERE f.suburb_sk<>-1 AND d.suburb_sk IS NULL")"
  oy="$(scalar "SELECT count(*) FROM $f f LEFT JOIN $dy d ON f.year_sk=d.year_sk WHERE f.year_sk<>-1 AND d.year_sk IS NULL")"
  unk="$(scalar "SELECT round(100*avg(CASE WHEN suburb_sk=-1 THEN 1 ELSE 0 END),1) FROM $f")"
  [[ "$os" == "0" && "$oy" == "0" ]] && add PASS "integrity: fact_suburb_${e}" "no orphan keys" || add FAIL "integrity: fact_suburb_${e}" "orphan suburb=$os year=$oy"
  [[ "$unk" =~ ^[0-9.]+$ ]] && awk "BEGIN{exit !($unk>50)}" && add WARN "integrity: fact_suburb_${e} unknown rate" "${unk}% on the -1 unknown member" || true
done

# ── serving layer ────────────────────────────────────────────────────────────
for v in vw_q1_population_growth vw_q2_transport_connectivity vw_q3_low_crime vw_q4_top_schools vw_q5_affordable_growth vw_q6_most_expensive; do
  n="$(scalar "SELECT count(*) FROM ${CAT}.\`04_reporting\`.${v}")"
  if   [[ "$n" =~ ^ERR ]]; then add FAIL "serving: ${v}" "not queryable (${n#ERR:})"
  elif [[ "$n" == "0" ]];   then add WARN "serving: ${v}" "0 rows"
  else add PASS "serving: ${v}" "$n rows"; fi
done

# ── output ───────────────────────────────────────────────────────────────────
echo "== verify_pipeline ($CAT) =="
echo
echo "Row flow:"
printf '%s' "$FLOW" | render_tsv
echo
echo "Checks:"
{ printf 'check\tresult\tdetail\n'; printf '%s' "$RESULTS"; } | render_tsv
echo
echo "Summary: PASS=$PASS  WARN=$WARN  FAIL=$FAIL"
[[ "$FAIL" -eq 0 ]] || exit 1
