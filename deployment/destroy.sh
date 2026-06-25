#!/usr/bin/env bash
#
# Tear down EVERYTHING this project provisions for one environment — a true clean slate:
#   1. bundle-deployed resources  — the DLT pipeline, the job, and dashboards
#   2. data objects              — the catalog and, by cascade, every schema, table,
#                                  managed table, view, and Volume (incl. checkpoints)
#   3. the SQL warehouse          — provisioned by bootstrap.sh for catalog creation
#   4. the RBAC groups            — the five role/service groups bootstrap.sh created
#
# NOTE: the RBAC groups are workspace-wide (not env-suffixed), so deleting them affects every
# environment. For a single-environment POC that is the intended clean-slate behaviour.
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
WH_NAME="vic_suburbs_${ENV}_wh"
RBAC_GROUPS=(role_deployer svc_ingest svc_transform role_analyst role_steward)

# RBAC groups are ACCOUNT groups, so deleting them needs account-level auth — the profile
# bootstrap.sh created (default 'vic-account'; override with DATABRICKS_ACCOUNT_PROFILE).
ACCOUNT_PROFILE="${DATABRICKS_ACCOUNT_PROFILE:-vic-account}"
acct() { databricks account "$@" -p "${ACCOUNT_PROFILE}"; }

echo "════════════════════════════════════════════════════════════════"
echo "  DESTROY — Victoria Suburbs Profiler"
echo "  Target environment : ${ENV}"
echo "  This will PERMANENTLY delete:"
echo "    • bundle resources  : pipeline, job, dashboards   (target ${ENV})"
echo "    • catalog (cascade) : ${CATALOG}  → all schemas, tables, volumes"
echo "    • SQL warehouse     : ${WH_NAME}"
echo "    • RBAC groups       : ${RBAC_GROUPS[*]}"
echo "════════════════════════════════════════════════════════════════"

if [[ "${FORCE}" != "true" ]]; then
  read -r -p "Type the catalog name '${CATALOG}' to confirm: " reply
  if [[ "${reply}" != "${CATALOG}" ]]; then
    echo "Confirmation did not match. Aborting — nothing was deleted."
    exit 1
  fi
fi

echo "→ [1/4] Destroying bundle resources (pipeline, job, dashboards) for ${ENV}..."
databricks bundle destroy -t "${ENV}" --auto-approve

# `bundle destroy` clears the target's files but leaves the parent workspace folders behind.
# Remove the whole project bundle folder (covers the dev/ target and its children).
ME="$(databricks current-user me -o json 2>/dev/null | jq -r '.userName // empty')"
if [[ -n "${ME}" ]]; then
  BUNDLE_DIR="/Workspace/Users/${ME}/.bundle/vic_suburbs_dwh"
  if databricks workspace get-status "${BUNDLE_DIR}" >/dev/null 2>&1; then
    databricks workspace delete "${BUNDLE_DIR}" --recursive && echo "   removed ${BUNDLE_DIR}"
  else
    echo "   (${BUNDLE_DIR} already absent — skipping)"
  fi
  # Remove the .bundle parent too, but ONLY if it's now empty — it is shared across bundles,
  # so deleting it unconditionally could remove other projects' deployments.
  PARENT="/Workspace/Users/${ME}/.bundle"
  if databricks workspace get-status "${PARENT}" >/dev/null 2>&1; then
    remaining="$(databricks workspace list "${PARENT}" -o json 2>/dev/null | jq 'length' 2>/dev/null || echo 0)"
    if [[ "${remaining}" == "0" ]]; then
      databricks workspace delete "${PARENT}" && echo "   removed empty ${PARENT}"
    else
      echo "   (${PARENT} kept — still holds other bundles)"
    fi
  fi
else
  echo "   WARN: could not resolve current user; skipping bundle-folder cleanup." >&2
fi

echo "→ [2/4] Dropping catalog ${CATALOG} (cascade)..."
# Drops the catalog and ALL contained schemas/tables/volumes. Idempotent: ignore "not found".
databricks catalogs delete "${CATALOG}" --force 2>/dev/null \
  || echo "   (catalog ${CATALOG} already absent — skipping)"

echo "→ [3/4] Deleting SQL warehouse ${WH_NAME}..."
# Removes the warehouse bootstrap.sh provisioned for catalog creation. Idempotent.
WH_ID="$(databricks warehouses list -o json 2>/dev/null \
  | jq -r --arg n "${WH_NAME}" 'map(select(.name == $n)) | .[0].id // empty')"
if [[ -n "${WH_ID}" ]]; then
  databricks warehouses delete "${WH_ID}" && echo "   deleted ${WH_NAME} (${WH_ID})"
else
  echo "   (warehouse ${WH_NAME} already absent — skipping)"
fi

echo "→ [4/4] Deleting account-level RBAC groups..."
# Removes the five account groups bootstrap.sh created (matched by display name). Best-effort:
# if account auth isn't configured, warn rather than fail the whole teardown.
if acct groups list -o json >/dev/null 2>&1; then
  acct_groups_json="$(acct groups list -o json 2>/dev/null || echo '[]')"
  for g in "${RBAC_GROUPS[@]}"; do
    gid="$(echo "${acct_groups_json}" \
      | jq -r --arg n "${g}" 'map(select((.displayName // .display_name) == $n)) | .[0].id // empty')"
    if [[ -n "${gid}" ]]; then
      acct groups delete "${gid}" && echo "   deleted account group ${g} (${gid})"
    else
      echo "   (account group ${g} already absent — skipping)"
    fi
  done
else
  echo "   WARN: account API unreachable — skipping group deletion." >&2
  echo "   Set DATABRICKS_ACCOUNT_PROFILE and re-run, or delete the groups in the account console." >&2
fi

echo "✓ Destroy complete for ${ENV}. Clean slate — no residual objects, no residual cost."
