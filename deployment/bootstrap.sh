#!/usr/bin/env bash
#
# One-command environment bootstrap. Prerequisite: a Premium-tier Databricks workspace
# (Unity Catalog + serverless enabled) and an authenticated Databricks CLI.
#
# It performs everything that would otherwise be manual:
#   1. creates the five account RBAC groups (idempotent)
#   2. ensures the project SQL warehouse and the catalog exist (idempotent)
#   3. deploys the bundle (pipeline, jobs, bootstrap job, dashboard)
#   4. runs the UC bootstrap job  -> schemas, Volume, and grants
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

# True only if the profile can actually reach the ACCOUNT API. This catches all three ways it can
# be wrong: missing, expired, or — the common trap — a profile with this name created against a
# WORKSPACE host (e.g. by `make auth`), which looks configured but can't make account calls.
# Testing the call is authoritative; a mere [section] in the config file is not.
account_profile_works() {
  databricks account groups list -p "${ACCOUNT_PROFILE}" -o json >/dev/null 2>&1
}

# Run the one-time OAuth account login that writes (or overwrites) the account profile.
account_login() {
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
      echo "ERROR: no working account profile and no interactive terminal to prompt." >&2
      echo "  Set DATABRICKS_ACCOUNT_ID=<id> and rerun, or create it once with:" >&2
      echo "  databricks auth login --host ${ACCOUNT_HOST} --account-id <ID> --profile ${ACCOUNT_PROFILE}" >&2
      exit 1
    fi
  fi
  [[ -z "${acct_id}" ]] && { echo "ERROR: no Account ID provided." >&2; exit 1; }
  echo "   launching account login (a browser window will open to authenticate)..."
  databricks auth login --host "${ACCOUNT_HOST}" --account-id "${acct_id}" --profile "${ACCOUNT_PROFILE}"
}

# Remove one [profile] section from the CLI config so a fresh `auth login` can recreate it. The
# CLI refuses to overwrite a profile whose saved host differs from --host, so a profile created
# against the wrong host (e.g. a workspace login named 'vic-account') must be cleared first.
# Backs the file up to <cfg>.bak before rewriting.
remove_cfg_profile() {
  local name="$1"
  [[ -f "${DATABRICKS_CFG}" ]] || return 0
  cp "${DATABRICKS_CFG}" "${DATABRICKS_CFG}.bak"
  awk -v hdr="[${name}]" '
    $0 == hdr { skip = 1; next }
    skip && /^\[/ { skip = 0 }
    !skip { print }
  ' "${DATABRICKS_CFG}.bak" > "${DATABRICKS_CFG}"
  echo "   cleared the stale '${name}' entry (backup saved to ${DATABRICKS_CFG}.bak)"
}

# Guarantee a WORKING account profile or exit with a precise message. We validate by calling the
# account API (not by trusting that a [profile] section merely exists), and self-heal by
# re-authenticating when a profile with this name exists but can't reach the account.
ensure_account_auth() {
  if account_profile_works; then
    echo "   account profile '${ACCOUNT_PROFILE}' is authenticated — reusing it"
    return 0
  fi
  if [[ -f "${DATABRICKS_CFG}" ]] && grep -q "^\[${ACCOUNT_PROFILE}\]$" "${DATABRICKS_CFG}"; then
    echo "   profile '${ACCOUNT_PROFILE}' exists but can't reach the account API." >&2
    echo "   Most often it was created against a WORKSPACE host (e.g. via 'make auth') or its token" >&2
    echo "   expired; it must be an ACCOUNT login against ${ACCOUNT_HOST}. Re-authenticating it now..." >&2
    remove_cfg_profile "${ACCOUNT_PROFILE}"   # clear the stale entry so `auth login` can recreate it
  fi
  account_login
  if ! account_profile_works; then
    echo "ERROR: account profile '${ACCOUNT_PROFILE}' still can't reach the account API after login." >&2
    echo "  Check you logged in against ${ACCOUNT_HOST} with a valid Account ID and are an account" >&2
    echo "  admin. If your IdP manages the RBAC groups instead, re-run with --skip-groups." >&2
    exit 1
  fi
}

echo "→ [1/4] Ensuring account-level RBAC groups exist..."
if [[ "${SKIP_GROUPS}" != "true" ]]; then
  ensure_account_auth
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

echo "→ [2/4] Ensuring SQL warehouse ${WH_NAME} and catalog ${CATALOG} exist..."
# The bundle resolves its `warehouse_id` variable by NAME (lookup: ${WH_NAME}) at deploy time,
# so the warehouse must exist BEFORE `bundle deploy` — independently of whether the catalog
# already exists. Ensure it first (idempotent), then reuse it for CREATE CATALOG below.
WID="$(find_warehouse_id)"
if [[ -z "${WID}" ]]; then
  echo "   creating serverless SQL warehouse ${WH_NAME} (2X-Small, auto-stops after 10 idle min)..."
  WID="$(databricks warehouses create --json "$(jq -n --arg n "${WH_NAME}" '{
      name: $n,
      cluster_size: "2X-Small",
      warehouse_type: "PRO",
      enable_serverless_compute: true,
      auto_stop_mins: 10,
      max_num_clusters: 1
    }')" -o json | jq -r '.id // empty')"
  [[ -z "${WID}" ]] && WID="$(find_warehouse_id)"   # fall back to lookup by name
  [[ -z "${WID}" ]] && { echo "ERROR: could not find or create SQL warehouse ${WH_NAME}." >&2; exit 1; }
  echo "   created SQL warehouse ${WH_NAME} (${WID})"
else
  echo "   SQL warehouse ${WH_NAME} already exists (${WID})"
fi

# 'databricks catalogs create' can't make a catalog on a Default-Storage workspace (CLI issue
# #4513), but 'CREATE CATALOG' via SQL can — run it through the Statement Execution API
# (tools/dbsql.sh) BEFORE deploy, since the DLT pipeline is validated against its catalog at
# deploy time.
if databricks catalogs get "${CATALOG}" >/dev/null 2>&1; then
  echo "   catalog ${CATALOG} already exists"
else
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
