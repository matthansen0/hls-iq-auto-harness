#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT_DIR"

ENV_NAME="healthcare-demo"
TURBO=false
SKIP_INSTALL=false
ASSIST_MANUAL=true

usage() {
  cat <<'EOF'
Usage: scripts/azd/run_all.sh [options]

Options:
  --env-name <name>      azd environment name (default: healthcare-demo)
  --turbodeploy          Use F256 for setup, then auto-scale to F64 after notebook run
  --skip-install         Skip prereq installer
  --no-assist-manual     Skip assisted manual checkpoint flow
  -h, --help             Show help
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --env-name)
      ENV_NAME="$2"
      shift 2
      ;;
    --turbodeploy)
      TURBO=true
      shift
      ;;
    --skip-install)
      SKIP_INSTALL=true
      shift
      ;;
    --no-assist-manual)
      ASSIST_MANUAL=false
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown arg: $1"
      usage
      exit 1
      ;;
  esac
done

if [[ "$SKIP_INSTALL" != true ]]; then
  bash scripts/azd/install_prereqs.sh
fi

for cmd in az azd python3; do
  if ! command -v "$cmd" >/dev/null 2>&1; then
    echo "[error] Missing command: $cmd"
    exit 1
  fi
done

if ! az account show >/dev/null 2>&1; then
  echo "[error] Not logged in to Azure CLI. Run: az login"
  exit 1
fi

if ! azd auth login --check-status >/dev/null 2>&1; then
  echo "[error] Not logged in to azd. Run: azd auth login --use-device-code"
  exit 1
fi

if ! azd env select "$ENV_NAME" >/dev/null 2>&1; then
  echo "[info] Creating azd environment: $ENV_NAME"
  azd env new "$ENV_NAME"
fi

echo "[info] Ensuring required azd environment values and infra config are set."

ENV_SLUG="$(printf '%s' "$ENV_NAME" | tr '[:upper:]' '[:lower:]' | tr -cd 'a-z0-9')"
if [[ -z "$ENV_SLUG" ]]; then
  ENV_SLUG="healthcaredemo"
fi
NAME_SUFFIX="${ENV_SLUG: -10}"
if [[ -z "$NAME_SUFFIX" ]]; then
  NAME_SUFFIX="demo"
fi
DEFAULT_RG_NAME="rg-${ENV_SLUG:0:40}"
DEFAULT_HUB_NAME="hub${NAME_SUFFIX}"
DEFAULT_SEARCH_SERVICE_NAME="search${NAME_SUFFIX}"
DEFAULT_FABRIC_CAPACITY_NAME="fab${NAME_SUFFIX}"

cleanup_stale_fabric_workspace() {
  local ws_name="$1"
  if [[ -z "$ws_name" ]]; then
    return 0
  fi
  
  echo "[cleanup] Checking for stale Fabric workspace: $ws_name"
  python3 scripts/azd/delete_fabric_workspace.py \
    --workspace-name "$ws_name" 2>/dev/null || true
}

set_if_missing() {
  local key="$1"
  local value="$2"
  if [[ -z "$(get_env_value_safe "$key")" ]]; then
    azd env set "$key" "$value" >/dev/null
    echo "[env] $key=$value"
  fi
}

get_config_value_safe() {
  local key="$1"
  local out=""
  out="$(azd env config get "$key" 2>/dev/null || true)"
  if [[ "$out" == *"ERROR:"* ]] || [[ "$out" == *"not found"* ]]; then
    echo ""
    return 0
  fi
  echo "$out"
}

set_config_if_missing() {
  local key="$1"
  local value="$2"
  if [[ -z "$(get_config_value_safe "$key")" ]]; then
    azd env config set "$key" "$value" >/dev/null
    echo "[config] $key=$value"
  fi
}

set_config_if_different() {
  local key="$1"
  local value="$2"
  local current=""
  current="$(get_config_value_safe "$key")"
  if [[ "$current" != "$value" ]]; then
    azd env config set "$key" "$value" >/dev/null
    echo "[config] $key=$value"
  fi
}

get_env_value_safe() {
  local key="$1"
  local out=""
  out="$(azd env get-value "$key" 2>/dev/null || true)"
  if [[ "$out" == *"ERROR: key not found in environment values"* ]]; then
    echo ""
    return 0
  fi
  echo "$out"
}

is_valid_admins_json_array() {
  local value="$1"
  [[ -n "$value" ]] || return 1
  echo "$value" | jq -e 'type == "array" and length > 0 and all(.[]; type == "string" and length > 3)' >/dev/null 2>&1
}

run_postprovision() {
  set -a
  eval "$(azd env get-values)"
  set +a
  python3 -u scripts/azd/postprovision.py
}

run_postdeployment_validation() {
  if [[ "$(get_env_value_safe RUN_POSTDEPLOY_VALIDATION)" != "true" ]]; then
    echo "[info] Post-deployment functional validation is disabled."
    return 0
  fi

  set -a
  eval "$(azd env get-values)"
  set +a

  rm -f -- \
    "${SEMANTIC_MODEL_HEALTH_LOG_PATH:-logs/semantic_model_health_canary.json}" \
    "${FUNCTIONAL_TEST_OUTPUT_PATH:-logs/functional_test_latest.json}"

  local semantic_args=()
  if [[ "${SEMANTIC_MODEL_TAKEOVER:-false}" == "true" ]]; then
    semantic_args+=(--take-over)
  fi
  if [[ "${SEMANTIC_MODEL_REFRESH:-true}" == "true" ]]; then
    semantic_args+=(--refresh)
  fi

  echo "[run] Running Direct Lake ownership/framing canary..."
  python3 -u scripts/automation/semantic_model_health_canary.py \
    --workspace-name "${FABRIC_WORKSPACE_NAME:-HealthcareDemo-WS}" \
    --dataset-name "${SEMANTIC_MODEL_NAME:-HealthcareDemoHLS}" \
    --output "${SEMANTIC_MODEL_HEALTH_LOG_PATH:-logs/semantic_model_health_canary.json}" \
    "${semantic_args[@]}" || return $?

  local project_endpoint
  project_endpoint="https://${HUB_NAME}.services.ai.azure.com/api/projects/${PROJECT_NAME}"
  local functional_args=()
  if [[ "${FUNCTIONAL_TEST_ENFORCE_SUCCESS:-true}" == "true" ]]; then
    functional_args+=(--enforce-success)
  fi

  echo "[run] Running IQ and direct-MCP functional tests..."
  python3 -u scripts/automation/functional_test_suite.py \
    --workspace-name "${FABRIC_WORKSPACE_NAME:-HealthcareDemo-WS}" \
    --project-endpoint "$project_endpoint" \
    --agent-name "${FOUNDRY_ORCHESTRATOR_AGENT_NAME:-HealthcareOrchestratorAgent2}" \
    --search-service-name "${SEARCH_SERVICE_NAME}" \
    --knowledge-base-name "${SEARCH_KNOWLEDGE_BASE_NAME:-healthcareknowledgebase}" \
    --timeout-seconds "${FUNCTIONAL_TEST_TIMEOUT_SECONDS:-180}" \
    --output "${FUNCTIONAL_TEST_OUTPUT_PATH:-logs/functional_test_latest.json}" \
    "${functional_args[@]}" || return $?
}

generate_live_iq_handoff() {
  set -a
  eval "$(azd env get-values --environment "$ENV_NAME")"
  set +a
  python3 -u scripts/automation/generate_live_iq_handoff.py --environment "$ENV_NAME"
}

finalize_deployment() {
  local validation_exit=0
  set +e
  run_postdeployment_validation
  validation_exit=$?
  set -e

  generate_live_iq_handoff
  if [[ "$validation_exit" -ne 0 ]]; then
    echo "[error] Post-deployment validation failed with exit code $validation_exit"
    return "$validation_exit"
  fi
}

colorize_stream() {
  if [[ ! -t 1 ]] || [[ -n "${NO_COLOR:-}" ]]; then
    cat
    return
  fi

  local c_reset c_blue c_green c_yellow c_red c_cyan
  c_reset=$'\033[0m'
  c_blue=$'\033[1;34m'
  c_green=$'\033[1;32m'
  c_yellow=$'\033[1;33m'
  c_red=$'\033[1;31m'
  c_cyan=$'\033[1;36m'

  awk \
    -v c_reset="$c_reset" \
    -v c_blue="$c_blue" \
    -v c_green="$c_green" \
    -v c_yellow="$c_yellow" \
    -v c_red="$c_red" \
    -v c_cyan="$c_cyan" \
    '
    {
      line = $0
      if (line ~ /^\[(INFO|info)\]/) {
        sub(/^\[(INFO|info)\]/, c_blue "[INFO]" c_reset, line)
      } else if (line ~ /^\[(STEP|step)\]/) {
        sub(/^\[(STEP|step)\]/, c_cyan "[STEP]" c_reset, line)
      } else if (line ~ /^\[(OK|ok|done|DONE)\]/) {
        sub(/^\[(OK|ok|done|DONE)\]/, c_green "[OK]" c_reset, line)
      } else if (line ~ /^\[(WARN|warn)\]/) {
        sub(/^\[(WARN|warn)\]/, c_yellow "[WARN]" c_reset, line)
      } else if (line ~ /^\[(ERROR|error)\]/) {
        sub(/^\[(ERROR|error)\]/, c_red "[ERROR]" c_reset, line)
      }
      print line
    }
  '
}

run_with_heartbeat() {
  local label="$1"
  shift

  local heartbeat_seconds=30
  local start_ts
  start_ts="$(date +%s)"

  "$@" &
  local cmd_pid=$!

  while kill -0 "$cmd_pid" >/dev/null 2>&1; do
    sleep "$heartbeat_seconds"
    if kill -0 "$cmd_pid" >/dev/null 2>&1; then
      local now elapsed mins secs
      now="$(date +%s)"
      elapsed=$((now - start_ts))
      mins=$((elapsed / 60))
      secs=$((elapsed % 60))
      printf '[wait] %s still running (%dm %02ds elapsed)\n' "$label" "$mins" "$secs"
    fi
  done

  wait "$cmd_pid"
}

show_manual_checkpoint_help() {
  local ws_name notebook_name ws_id env_name
  ws_name="$(get_env_value_safe FABRIC_WORKSPACE_NAME)"
  notebook_name="$(get_env_value_safe FABRIC_LAUNCHER_NOTEBOOK_NAME)"
  env_name="$(get_env_value_safe AZURE_ENV_NAME)"
  ws_name="${ws_name:-HealthcareDemo-WS}"
  notebook_name="${notebook_name:-Healthcare_Launcher}"
  env_name="${env_name:-healthcare-demo}"

  WS_NAME="$ws_name" ws_id="$(python3 - <<'PY'
import json, subprocess, sys, requests
import os

def run(cmd):
    return subprocess.check_output(cmd, text=True).strip()

try:
    token = run([
        'az', 'account', 'get-access-token',
        '--resource', 'https://api.fabric.microsoft.com',
        '--query', 'accessToken', '-o', 'tsv'
    ])
    ws_name = os.getenv('WS_NAME', '').strip() or 'HealthcareDemo-WS'
    h = {'Authorization': f'Bearer {token}', 'Content-Type': 'application/json'}
    r = requests.get('https://api.fabric.microsoft.com/v1/workspaces', headers=h, timeout=30)
    r.raise_for_status()
    for ws in r.json().get('value', []):
        if ws.get('displayName') == ws_name:
            print(ws.get('id', ''))
            sys.exit(0)
except Exception:
    pass
print('')
PY
)"

  echo ""
  echo "╔════════════════════════════════════════════════════════════════╗"
  echo "║                    MANUAL IMPORT REQUIRED                      ║"
  echo "╚════════════════════════════════════════════════════════════════╝"
  echo ""
  echo "STEP 1: Open Fabric workspace"
  if [[ -n "$ws_id" ]]; then
    echo "  URL: https://app.fabric.microsoft.com/groups/$ws_id/list"
  else
    echo "  Workspace name: $ws_name"
  fi
  echo ""
  echo "STEP 2: Import the notebook"
  echo "  a) Click 'Import' button in workspace"
  echo "  b) Select 'Notebook' option"
  echo "  c) Upload: fabric-main/Healthcare_Launcher.ipynb (preferred in this repo)"
  echo "     If present, repo-root Healthcare_Launcher.ipynb also works"
  echo "  d) Wait for import to complete"
  echo ""
  echo "STEP 3: Verify notebook is NOT empty"
  echo "  a) Open the imported notebook in Fabric editor"
  echo "  b) Confirm it contains cells (should see code/markdown)"
  echo "  c) Close the notebook"
  echo ""
  echo "STEP 4: Run the post-provision automation"
  echo "  Command: set -a && eval \"\$(azd env get-values)\" && set +a && python3 scripts/azd/postprovision.py"
  echo ""
  echo "  This will automatically:"
  echo "    • Start the notebook execution"
  echo "    • Monitor progress (~90 minutes)"
  echo "    • Display status every 30 seconds"
  echo "    • Complete when notebook finishes"
  echo ""
  echo "Press Enter after import to continue, or Ctrl+C to exit."
}

set_if_missing TURBO_SETUP_SKU "F256"
set_if_missing TURBO_SCALE_DOWN_SKU "F64"
set_if_missing NOTEBOOK_RUN_POLL_SECONDS "30"
set_if_missing NOTEBOOK_RUN_MAX_MINUTES "240"
set_if_missing FABRIC_WORKSPACE_NAME "HealthcareDemo-WS"
set_if_missing FABRIC_LAUNCHER_NOTEBOOK_NAME "Healthcare_Launcher"
set_if_missing FABRIC_DATA_AGENT_NAME "HealthcareHLSAgent"
set_if_missing FABRIC_DATA_AGENT_ROUTING "lakehouse_primary"
set_if_missing FABRIC_DATA_AGENT_ROUTING_SNAPSHOT_PATH "logs/data_agent_routing_snapshot.json"
set_if_missing FABRIC_DATA_AGENT_DEFINITION_DIRECTORY "fabric-main/workspace/HealthcareHLSAgent.DataAgent/Files/Config/draft"
set_if_missing FABRIC_DATA_AGENT_SEMANTIC_SOURCE "remove"
set_if_missing FABRIC_DATA_AGENT_SEMANTIC_SOURCE_SNAPSHOT_PATH "logs/data_agent_semantic_source_snapshot.json"
set_if_missing PROJECT_NAME "HealthcareDemo-HLS"
set_if_missing AZURE_RESOURCE_GROUP "$DEFAULT_RG_NAME"
set_if_missing LOCATION "japaneast"
set_if_missing AZURE_LOCATION "$(get_env_value_safe LOCATION)"
set_if_missing HUB_NAME "$DEFAULT_HUB_NAME"
set_if_missing SEARCH_SERVICE_NAME "$DEFAULT_SEARCH_SERVICE_NAME"
set_if_missing FABRIC_CAPACITY_NAME "$DEFAULT_FABRIC_CAPACITY_NAME"
set_if_missing AUTOMATE_FOUNDRY_REMAINING "true"
set_if_missing FOUNDRY_AUTOMATION_ENFORCE_SUCCESS "true"
set_if_missing FOUNDRY_REMAINING_MAX_ATTEMPTS "2"
set_if_missing FOUNDRY_REMAINING_RETRY_DELAY_SECONDS "30"
set_if_missing FOUNDRY_SELF_HEAL_MAX_ATTEMPTS "3"
set_if_missing FOUNDRY_SELF_HEAL_RETRY_DELAY_SECONDS "60"
set_if_missing FOUNDRY_SELF_HEAL_CONNECTION_ATTEMPTS "1"
set_if_missing FOUNDRY_SELF_HEAL_STATUS_PATH "logs/foundry_self_heal_status.json"
set_if_missing FOUNDRY_SUPPORT_BUNDLE_ON_FAILURE "true"
set_if_missing FOUNDRY_SUPPORT_BUNDLE_DIR "logs/support-bundles"
set_if_missing FOUNDRY_CONNECTION_BICEP_FALLBACK "true"
set_if_missing FOUNDRY_CONNECTION_BICEP_TIMEOUT_SECONDS "300"
set_if_missing FOUNDRY_CONNECTION_MAX_ATTEMPTS "2"
set_if_missing FOUNDRY_CONNECTION_RETRY_INITIAL_DELAY_SECONDS "5"
set_if_missing FOUNDRY_FABRIC_MODE "fabric_iq"
set_if_missing FOUNDRY_CHAT_DEPLOYMENT_NAME "gpt-5.4"
set_if_missing FOUNDRY_CHAT_MODEL_NAME "gpt-5.4"
set_if_missing FOUNDRY_CHAT_MODEL_VERSION "2026-03-05"
set_if_missing FOUNDRY_CHAT_SKU_NAME "GlobalStandard"
set_if_missing FOUNDRY_CHAT_CAPACITY "100"
set_if_missing FOUNDRY_MODEL_RESOURCE_URI "https://$(get_env_value_safe HUB_NAME).openai.azure.com"
set_if_missing FOUNDRY_EMBEDDING_DEPLOYMENT_NAME "text-embedding-ada-002"
set_if_missing FOUNDRY_EMBEDDING_MODEL_NAME "text-embedding-ada-002"
set_if_missing FOUNDRY_EMBEDDING_SKU_NAME "Standard"
set_if_missing FOUNDRY_EMBEDDING_CAPACITY "120"
set_if_missing FOUNDRY_FABRIC_CONNECTION_NAME "HealthcareHLSAgent"
set_if_missing FOUNDRY_FABRIC_IQ_CONNECTION_NAME "healthcare-fabric-iq"
set_if_missing SEARCH_KNOWLEDGE_MODE "onelake"
set_if_missing SEARCH_KNOWLEDGE_SOURCE_NAME "healthcare-policy-ks"
set_if_missing SEARCH_KNOWLEDGE_BASE_NAME "healthcareknowledgebase"
set_if_missing SEARCH_KNOWLEDGE_INDEX_NAME "healthcare-policy-index"
set_if_missing SEARCH_KNOWLEDGE_DIRECTORY "fabric-main/healthcare_knowledge"
set_if_missing SEARCH_KNOWLEDGE_ONELAKE_TARGET_PATH "healthcare_knowledge"
set_if_missing SEARCH_KNOWLEDGE_INGESTION_INTERVAL "P1D"
set_if_missing SEARCH_KNOWLEDGE_INGESTION_TIMEOUT_SECONDS "900"
set_if_missing SEARCH_KNOWLEDGE_INGESTION_POLL_SECONDS "15"
set_if_missing SEARCH_KNOWLEDGE_RETRIEVAL_REASONING_EFFORT "medium"
set_if_missing FOUNDRY_KB_CONNECTION_NAME "healthcare-kb-connection"
set_if_missing FOUNDRY_ORCHESTRATOR_AGENT_NAME "HealthcareOrchestratorAgent2"
set_if_missing FOUNDRY_ALLOW_KB_ONLY_AGENT_FALLBACK "true"
set_if_missing FOUNDRY_KB_ONLY_AGENT_NAME "HealthcareOrchestratorNonIQ"
set_if_missing FOUNDRY_BLOCKER_LOG_PATH "logs/foundry_connection_blockers.jsonl"
set_if_missing FOUNDRY_STATUS_REPORT_PATH "logs/foundry_completion_status.json"
set_if_missing FOUNDRY_INCLUDE_WEB_SEARCH "true"
set_if_missing FOUNDRY_ORCHESTRATOR_INSTRUCTIONS_FILE "config/orchestrator_instructions.md"
set_if_missing RUN_POSTDEPLOY_VALIDATION "true"
set_if_missing SEMANTIC_MODEL_TAKEOVER "true"
set_if_missing SEMANTIC_MODEL_REFRESH "true"
set_if_missing SEMANTIC_MODEL_HEALTH_LOG_PATH "logs/semantic_model_health_canary.json"
set_if_missing FUNCTIONAL_TEST_ENFORCE_SUCCESS "true"
set_if_missing FUNCTIONAL_TEST_TIMEOUT_SECONDS "180"
set_if_missing FUNCTIONAL_TEST_OUTPUT_PATH "logs/functional_test_latest.json"

# Keep key infra parameters synced into azd env config so azd up never prompts.
HUB_NAME_VAL="$(get_env_value_safe HUB_NAME)"
SEARCH_SERVICE_NAME_VAL="$(get_env_value_safe SEARCH_SERVICE_NAME)"
FABRIC_CAPACITY_NAME_VAL="$(get_env_value_safe FABRIC_CAPACITY_NAME)"
LOCATION_VAL="$(get_env_value_safe LOCATION)"
if [[ -n "$HUB_NAME_VAL" ]]; then
  set_config_if_different infra.parameters.hubName "$HUB_NAME_VAL"
fi
if [[ -n "$SEARCH_SERVICE_NAME_VAL" ]]; then
  set_config_if_different infra.parameters.searchServiceName "$SEARCH_SERVICE_NAME_VAL"
fi
if [[ -n "$FABRIC_CAPACITY_NAME_VAL" ]]; then
  set_config_if_different infra.parameters.fabricCapacityName "$FABRIC_CAPACITY_NAME_VAL"
fi
if [[ -n "$LOCATION_VAL" ]]; then
  set_config_if_different infra.parameters.location "$LOCATION_VAL"
fi

# Ensure azd does not prompt for subscription selection during azd up.
DEFAULT_SUBSCRIPTION_ID="$(az account show --query id -o tsv 2>/dev/null || true)"
if [[ -n "$DEFAULT_SUBSCRIPTION_ID" ]]; then
  set_if_missing AZURE_SUBSCRIPTION_ID "$DEFAULT_SUBSCRIPTION_ID"
fi

# Fabric capacity requires at least one capacity admin (UPN/email).
DEFAULT_ADMIN_UPN=""
AZ_ACCOUNT_USER_TYPE="$(az account show --query user.type -o tsv 2>/dev/null || true)"
if [[ "$AZ_ACCOUNT_USER_TYPE" == "user" ]]; then
  DEFAULT_ADMIN_UPN="$(az account show --query user.name -o tsv 2>/dev/null || true)"
fi

if [[ -n "$DEFAULT_ADMIN_UPN" ]]; then
  set_if_missing FABRIC_CAPACITY_ADMINS "[\"$DEFAULT_ADMIN_UPN\"]"
fi

ADMINS_ARRAY_JSON="$(get_env_value_safe FABRIC_CAPACITY_ADMINS)"
if ! is_valid_admins_json_array "$ADMINS_ARRAY_JSON"; then
  if [[ -n "$DEFAULT_ADMIN_UPN" ]]; then
    ADMINS_ARRAY_JSON="[\"$DEFAULT_ADMIN_UPN\"]"
    azd env set FABRIC_CAPACITY_ADMINS "$ADMINS_ARRAY_JSON" >/dev/null
    echo "[env] FABRIC_CAPACITY_ADMINS=$ADMINS_ARRAY_JSON"
  else
    echo "[error] FABRIC_CAPACITY_ADMINS is missing or invalid."
    echo "        Expected JSON array, for example: [\"you@contoso.com\"]"
    exit 1
  fi
fi

# Keep azd config in sync and overwrite bad values from previous runs.
azd env config set infra.parameters.fabricCapacityAdmins "$ADMINS_ARRAY_JSON" >/dev/null
echo "[config] infra.parameters.fabricCapacityAdmins=$ADMINS_ARRAY_JSON"

if [[ -z "$(get_env_value_safe FABRIC_CAPACITY_ADMINS)" ]]; then
  echo "[error] Missing FABRIC_CAPACITY_ADMINS in azd env."
  echo "        Fabric capacity deployment requires at least one admin UPN/email."
  echo "        Example: azd env set FABRIC_CAPACITY_ADMINS '[\"you@contoso.com\"]'"
  exit 1
fi

if [[ "$TURBO" == true ]]; then
  azd env set TURBO_DEPLOY "true" >/dev/null
  azd env set FABRIC_CAPACITY_SKU "F256" >/dev/null
  azd env config set infra.parameters.fabricCapacitySku "F256" >/dev/null
  echo "[mode] TURBO enabled: provisioning capacity at F256"
else
  azd env set TURBO_DEPLOY "false" >/dev/null
  azd env set FABRIC_CAPACITY_SKU "F64" >/dev/null
  azd env config set infra.parameters.fabricCapacitySku "F64" >/dev/null
  echo "[mode] Standard mode: provisioning capacity at F64"
fi

echo "[run] Starting azd up (this may take a long time)..."
FABRIC_WS_NAME="$(get_env_value_safe FABRIC_WORKSPACE_NAME)"
if [[ -n "$FABRIC_WS_NAME" ]]; then
  echo "[cleanup] Removing stale Fabric workspace before azd up (if it exists)..."
  cleanup_stale_fabric_workspace "$FABRIC_WS_NAME"
fi

SKIP_AZD_POSTPROVISION=true azd up --no-prompt

echo "[done] azd up complete"
if [[ "$TURBO" == true ]]; then
  echo "[done] Turbo mode requested. Post-provision script will scale down to F64 after notebook reaches terminal status."
fi

echo "[run] Running post-provision bootstrap..."
POSTPROVISION_LOG="$(mktemp)"
trap 'rm -f "$POSTPROVISION_LOG"' EXIT
set +e
run_postprovision | tee "$POSTPROVISION_LOG" | colorize_stream
POSTPROVISION_EXIT=${PIPESTATUS[0]}
set -e

# Trigger manual import only when postprovision definitively reports notebook-missing failure.
NOTEBOOK_MISSING_MSG_PRIMARY="is still missing in workspace."
NOTEBOOK_MISSING_MSG_FALLBACK="Manual fallback: Fabric workspace -> Import -> Notebook -> Healthcare_Launcher.ipynb"
if grep -q "$NOTEBOOK_MISSING_MSG_PRIMARY" "$POSTPROVISION_LOG" || \
   grep -q "$NOTEBOOK_MISSING_MSG_FALLBACK" "$POSTPROVISION_LOG"; then
  show_manual_checkpoint_help
  if [[ -r /dev/tty ]]; then
    read -r < /dev/tty
  else
    echo "[warn] Non-interactive shell detected; cannot wait for Enter."
    echo "[next] Import notebook manually, then run:"
    echo "       set -a && eval \"\$(azd env get-values)\" && set +a && python3 -u scripts/azd/postprovision.py"
    exit 0
  fi
  echo ""
  echo "[run] Re-running post-provision bootstrap after manual import..."
  echo ""
  set +e
  run_postprovision | colorize_stream
  MANUAL_POSTPROVISION_EXIT=${PIPESTATUS[0]}
  set -e
  if [[ "$MANUAL_POSTPROVISION_EXIT" -ne 0 ]]; then
    exit "$MANUAL_POSTPROVISION_EXIT"
  fi
  finalize_deployment
  exit $?
fi

if [[ "$POSTPROVISION_EXIT" -ne 0 ]]; then
  echo "[error] Post-provision bootstrap failed with exit code $POSTPROVISION_EXIT"
  exit "$POSTPROVISION_EXIT"
fi

finalize_deployment
