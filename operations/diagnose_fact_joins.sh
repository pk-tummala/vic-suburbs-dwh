#!/usr/bin/env bash
#
# diagnose_fact_joins.sh — explain a fact's surrogate-key joins so the numbers are trustable.
#
# For each fact_suburb_<entity> it prints, as a spark.show()-style grid:
#   • per-year resolution     rows, resolved-to-a-suburb vs on the -1 unknown member, % resolved
#                             (this is where a temporal-join coverage gap shows up immediately)
#   • dimension coverage      dim_suburb version count and the [min __START_AT, max __END_AT] window
#   • key integrity           orphan suburb_sk / year_sk (fact keys with no matching dimension row)
#
# Usage:   ./operations/diagnose_fact_joins.sh [--warehouse-id <id>] [env] [entity]
#          entity optional; defaults to all measures. env defaults to dev.
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
ONLY="${POS[1]:-}"
CAT="vic_suburbs_${ENV}"
[[ -z "$WID" ]] && WID="$(databricks warehouses list -o json | jq -r '.[0].id')"
[[ -z "$WID" || "$WID" == "null" ]] && { echo "No SQL warehouse found; pass --warehouse-id or set DATABRICKS_WAREHOUSE_ID." >&2; exit 1; }

ALL=(demographics property crime transport education)
[[ -n "$ONLY" ]] && ALL=("$ONLY")

# Gold dims/facts live in 03_gold in every layout; reference them directly.
G="${CAT}.\`03_gold\`"

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

echo "== diagnose_fact_joins ($CAT) =="
echo "-- dim_suburb coverage window --"
render "SELECT count(*) AS versions, count(DISTINCT sal_code) AS suburbs,
               min(__START_AT) AS earliest_start, max(coalesce(__END_AT, TIMESTAMP'9999-12-31')) AS latest_end
        FROM ${G}.dim_suburb"

for e in "${ALL[@]}"; do
  echo ""
  echo "── fact_suburb_${e} ─────────────────────────────────────────────"
  echo "  per-year resolution (suburb_sk):"
  render "SELECT y.year,
                 count(*)                                            AS rows,
                 sum(CASE WHEN f.suburb_sk<>-1 THEN 1 ELSE 0 END)    AS resolved,
                 sum(CASE WHEN f.suburb_sk =-1 THEN 1 ELSE 0 END)    AS unknown,
                 round(100*avg(CASE WHEN f.suburb_sk<>-1 THEN 1 ELSE 0 END),1) AS pct_resolved
          FROM ${G}.fact_suburb_${e} f
          LEFT JOIN ${G}.dim_year y ON f.year_sk=y.year_sk
          GROUP BY y.year ORDER BY y.year"
  echo "  key integrity (orphans = fact key with no dimension row):"
  render "SELECT
            (SELECT count(*) FROM ${G}.fact_suburb_${e}) AS fact_rows,
            (SELECT count(*) FROM ${G}.fact_suburb_${e} f LEFT JOIN ${G}.dim_suburb d ON f.suburb_sk=d.suburb_sk
               WHERE f.suburb_sk<>-1 AND d.suburb_sk IS NULL) AS orphan_suburb_sk,
            (SELECT count(*) FROM ${G}.fact_suburb_${e} f LEFT JOIN ${G}.dim_year d ON f.year_sk=d.year_sk
               WHERE f.year_sk<>-1 AND d.year_sk IS NULL) AS orphan_year_sk,
            (SELECT count(*) FROM (SELECT suburb_sk, year_sk FROM ${G}.fact_suburb_${e}
               GROUP BY suburb_sk, year_sk HAVING count(*)>1)) AS duplicated_grain"
done
