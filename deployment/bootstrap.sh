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

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CATALOG="vic_suburbs_${ENV}"
WH_NAME="vic_suburbs_${ENV}_wh"
RBAC_GROUPS=(role_deployer svc_ingest svc_transform role_analyst role_steward)

# Resolve our project SQL warehouse's id by name (empty string if it doesn't exist yet).
find_warehouse_id() {
  databricks warehouses list -o json 2>/dev/null \
    | jq -r --arg n "${WH_NAME}" 'map(select(.name == $n)) | .[0].id // empty'
}

# Unity Catalog grants resolve principals against the ACCOUNT identity store, so the RBAC
# groups must be ACCOUNT groups (workspace-local groups from `databricks groups create` are
# invisible to UC and produce PRINCIPAL_DOES_NOT_EXIST). Account API calls use a dedicated
# account-level CLI profile, which bootstrap sets up on first run (see ensure_account_auth).
DATABRICKS_CFG="${DATABRICKS_CONFIG_FILE:-$HOME/.databrickscfg}"
ACCOUNT_HOST="${DATABRICKS_ACCOUNT_HOST:-https://accounts.cloud.databricks.com}"
ACCOUNT_PROFILE="${DATABRICKS_ACCOUNT_PROFILE:-vic-account}"
acct() { databricks account "$@" -p "${ACCOUNT_PROFILE}"; }

# Idempotently ensure an account-level CLI profile exists. If it's already in the config file we
# reuse it; otherwise we explain why it's needed, collect the Account ID, and run a one-time
# OAuth account login that writes the profile to the config file.
ensure_account_auth() {
  if [[ -f "${DATABRICKS_CFG}" ]] && grep -q "^\[${ACCOUNT_PROFILE}\]$" "${DATABRICKS_CFG}"; then
    echo "   account profile '${ACCOUNT_PROFILE}' already configured — reusing it"
    return 0
  fi
  echo ""
  echo "   One-time account login required."
  echo "   Why: Unity Catalog grants only recognise ACCOUNT-level groups, and creating those"
  echo "   needs account-level auth. This writes a local CLI profile ('${ACCOUNT_PROFILE}' in"
  echo "   ${DATABRICKS_CFG}) used solely to create/manage this project's RBAC groups."
  echo "   Find your Account ID at ${ACCOUNT_HOST} → top-right user menu."
  echo ""
  local acct_id="${DATABRICKS_ACCOUNT_ID:-}"
  if [[ -z "${acct_id}" ]]; then
    if [[ -t 0 ]]; then
      read -r -p "   Enter your Databricks Account ID: " acct_id || true
    else
      echo "ERROR: no account profile and no interactive terminal to prompt." >&2
      echo "  Set DATABRICKS_ACCOUNT_ID=<id> and rerun, or create it once with:" >&2
      echo "  databricks auth login --host ${ACCOUNT_HOST} --account-id <ID> --profile ${ACCOUNT_PROFILE}" >&2
      exit 1
    fi
  fi
  [[ -z "${acct_id}" ]] && { echo "ERROR: no Account ID provided." >&2; exit 1; }
  echo "   launching account login (a browser window will open to authenticate)..."
  databricks auth login --host "${ACCOUNT_HOST}" --account-id "${acct_id}" --profile "${ACCOUNT_PROFILE}"
}

echo "→ [1/4] Ensuring account-level RBAC groups exist..."
if [[ "${SKIP_GROUPS}" != "true" ]]; then
  ensure_account_auth
  if ! acct groups list -o json >/dev/null 2>&1; then
    echo "ERROR: account profile '${ACCOUNT_PROFILE}' isn't working (login failed or expired)." >&2
    echo "  Re-run: databricks auth login --host ${ACCOUNT_HOST} --account-id <ID> --profile ${ACCOUNT_PROFILE}" >&2
    exit 1
  fi
  acct_groups_json="$(acct groups list -o json 2>/dev/null || echo '[]')"
  for g in "${RBAC_GROUPS[@]}"; do
    gid="$(echo "${acct_groups_json}" \
      | jq -r --arg n "${g}" 'map(select((.displayName // .display_name) == $n)) | .[0].id // empty')"
    if [[ -n "${gid}" ]]; then
      echo "   account group ${g} already exists — skipping"
    else
      acct groups create --json "$(jq -n --arg n "${g}" '{displayName: $n}')" >/dev/null \
        && echo "   created account group ${g}"
    fi
  done
else
  echo "   --skip-groups set; assuming groups are provisioned by your identity provider"
fi

echo "→ [2/4] Ensuring catalog ${CATALOG} exists..."
# 'databricks catalogs create' can't make a catalog on a Default-Storage workspace (CLI issue
# #4513), but 'CREATE CATALOG' via SQL can. So create it through the Statement Execution API
# (tools/dbsql.sh) BEFORE deploy, since the DLT pipeline is validated against its catalog at
# deploy time. Skipped entirely if the catalog already exists (no warehouse needed on re-runs).
if databricks catalogs get "${CATALOG}" >/dev/null 2>&1; then
  echo "   catalog ${CATALOG} already exists"
else
  # SQL needs a warehouse. Reuse our project warehouse if present, else create a small
  # serverless one (auto-stops after 10 idle min; removed by deployment/destroy.sh).
  WID="$(find_warehouse_id)"
  if [[ -z "${WID}" ]]; then
    echo "   creating serverless SQL warehouse ${WH_NAME} (2X-Small)..."
    WID="$(databricks warehouses create --json "$(jq -n --arg n "${WH_NAME}" '{
        name: $n,
        cluster_size: "2X-Small",
        warehouse_type: "PRO",
        enable_serverless_compute: true,
        auto_stop_mins: 10,
        max_num_clusters: 1
      }')" -o json | jq -r '.id // empty')"
    [[ -z "${WID}" ]] && WID="$(find_warehouse_id)"   # fall back to lookup by name
  fi
  [[ -z "${WID}" ]] && { echo "ERROR: could not find or create a SQL warehouse." >&2; exit 1; }
  echo "   using SQL warehouse ${WH_NAME} (${WID})"

  echo "   creating catalog via SQL (Default Storage compatible)..."
  bash "${SCRIPT_DIR}/../tools/dbsql.sh" --warehouse-id "${WID}" \
    "CREATE CATALOG IF NOT EXISTS ${CATALOG} COMMENT 'Victoria Suburbs Profiler'" >/dev/null
  # confirm the catalog actually exists rather than trusting the async statement call
  if ! databricks catalogs get "${CATALOG}" >/dev/null 2>&1; then
    echo "ERROR: catalog ${CATALOG} was not created (check the SQL warehouse)." >&2
    exit 1
  fi
  echo "   created catalog ${CATALOG}"
fi

echo "→ [3/4] Deploying bundle for ${ENV}..."
# uploads bootstrap/bootstrap_uc.py and creates the pipeline + jobs; the catalog now exists,
# so the DLT pipeline validates cleanly.
databricks bundle deploy -t "${ENV}"

echo "→ [4/4] Running Unity Catalog bootstrap job (schemas, volume, grants)..."
# bootstrap_uc.py creates the six schemas, the landing Volume, and applies the grants
# (re-ensuring the catalog with CREATE CATALOG IF NOT EXISTS — a harmless no-op now).
databricks bundle run vic_suburbs_bootstrap_job -t "${ENV}"

echo "✓ Bootstrap complete for ${ENV}."
echo "  Next:  make run ENV=${ENV}   (or: databricks bundle run vic_suburbs_job -t ${ENV})"
