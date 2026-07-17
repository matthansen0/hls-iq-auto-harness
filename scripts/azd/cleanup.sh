#!/usr/bin/env bash
set -euo pipefail

# Robust cleanup script for azd environment and all resources
# Usage: bash scripts/azd/cleanup.sh [--env-name <name>] [--remove-env]

ENV_NAME="healthcare-demo"
REMOVE_ENV=false
SKIP_FABRIC_WORKSPACE=false

while [[ $# -gt 0 ]]; do
  case "$1" in
    --env-name)
      ENV_NAME="$2"
      shift 2
      ;;
    --remove-env)
      REMOVE_ENV=true
      shift
      ;;
    --skip-fabric-workspace)
      SKIP_FABRIC_WORKSPACE=true
      shift
      ;;
    -h|--help)
      echo "Usage: $0 [--env-name <name>] [--remove-env] [--skip-fabric-workspace]"
      exit 0
      ;;
    *)
      echo "Unknown arg: $1"
      exit 1
      ;;
  esac
done

# Ensure azd and az are installed
for cmd in azd az; do
  if ! command -v "$cmd" >/dev/null 2>&1; then
    echo "[error] Missing command: $cmd"
    exit 1
  fi
done

# Select environment and get resource group
if ! azd env select "$ENV_NAME" >/dev/null 2>&1; then
  echo "[error] azd environment not found: $ENV_NAME"
  exit 1
fi

get_env_value_safe() {
  local key="$1"
  local value=""
  value="$(azd env get-value "$key" 2>/dev/null || true)"
  if [[ "$value" == *"ERROR:"* ]] || [[ "$value" == *"key not found"* ]]; then
    echo ""
    return 0
  fi
  echo "$value"
}

RESOURCE_GROUP="$(get_env_value_safe AZURE_RESOURCE_GROUP)"
if [[ -z "$RESOURCE_GROUP" ]]; then
  ENV_SLUG="$(printf '%s' "$ENV_NAME" | tr '[:upper:]' '[:lower:]' | tr -cd 'a-z0-9-')"
  RESOURCE_GROUP="rg-${ENV_SLUG}"
  echo "[warn] AZURE_RESOURCE_GROUP is missing; using conventional fallback: $RESOURCE_GROUP"
fi

SUBSCRIPTION_ID="$(get_env_value_safe AZURE_SUBSCRIPTION_ID)"
if [[ -z "$SUBSCRIPTION_ID" ]]; then
  SUBSCRIPTION_ID="$(az account show --query id -o tsv 2>/dev/null || true)"
  if [[ -z "$SUBSCRIPTION_ID" ]]; then
    echo "[error] Could not determine subscription ID from azd env or Azure CLI."
    exit 1
  fi
  echo "[warn] AZURE_SUBSCRIPTION_ID is missing; using active Azure subscription."
fi

FABRIC_WORKSPACE_NAME="$(get_env_value_safe FABRIC_WORKSPACE_NAME)"
if [[ -z "$FABRIC_WORKSPACE_NAME" ]]; then
  FABRIC_WORKSPACE_NAME="HealthcareDemo-WS"
fi

# Run azd down (will delete resources defined in infra) without prompts
echo "[info] Running azd down for environment: $ENV_NAME"
azd down --environment "$ENV_NAME" --force --purge --no-prompt || true


# Explicitly delete the resource group to ensure all resources are purged
RG_EXISTS="$(az group exists --name "$RESOURCE_GROUP" --subscription "$SUBSCRIPTION_ID" 2>/dev/null || echo false)"
if [[ "$RG_EXISTS" == "true" ]]; then
  echo "[info] Deleting resource group: $RESOURCE_GROUP"
  if az group delete --name "$RESOURCE_GROUP" --subscription "$SUBSCRIPTION_ID" --yes --no-wait; then
    echo "[done] Cleanup initiated. Resource group deletion may take several minutes."
    echo "[info] You can check status with: az group show --name $RESOURCE_GROUP --subscription $SUBSCRIPTION_ID"
  else
    echo "[warn] Could not delete resource group '$RESOURCE_GROUP'. Continuing with Fabric workspace cleanup."
  fi
else
  echo "[info] Resource group '$RESOURCE_GROUP' was already removed."
fi

# Attempt to delete Fabric workspace via API unless the caller is cleaning an
# incomplete probe environment with no workspace metadata.
if [[ "$SKIP_FABRIC_WORKSPACE" == true ]]; then
  echo "[info] Skipping Fabric workspace deletion by request."
else
  echo "[info] Attempting to delete Fabric workspace via API (if present)"
  python3 "$(dirname "$0")/delete_fabric_workspace.py" --workspace-name "$FABRIC_WORKSPACE_NAME"
fi

if [[ "$REMOVE_ENV" == true ]]; then
  echo "[info] Removing local azd environment: $ENV_NAME"
  azd env remove "$ENV_NAME" --force
  echo "[done] Local azd environment removed. Next run will require fresh names."
fi
