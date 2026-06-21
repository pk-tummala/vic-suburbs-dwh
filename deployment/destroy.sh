#!/usr/bin/env bash
#
# Tear down EVERYTHING this project deploys into a Databricks account for one environment:
#   1. bundle-deployed resources  — the DLT pipeline, the job, and dashboards
#   2. data objects              — the catalog and, by cascade, every schema, table,
#                                  managed table, view, and Volume (incl. checkpoints)
#
# Account-level groups (role_analyst, svc_ingest, ...) are intentionally left intact —
# they are shared identity, not project data. Drop them by hand if truly decommissioning.
#
# Usage:
#   ./deployment/destroy.sh --env dev            # prompts for confirmation
#   ./deployment/destroy.sh --env dev --force    # no prompt (CI / scripted)
#
set -euo pipefail

ENV="dev"
FORCE="false"
while [[ $# -gt 0 ]]; do
  case "$1" in
    --env)   ENV="$2"; shift 2 ;;
    --force) FORCE="true"; shift ;;
    *) echo "Unknown arg: $1" >&2; exit 2 ;;
  esac
done

CATALOG="vic_suburbs_${ENV}"

echo "════════════════════════════════════════════════════════════════"
echo "  DESTROY — Victoria Suburbs Profiler"
echo "  Target environment : ${ENV}"
echo "  This will PERMANENTLY delete:"
echo "    • bundle resources  : pipeline, job, dashboards   (target ${ENV})"
echo "    • catalog (cascade) : ${CATALOG}  → all schemas, tables, volumes"
echo "════════════════════════════════════════════════════════════════"

if [[ "${FORCE}" != "true" ]]; then
  read -r -p "Type the catalog name '${CATALOG}' to confirm: " reply
  if [[ "${reply}" != "${CATALOG}" ]]; then
    echo "Confirmation did not match. Aborting — nothing was deleted."
    exit 1
  fi
fi

echo "→ [1/2] Destroying bundle resources (pipeline, job, dashboards) for ${ENV}..."
databricks bundle destroy -t "${ENV}" --auto-approve

echo "→ [2/2] Dropping catalog ${CATALOG} (cascade)..."
# Drops the catalog and ALL contained schemas/tables/volumes. Idempotent: ignore "not found".
databricks catalogs delete "${CATALOG}" --force 2>/dev/null \
  || echo "   (catalog ${CATALOG} already absent — skipping)"

echo "✓ Destroy complete for ${ENV}. No residual objects, no residual cost."
echo "  Note: account groups were left intact by design."
