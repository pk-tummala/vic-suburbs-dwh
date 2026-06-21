#!/usr/bin/env bash
#
# One-command environment bootstrap. Prerequisite: a Premium-tier Databricks workspace
# (Unity Catalog + serverless enabled) and an authenticated Databricks CLI.
#
# It performs everything that would otherwise be manual:
#   1. creates the five RBAC groups (idempotent)
#   2. deploys the bundle (pipeline, jobs, bootstrap job)
#   3. runs the UC bootstrap job  -> catalog, schemas, Volume, and grants
#
# After this, `make run ENV=<env>` (or `databricks bundle run vic_suburbs_job -t <env>`)
# is all that's needed to load data.
#
# Usage:
#   ./deployment/bootstrap.sh --env dev
#   ./deployment/bootstrap.sh --env dev --skip-groups   # if groups already managed by your IdP
#
set -euo pipefail

ENV="dev"
SKIP_GROUPS="false"
while [[ $# -gt 0 ]]; do
  case "$1" in
    --env)         ENV="$2"; shift 2 ;;
    --skip-groups) SKIP_GROUPS="true"; shift ;;
    *) echo "Unknown arg: $1" >&2; exit 2 ;;
  esac
done

GROUPS=(role_deployer svc_ingest svc_transform role_analyst role_steward)

echo "→ [1/3] Ensuring RBAC groups exist..."
if [[ "${SKIP_GROUPS}" != "true" ]]; then
  for g in "${GROUPS[@]}"; do
    if databricks groups create --display-name "${g}" >/dev/null 2>&1; then
      echo "   created group ${g}"
    else
      echo "   group ${g} already exists (or is IdP-managed) — skipping"
    fi
  done
else
  echo "   --skip-groups set; assuming groups are provisioned by your identity provider"
fi

echo "→ [2/3] Deploying bundle for ${ENV}..."
databricks bundle deploy -t "${ENV}"

echo "→ [3/3] Running Unity Catalog bootstrap job (catalog, schemas, volume, grants)..."
databricks bundle run vic_suburbs_bootstrap_job -t "${ENV}"

echo "✓ Bootstrap complete for ${ENV}."
echo "  Next:  make run ENV=${ENV}   (or: databricks bundle run vic_suburbs_job -t ${ENV})"
