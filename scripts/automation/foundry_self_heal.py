#!/usr/bin/env python3
"""Retry only the Foundry Fabric connection and full orchestrator agent."""

from __future__ import annotations

import argparse
import ast
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parents[1]
sys.path.insert(0, str(SCRIPT_DIR))

import automate_foundry_remaining as foundry  # noqa: E402


def run(command: list[str], check: bool = True) -> str:
    result = subprocess.run(command, capture_output=True, text=True)
    if check and result.returncode != 0:
        raise RuntimeError(
            f"Command failed: {' '.join(command)}\n{result.stderr.strip()}"
        )
    return result.stdout.strip()


def load_azd_environment(environment: str) -> None:
    raw = run(["azd", "env", "get-values", "--environment", environment])
    for line in raw.splitlines():
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if value.startswith('"') and value.endswith('"'):
            try:
                value = str(ast.literal_eval(value))
            except (SyntaxError, ValueError):
                value = value[1:-1]
        if key:
            os.environ[key] = value
    os.environ["AZURE_ENV_NAME"] = environment


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Retry Foundry Fabric connection and full orchestrator only"
    )
    parser.add_argument(
        "--environment",
        default=os.getenv("AZURE_ENV_NAME", ""),
        help="azd environment to load",
    )
    parser.add_argument(
        "--max-attempts",
        type=int,
        default=int(os.getenv("FOUNDRY_SELF_HEAL_MAX_ATTEMPTS", "3")),
    )
    parser.add_argument(
        "--retry-delay-seconds",
        type=int,
        default=int(os.getenv("FOUNDRY_SELF_HEAL_RETRY_DELAY_SECONDS", "60")),
    )
    parser.add_argument(
        "--connection-attempts",
        type=int,
        default=int(os.getenv("FOUNDRY_SELF_HEAL_CONNECTION_ATTEMPTS", "1")),
        help="HTTP attempts per API version inside each self-heal round",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Resolve configuration and print the recovery plan without calling Foundry",
    )
    parser.add_argument(
        "--disable-bicep-fallback",
        action="store_true",
        help="Do not try the documented Bicep child-resource route after REST failure",
    )
    parser.add_argument(
        "--status-path",
        default=os.getenv(
            "FOUNDRY_SELF_HEAL_STATUS_PATH", "logs/foundry_self_heal_status.json"
        ),
    )
    return parser.parse_args()


def connection_resource_url(cfg: Any, connection_name: str, api_version: str) -> str:
    return (
        "https://management.azure.com"
        f"/subscriptions/{cfg.subscription_id}"
        f"/resourceGroups/{cfg.resource_group}"
        f"/providers/Microsoft.CognitiveServices/accounts/{cfg.hub_name}"
        f"/projects/{cfg.project_name}/connections/{connection_name}"
        f"?api-version={api_version}"
    )


def get_existing_connection(cfg: Any, connection_name: str) -> str:
    token = foundry.get_token(scope="https://management.azure.com/.default")
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    for api_version in ("2025-10-01-preview", "2025-06-01"):
        response = requests.get(
            connection_resource_url(cfg, connection_name, api_version),
            headers=headers,
            timeout=60,
        )
        if response.status_code == 200:
            payload = response.json()
            return str(
                payload.get(
                    "id",
                    connection_resource_url(cfg, connection_name, api_version).split("?")[0],
                )
            )
        if response.status_code not in (404, 409):
            raise RuntimeError(
                f"Get Foundry connection '{connection_name}' failed "
                f"(HTTP {response.status_code}): {response.text[:1000]}"
            )
    return ""


def write_status(path_value: str, payload: dict[str, Any]) -> None:
    path = Path(path_value)
    if not path.is_absolute():
        path = REPO_ROOT / path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    print(f"[OK] Self-heal status written: {path}")


def attempt_recovery(
    cfg: Any,
    connection_attempts: int,
    allow_bicep_fallback: bool,
) -> dict[str, Any]:
    if cfg.fabric_mode == "disabled":
        raise RuntimeError("Foundry Fabric self-heal is disabled by FOUNDRY_FABRIC_MODE")

    workspace_id, data_agent_id = foundry.find_fabric_workspace_and_agent_ids(cfg)
    connection_name = foundry.active_fabric_connection_name(cfg)
    fabric_mcp_endpoint = foundry.build_fabric_mcp_endpoint(
        cfg,
        workspace_id=workspace_id,
        data_agent_id=data_agent_id,
    )
    fabric_connection_id = get_existing_connection(
        cfg, connection_name
    )
    connection_action = "existing"
    if not fabric_connection_id:
        connection_action = "create"
        try:
            fabric_connection_id = foundry.create_or_update_foundry_connection(
                cfg,
                connection_name=connection_name,
                properties=foundry.build_fabric_connection_properties(
                    cfg,
                    workspace_id=workspace_id,
                    data_agent_id=data_agent_id,
                ),
                api_version=(
                    "2025-10-01-preview"
                    if cfg.fabric_mode == "fabric_iq"
                    else "2025-06-01"
                ),
                max_attempts=max(1, connection_attempts),
                initial_delay_seconds=5,
            )
        except Exception as rest_error:
            if cfg.fabric_mode != "legacy" or not allow_bicep_fallback:
                raise
            print(f"[WARN] REST connection route failed; trying Bicep fallback: {rest_error}")
            fabric_connection_id = foundry.create_foundry_connection_via_bicep(
                cfg,
                connection_name=connection_name,
                workspace_id=workspace_id,
                data_agent_id=data_agent_id,
            )
            connection_action = "bicep"

    kb_connection_id = get_existing_connection(cfg, cfg.foundry_kb_connection_name)
    if not kb_connection_id:
        raise RuntimeError(
            f"KB connection '{cfg.foundry_kb_connection_name}' was not found; "
            "run automate_foundry_remaining.py once before self-heal."
        )

    kb_endpoint = (
        f"https://{cfg.search_service_name}.search.windows.net/knowledgebases/"
        f"{cfg.search_knowledge_base_name}/mcp?api-version=2026-05-01-preview"
    )
    foundry.upsert_orchestrator_agent(
        cfg,
        fabric_connection_id=fabric_connection_id,
        fabric_mcp_endpoint=fabric_mcp_endpoint,
        kb_connection_id=kb_connection_id,
        kb_mcp_endpoint=kb_endpoint,
    )
    return {
        "status": "succeeded",
        "connectionAction": connection_action,
        "workspaceId": workspace_id,
        "dataAgentId": data_agent_id,
        "fabricMode": cfg.fabric_mode,
        "fabricConnectionId": fabric_connection_id,
        "kbConnectionId": kb_connection_id,
        "orchestratorAgent": cfg.orchestrator_agent_name,
    }


def main() -> int:
    args = parse_args()
    if not args.environment:
        print("[ERROR] --environment or AZURE_ENV_NAME is required.")
        return 2

    load_azd_environment(args.environment)
    cfg = foundry.load_cfg()
    plan = {
        "tsUtc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "environment": args.environment,
        "region": cfg.location if hasattr(cfg, "location") else os.getenv("LOCATION", ""),
        "projectName": cfg.project_name,
        "fabricMode": cfg.fabric_mode,
        "fabricConnection": foundry.active_fabric_connection_name(cfg),
        "kbConnection": cfg.foundry_kb_connection_name,
        "orchestratorAgent": cfg.orchestrator_agent_name,
        "maxAttempts": max(1, args.max_attempts),
    }
    print(json.dumps(plan, indent=2, sort_keys=True))
    if args.dry_run:
        return 0

    status: dict[str, Any] = {"plan": plan, "attempts": []}
    for attempt in range(1, max(1, args.max_attempts) + 1):
        print(f"[STEP] Self-heal attempt {attempt}/{max(1, args.max_attempts)}")
        try:
            result = attempt_recovery(
                cfg,
                args.connection_attempts,
                allow_bicep_fallback=not args.disable_bicep_fallback
                and os.getenv("FOUNDRY_CONNECTION_BICEP_FALLBACK", "true").strip().lower() == "true",
            )
            result["attempt"] = attempt
            status["attempts"].append(result)
            status["result"] = result
            write_status(args.status_path, status)
            print("[OK] Foundry self-heal completed")
            return 0
        except Exception as ex:
            error = {"attempt": attempt, "status": "failed", "error": str(ex)}
            status["attempts"].append(error)
            print(f"[WARN] Self-heal attempt failed: {ex}")
            if attempt < max(1, args.max_attempts):
                time.sleep(max(1, args.retry_delay_seconds))

    status["result"] = {"status": "blocked", "message": "Fabric connection remains unavailable"}
    write_status(args.status_path, status)
    foundry.append_blocker_log(
        cfg,
        stage="foundry_self_heal",
        message="Self-heal exhausted without creating the full Fabric-enabled orchestrator.",
        details=status,
    )
    return 2


if __name__ == "__main__":
    sys.exit(main())
