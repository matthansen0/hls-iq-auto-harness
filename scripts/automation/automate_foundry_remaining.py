#!/usr/bin/env python3
"""
Automate the remaining Foundry/Fabric setup steps after infrastructure + launcher.

This script is intentionally idempotent and best-effort by default.
Set FOUNDRY_AUTOMATION_ENFORCE_SUCCESS=true to fail fast on any step failure.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote, urljoin

import requests


DEFAULT_FABRIC_DATA_AGENT_TABLES = (
    "fact_encounter",
    "fact_claim",
    "fact_prescription",
    "fact_diagnosis",
    "dim_patient",
    "dim_provider",
    "dim_payer",
    "dim_diagnosis",
    "dim_medication",
    "dim_sdoh",
    "dim_date",
    "agg_readmission_by_date",
    "agg_medication_adherence",
)


@dataclass
class Cfg:
    subscription_id: str
    resource_group: str
    hub_name: str
    project_name: str
    search_service_name: str
    fabric_workspace_name: str
    fabric_data_agent_name: str
    foundry_model_deployment_name: str
    foundry_model_name: str
    foundry_model_resource_uri: str
    foundry_embedding_deployment_name: str
    foundry_embedding_model_name: str
    fabric_mode: str
    foundry_fabric_connection_name: str
    foundry_fabric_iq_connection_name: str
    fabric_iq_mcp_endpoint_override: str
    search_knowledge_source_name: str
    search_knowledge_base_name: str
    search_knowledge_mode: str
    search_knowledge_index_name: str
    search_knowledge_directory: str
    search_knowledge_onelake_target_path: str
    search_knowledge_ingestion_interval: str
    search_knowledge_ingestion_timeout_seconds: int
    search_knowledge_ingestion_poll_seconds: int
    search_knowledge_retrieval_reasoning_effort: str
    foundry_kb_connection_name: str
    orchestrator_agent_name: str
    include_web_search_tool: bool
    allow_kb_only_agent_fallback: bool
    kb_only_agent_name: str
    enforce_success: bool
    instructions_file: str
    blocker_log_path: str
    status_report_path: str
    fabric_lakehouse_name: str
    fabric_data_agent_tables: tuple[str, ...]
    fabric_data_agent_routing: str
    fabric_data_agent_routing_snapshot_path: str
    fabric_data_agent_definition_directory: str
    fabric_data_agent_semantic_source: str
    fabric_data_agent_semantic_source_snapshot_path: str


def run(cmd: list[str], check: bool = True, timeout: int | None = None) -> str:
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired as ex:
        raise RuntimeError(
            f"Command timed out after {timeout}s: {' '.join(cmd)}"
        ) from ex
    if check and p.returncode != 0:
        raise RuntimeError(f"Command failed: {' '.join(cmd)}\n{p.stderr.strip()}")
    return p.stdout.strip()


def get_token(*, resource: str | None = None, scope: str | None = None) -> str:
    cmd = ["az", "account", "get-access-token", "--query", "accessToken", "-o", "tsv"]
    if scope:
        cmd.extend(["--scope", scope])
    elif resource:
        cmd.extend(["--resource", resource])
    else:
        raise ValueError("resource or scope is required")
    return run(cmd)


def get_search_admin_key(cfg: Cfg) -> str:
    return run(
        [
            "az",
            "search",
            "admin-key",
            "show",
            "-g",
            cfg.resource_group,
            "--service-name",
            cfg.search_service_name,
            "--query",
            "primaryKey",
            "-o",
            "tsv",
        ]
    )


def to_bool(value: str, default: bool = False) -> bool:
    raw = (value or "").strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "y", "on"}


def load_cfg() -> Cfg:
    required = [
        "AZURE_SUBSCRIPTION_ID",
        "AZURE_RESOURCE_GROUP",
        "HUB_NAME",
        "SEARCH_SERVICE_NAME",
    ]
    missing = [name for name in required if not os.getenv(name, "").strip()]
    if missing:
        raise ValueError(f"Missing required env values: {', '.join(missing)}")

    configured_tables = tuple(
        item.strip()
        for item in os.getenv("FABRIC_DATA_AGENT_TABLES", "").split(",")
        if item.strip()
    )
    fabric_mode = os.getenv("FOUNDRY_FABRIC_MODE", "fabric_iq").strip().lower()
    if fabric_mode not in {"fabric_iq", "legacy", "disabled"}:
        raise ValueError(
            "FOUNDRY_FABRIC_MODE must be one of: fabric_iq, legacy, disabled"
        )
    search_knowledge_mode = os.getenv(
        "SEARCH_KNOWLEDGE_MODE", "onelake"
    ).strip().lower()
    if search_knowledge_mode not in {"onelake", "local_index", "fabric_data_agent"}:
        raise ValueError(
            "SEARCH_KNOWLEDGE_MODE must be one of: onelake, local_index, fabric_data_agent"
        )
    onelake_target_path = os.getenv(
        "SEARCH_KNOWLEDGE_ONELAKE_TARGET_PATH", "healthcare_knowledge"
    ).strip().strip("/")
    if onelake_target_path.lower().startswith("files/"):
        onelake_target_path = onelake_target_path[6:]
    if search_knowledge_mode == "onelake" and not onelake_target_path:
        raise ValueError("SEARCH_KNOWLEDGE_ONELAKE_TARGET_PATH must name a lakehouse Files folder")
    retrieval_reasoning_effort = os.getenv(
        "SEARCH_KNOWLEDGE_RETRIEVAL_REASONING_EFFORT", "medium"
    ).strip().lower()
    if retrieval_reasoning_effort not in {"minimal", "low", "medium"}:
        raise ValueError(
            "SEARCH_KNOWLEDGE_RETRIEVAL_REASONING_EFFORT must be one of: minimal, low, medium"
        )
    fabric_data_agent_routing = os.getenv(
        "FABRIC_DATA_AGENT_ROUTING", "lakehouse_primary"
    ).strip().lower()
    if fabric_data_agent_routing not in {"lakehouse_primary", "preserve", "restore"}:
        raise ValueError(
            "FABRIC_DATA_AGENT_ROUTING must be one of: lakehouse_primary, preserve, restore"
        )
    fabric_data_agent_semantic_source = os.getenv(
        "FABRIC_DATA_AGENT_SEMANTIC_SOURCE", "remove"
    ).strip().lower()
    if fabric_data_agent_semantic_source not in {"remove", "preserve", "restore"}:
        raise ValueError(
            "FABRIC_DATA_AGENT_SEMANTIC_SOURCE must be one of: remove, preserve, restore"
        )

    return Cfg(
        subscription_id=os.getenv("AZURE_SUBSCRIPTION_ID", "").strip(),
        resource_group=os.getenv("AZURE_RESOURCE_GROUP", "").strip(),
        hub_name=os.getenv("HUB_NAME", "").strip(),
        project_name=os.getenv("PROJECT_NAME", "HealthcareDemo-HLS").strip(),
        search_service_name=os.getenv("SEARCH_SERVICE_NAME", "").strip(),
        fabric_workspace_name=os.getenv("FABRIC_WORKSPACE_NAME", "HealthcareDemo-WS").strip(),
        fabric_data_agent_name=os.getenv("FABRIC_DATA_AGENT_NAME", "HealthcareHLSAgent").strip(),
        foundry_model_deployment_name=os.getenv("FOUNDRY_CHAT_DEPLOYMENT_NAME", "gpt-5.4").strip(),
        foundry_model_name=os.getenv("FOUNDRY_CHAT_MODEL_NAME", "gpt-5.4").strip(),
        foundry_model_resource_uri=os.getenv(
            "FOUNDRY_MODEL_RESOURCE_URI",
            f"https://{os.getenv('HUB_NAME', '').strip()}.openai.azure.com",
        ).strip().rstrip("/"),
        foundry_embedding_deployment_name=os.getenv(
            "FOUNDRY_EMBEDDING_DEPLOYMENT_NAME", "text-embedding-ada-002"
        ).strip(),
        foundry_embedding_model_name=os.getenv(
            "FOUNDRY_EMBEDDING_MODEL_NAME", "text-embedding-ada-002"
        ).strip(),
        fabric_mode=fabric_mode,
        foundry_fabric_connection_name=os.getenv("FOUNDRY_FABRIC_CONNECTION_NAME", "HealthcareHLSAgent").strip(),
        foundry_fabric_iq_connection_name=os.getenv(
            "FOUNDRY_FABRIC_IQ_CONNECTION_NAME", "healthcare-fabric-iq"
        ).strip(),
        fabric_iq_mcp_endpoint_override=os.getenv(
            "FOUNDRY_FABRIC_IQ_MCP_ENDPOINT", ""
        ).strip(),
        search_knowledge_source_name=os.getenv(
            "SEARCH_KNOWLEDGE_SOURCE_NAME", "healthcare-policy-ks"
        ).strip(),
        search_knowledge_base_name=os.getenv("SEARCH_KNOWLEDGE_BASE_NAME", "healthcareknowledgebase").strip(),
        search_knowledge_mode=search_knowledge_mode,
        search_knowledge_index_name=os.getenv(
            "SEARCH_KNOWLEDGE_INDEX_NAME", "healthcare-policy-index"
        ).strip(),
        search_knowledge_directory=os.getenv(
            "SEARCH_KNOWLEDGE_DIRECTORY", "fabric-main/healthcare_knowledge"
        ).strip(),
        search_knowledge_onelake_target_path=onelake_target_path,
        search_knowledge_ingestion_interval=os.getenv(
            "SEARCH_KNOWLEDGE_INGESTION_INTERVAL", "P1D"
        ).strip(),
        search_knowledge_ingestion_timeout_seconds=max(
            60,
            int(os.getenv("SEARCH_KNOWLEDGE_INGESTION_TIMEOUT_SECONDS", "900")),
        ),
        search_knowledge_ingestion_poll_seconds=max(
            5,
            int(os.getenv("SEARCH_KNOWLEDGE_INGESTION_POLL_SECONDS", "15")),
        ),
        search_knowledge_retrieval_reasoning_effort=retrieval_reasoning_effort,
        foundry_kb_connection_name=os.getenv("FOUNDRY_KB_CONNECTION_NAME", "healthcare-kb-connection").strip(),
        orchestrator_agent_name=os.getenv("FOUNDRY_ORCHESTRATOR_AGENT_NAME", "HealthcareOrchestratorAgent2").strip(),
        include_web_search_tool=to_bool(os.getenv("FOUNDRY_INCLUDE_WEB_SEARCH", "true"), default=True),
        allow_kb_only_agent_fallback=to_bool(
            os.getenv("FOUNDRY_ALLOW_KB_ONLY_AGENT_FALLBACK", "true"), default=True
        ),
        kb_only_agent_name=os.getenv(
            "FOUNDRY_KB_ONLY_AGENT_NAME",
            "HealthcareOrchestratorKBOnly",
        ).strip(),
        enforce_success=to_bool(os.getenv("FOUNDRY_AUTOMATION_ENFORCE_SUCCESS", "false"), default=False),
        instructions_file=os.getenv(
            "FOUNDRY_ORCHESTRATOR_INSTRUCTIONS_FILE",
            "config/orchestrator_instructions.md",
        ).strip(),
        blocker_log_path=os.getenv(
            "FOUNDRY_BLOCKER_LOG_PATH",
            "logs/foundry_connection_blockers.jsonl",
        ).strip(),
        status_report_path=os.getenv(
            "FOUNDRY_STATUS_REPORT_PATH",
            "logs/foundry_completion_status.json",
        ).strip(),
        fabric_lakehouse_name=os.getenv(
            "FABRIC_DATA_AGENT_LAKEHOUSE_NAME",
            "lh_gold_curated",
        ).strip(),
        fabric_data_agent_tables=configured_tables or DEFAULT_FABRIC_DATA_AGENT_TABLES,
        fabric_data_agent_routing=fabric_data_agent_routing,
        fabric_data_agent_routing_snapshot_path=os.getenv(
            "FABRIC_DATA_AGENT_ROUTING_SNAPSHOT_PATH",
            "logs/data_agent_routing_snapshot.json",
        ).strip(),
        fabric_data_agent_definition_directory=os.getenv(
            "FABRIC_DATA_AGENT_DEFINITION_DIRECTORY",
            "fabric-main/workspace/HealthcareHLSAgent.DataAgent/Files/Config/draft",
        ).strip(),
        fabric_data_agent_semantic_source=fabric_data_agent_semantic_source,
        fabric_data_agent_semantic_source_snapshot_path=os.getenv(
            "FABRIC_DATA_AGENT_SEMANTIC_SOURCE_SNAPSHOT_PATH",
            "logs/data_agent_semantic_source_snapshot.json",
        ).strip(),
    )


def _resolve_repo_relative_path(path_str: str) -> Path:
    path = Path(path_str)
    if path.is_absolute():
        return path
    return Path(__file__).resolve().parents[2] / path


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def append_blocker_log(cfg: Cfg, *, stage: str, message: str, details: dict[str, Any]) -> None:
    path = _resolve_repo_relative_path(cfg.blocker_log_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    event = {
        "tsUtc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "stage": stage,
        "message": message,
        "details": details,
    }
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(event, sort_keys=True) + "\n")
    print(f"[TRACK] Blocker event logged: {path}")


def _json_or_error(resp: requests.Response) -> dict[str, Any]:
    try:
        return resp.json()
    except Exception:
        return {"raw": resp.text}


def _raise_if_bad(resp: requests.Response, step: str) -> None:
    if 200 <= resp.status_code < 300:
        return
    payload = _json_or_error(resp)
    raise RuntimeError(f"{step} failed (HTTP {resp.status_code}): {json.dumps(payload)}")


def _request_with_retry(
    *,
    method: str,
    url: str,
    headers: dict[str, str],
    body: dict[str, Any],
    step: str,
    timeout: int = 90,
    max_attempts: int = 5,
    initial_delay_seconds: int = 8,
) -> requests.Response:
    attempt = 1
    delay = max(initial_delay_seconds, 2)
    last_resp: requests.Response | None = None
    last_error: Exception | None = None

    while attempt <= max_attempts:
        try:
            resp = requests.request(method, url, headers=headers, json=body, timeout=timeout)
            last_resp = resp
            if 200 <= resp.status_code < 300:
                return resp

            if resp.status_code not in (429, 500, 502, 503, 504) or attempt == max_attempts:
                return resp

            retry_after_raw = resp.headers.get("Retry-After", "").strip()
            retry_after = int(retry_after_raw) if retry_after_raw.isdigit() else delay
            print(
                f"[WARN] {step} transient failure HTTP {resp.status_code} "
                f"(attempt {attempt}/{max_attempts}); retrying in {retry_after}s"
            )
            time.sleep(retry_after)
            delay = min(delay * 2, 60)
            attempt += 1
            continue
        except Exception as ex:
            last_error = ex
            if attempt == max_attempts:
                break
            print(
                f"[WARN] {step} request error (attempt {attempt}/{max_attempts}): {ex}; "
                f"retrying in {delay}s"
            )
            time.sleep(delay)
            delay = min(delay * 2, 60)
            attempt += 1

    if last_resp is not None:
        return last_resp
    raise RuntimeError(f"{step} failed after retries: {last_error}")


def find_fabric_workspace_and_agent_ids(cfg: Cfg) -> tuple[str, str]:
    token = get_token(resource="https://api.fabric.microsoft.com")
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    ws_resp = requests.get("https://api.fabric.microsoft.com/v1/workspaces", headers=headers, timeout=60)
    _raise_if_bad(ws_resp, "List Fabric workspaces")
    ws_id = ""
    for ws in ws_resp.json().get("value", []):
        if ws.get("displayName") == cfg.fabric_workspace_name:
            ws_id = str(ws.get("id", ""))
            break
    if not ws_id:
        raise RuntimeError(f"Fabric workspace not found: {cfg.fabric_workspace_name}")

    items_resp = requests.get(
        f"https://api.fabric.microsoft.com/v1/workspaces/{ws_id}/items?type=DataAgent",
        headers=headers,
        timeout=60,
    )
    _raise_if_bad(items_resp, "List Fabric DataAgent items")
    data_agent_id = ""
    for item in items_resp.json().get("value", []):
        if item.get("displayName") == cfg.fabric_data_agent_name:
            data_agent_id = str(item.get("id", ""))
            break
    if not data_agent_id:
        raise RuntimeError(f"Fabric data agent not found: {cfg.fabric_data_agent_name}")

    return ws_id, data_agent_id


def find_fabric_item_id(
    *,
    workspace_id: str,
    item_type: str,
    display_name: str,
) -> str:
    token = get_token(resource="https://api.fabric.microsoft.com")
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    response = requests.get(
        "https://api.fabric.microsoft.com/v1/workspaces/"
        f"{workspace_id}/items?type={quote(item_type, safe='')}",
        headers=headers,
        timeout=60,
    )
    _raise_if_bad(response, f"List Fabric {item_type} items")
    for item in _json_or_error(response).get("value", []):
        if item.get("displayName") == display_name:
            item_id = str(item.get("id") or "").strip()
            if item_id:
                return item_id
    raise RuntimeError(f"Fabric item not found: {item_type}/{display_name}")


def _get_staging_elements(
    *,
    staging_elements_url: str,
    headers: dict[str, str],
    root_id: str | None = None,
) -> list[dict[str, Any]]:
    params = {"rootId": root_id} if root_id else None
    response = requests.get(staging_elements_url, headers=headers, params=params, timeout=90)
    _raise_if_bad(response, "List Fabric Data Agent staging elements")
    payload = _json_or_error(response)
    return payload.get("value", [])


def _list_staging_table_elements(
    *,
    staging_elements_url: str,
    headers: dict[str, str],
) -> list[dict[str, Any]]:
    pending = _get_staging_elements(
        staging_elements_url=staging_elements_url,
        headers=headers,
    )
    visited_roots: set[str] = set()
    table_elements: list[dict[str, Any]] = []

    while pending:
        element = pending.pop()
        element_id = str(element.get("id", "")).strip()
        element_type = str(element.get("type", "")).strip().lower()
        if element_type == "table":
            table_elements.append(element)
            continue
        if not element_id or not element.get("hasSubElements") or element_id in visited_roots:
            continue
        visited_roots.add(element_id)
        pending.extend(
            _get_staging_elements(
                staging_elements_url=staging_elements_url,
                headers=headers,
                root_id=element_id,
            )
        )

    return table_elements


def _wait_for_staging_publish(
    response: requests.Response,
    *,
    headers: dict[str, str],
    step: str,
) -> None:
    if response.status_code != 202:
        return

    payload = _json_or_error(response)
    operation_url = response.headers.get("Location", "").strip()
    if not operation_url:
        operation_url = str(
            payload.get("operationUrl")
            or payload.get("statusQueryGetUri")
            or payload.get("location")
            or ""
        ).strip()
    if not operation_url:
        raise RuntimeError(f"{step} returned HTTP 202 without an operation URL")
    operation_url = urljoin(response.url, operation_url)

    deadline = time.monotonic() + int(os.getenv("FABRIC_DATA_AGENT_PUBLISH_TIMEOUT_SECONDS", "300"))
    while time.monotonic() < deadline:
        time.sleep(5)
        poll = requests.get(operation_url, headers=headers, timeout=90)
        _raise_if_bad(poll, f"Poll {step}")
        poll_payload = _json_or_error(poll)
        status = str(poll_payload.get("status") or poll_payload.get("state") or "").lower()
        if status in {"succeeded", "success", "completed"} or (poll.status_code == 200 and not status):
            return
        if status in {"failed", "cancelled", "canceled", "rejected"}:
            raise RuntimeError(f"{step} ended with status {status}: {json.dumps(poll_payload)}")

    raise RuntimeError(f"{step} did not complete within the configured timeout")


def ensure_fabric_data_agent_table_selection(
    cfg: Cfg,
    *,
    workspace_id: str,
    data_agent_id: str,
) -> dict[str, Any]:
    token = get_token(resource="https://api.fabric.microsoft.com")
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    agent_url = f"https://api.fabric.microsoft.com/v1/workspaces/{workspace_id}/dataAgents/{data_agent_id}"
    datasources_url = f"{agent_url}/staging/datasources"

    datasources_response = requests.get(datasources_url, headers=headers, timeout=90)
    _raise_if_bad(datasources_response, "List Fabric Data Agent staging datasources")
    datasources = _json_or_error(datasources_response).get("value", [])
    lakehouse = next(
        (
            source
            for source in datasources
            if str(source.get("displayName") or source.get("name") or "").strip()
            == cfg.fabric_lakehouse_name
        ),
        None,
    )
    if lakehouse is None:
        lakehouse = next(
            (source for source in datasources if source.get("type") == "LakehouseTables"),
            None,
        )
    if lakehouse is None:
        raise RuntimeError(
            f"Fabric Data Agent lakehouse datasource not found: {cfg.fabric_lakehouse_name}"
        )

    datasource_id = str(lakehouse.get("id") or lakehouse.get("itemId") or "").strip()
    if not datasource_id:
        raise RuntimeError("Fabric Data Agent lakehouse datasource has no ID")

    elements_url = f"{datasources_url}/{datasource_id}/elements"
    table_elements = _list_staging_table_elements(
        staging_elements_url=elements_url,
        headers=headers,
    )
    table_by_name = {
        str(element.get("displayName", "")).strip(): element
        for element in table_elements
        if str(element.get("displayName", "")).strip()
    }
    missing_tables = [name for name in cfg.fabric_data_agent_tables if name not in table_by_name]
    if missing_tables:
        raise RuntimeError(
            f"Fabric Data Agent staging source is missing required tables: {', '.join(missing_tables)}"
        )

    changed_tables: list[str] = []
    for table_name in cfg.fabric_data_agent_tables:
        element = table_by_name[table_name]
        if element.get("isSelected"):
            continue
        element_id = str(element.get("id", "")).strip()
        if not element_id:
            raise RuntimeError(f"Fabric Data Agent table has no element ID: {table_name}")
        response = _request_with_retry(
            method="PATCH",
            url=f"{elements_url}?id={quote(element_id, safe='')}",
            headers=headers,
            body={"isSelected": True},
            step=f"Select Fabric Data Agent table '{table_name}'",
            timeout=90,
            max_attempts=3,
            initial_delay_seconds=3,
        )
        _raise_if_bad(response, f"Select Fabric Data Agent table '{table_name}'")
        changed_tables.append(table_name)

    if changed_tables:
        agent_response = requests.get(agent_url, headers=headers, timeout=90)
        _raise_if_bad(agent_response, "Get Fabric Data Agent publish description")
        agent_payload = _json_or_error(agent_response)
        description = str(
            agent_payload.get("properties", {}).get("publishedDescription", "")
        ).strip()
        publish_response = _request_with_retry(
            method="POST",
            url=f"{agent_url}/staging/publish",
            headers=headers,
            body={"publishedDescription": description} if description else {},
            step="Publish Fabric Data Agent staging selection",
            timeout=90,
            max_attempts=3,
            initial_delay_seconds=5,
        )
        _raise_if_bad(publish_response, "Publish Fabric Data Agent staging selection")
        _wait_for_staging_publish(
            publish_response,
            headers=headers,
            step="Fabric Data Agent staging publish",
        )

    verified_elements = _list_staging_table_elements(
        staging_elements_url=elements_url,
        headers=headers,
    )
    selected_tables = sorted(
        str(element.get("displayName", "")).strip()
        for element in verified_elements
        if element.get("isSelected") and str(element.get("displayName", "")).strip()
    )
    unselected_required = [
        name for name in cfg.fabric_data_agent_tables if name not in selected_tables
    ]
    if unselected_required:
        raise RuntimeError(
            "Fabric Data Agent table selection did not persist for: "
            + ", ".join(unselected_required)
        )
    return {
        "status": "ok",
        "datasourceId": datasource_id,
        "lakehouseName": cfg.fabric_lakehouse_name,
        "requiredTables": list(cfg.fabric_data_agent_tables),
        "selectedTables": selected_tables,
        "changedTables": changed_tables,
        "published": bool(changed_tables),
    }


def _load_data_agent_source_metadata(cfg: Cfg) -> dict[str, dict[str, str]]:
    definition_root = _resolve_repo_relative_path(
        cfg.fabric_data_agent_definition_directory
    )
    if not definition_root.exists():
        raise RuntimeError(
            f"Fabric Data Agent definition directory not found: {definition_root}"
        )

    metadata: dict[str, dict[str, str]] = {}
    for path in sorted(definition_root.glob("*/datasource.json")):
        payload = json.loads(path.read_text(encoding="utf-8"), strict=False)
        display_name = str(payload.get("displayName") or "").strip()
        if not display_name:
            continue
        metadata[display_name] = {
            "description": str(payload.get("userDescription") or ""),
            "instructions": str(payload.get("dataSourceInstructions") or ""),
        }
    if cfg.fabric_data_agent_routing == "lakehouse_primary":
        if "lh_gold_curated" in metadata:
            metadata["lh_gold_curated"]["description"] = (
                "PRIMARY governed SQL source for healthcare KPIs and row-level detail. "
                f"Gold-layer lakehouse with {len(cfg.fabric_data_agent_tables)} selected "
                "analytical tables covering encounters, claims, prescriptions, diagnoses, "
                "medication adherence, SDOH, providers, payers, patients, and dates. Use its "
                "validated example queries for rates, percentages, totals, counts, trends, "
                "breakdowns, costs, and detail lookups. Use the semantic model only when the "
                "user explicitly requests a named DAX measure or report-specific calculation."
            )
        if "HealthcareDemoHLS" in metadata:
            metadata["HealthcareDemoHLS"]["description"] = (
                "OPTIONAL semantic-model source for explicit named DAX measures and "
                "report-specific calculations only."
            )
            metadata["HealthcareDemoHLS"]["instructions"] = (
                "OPTIONAL SOURCE FOR EXPLICIT NAMED DAX MEASURES ONLY. "
                "Use this semantic model only when the user explicitly requests a named measure "
                "or report-specific calculation such as MTD, YTD, YoY, PMPM, or a measure shown "
                "in the HealthcareDemoHLS report. For normal rates, counts, totals, trends, payer "
                "breakdowns, patient detail, claim detail, adherence, costs, diagnoses, providers, "
                "or SDOH questions, use the primary lh_gold_curated lakehouse source. Never add a "
                "date filter unless the user explicitly requests a time period."
            )
    if not metadata:
        raise RuntimeError(
            f"No datasource metadata found under: {definition_root}"
        )
    return metadata


def _load_data_agent_fewshots(cfg: Cfg) -> dict[str, list[dict[str, str]]]:
    definition_root = _resolve_repo_relative_path(
        cfg.fabric_data_agent_definition_directory
    )
    fewshots_by_source: dict[str, list[dict[str, str]]] = {}
    for path in sorted(definition_root.glob("*/fewshots.json")):
        datasource_path = path.with_name("datasource.json")
        if not datasource_path.exists():
            continue
        datasource = json.loads(
            datasource_path.read_text(encoding="utf-8"), strict=False
        )
        display_name = str(datasource.get("displayName") or "").strip()
        payload = json.loads(path.read_text(encoding="utf-8"), strict=False)
        values = payload.get("fewShots") or []
        if display_name:
            fewshots_by_source[display_name] = [
                {
                    "id": str(item.get("id") or ""),
                    "question": str(item.get("question") or ""),
                    "query": str(item.get("query") or ""),
                }
                for item in values
                if item.get("id") and item.get("question") and item.get("query")
            ]
    if cfg.fabric_data_agent_routing == "lakehouse_primary":
        for item in fewshots_by_source.get("lh_gold_curated", []):
            if item["id"] == "607669a7-e39a-4456-bff4-80f32949f6d9":
                item["question"] = (
                    "Show me denial rates by payer using all available data with no date filter"
                )
    return fewshots_by_source


def ensure_fabric_data_agent_source_metadata(
    cfg: Cfg,
    *,
    workspace_id: str,
    data_agent_id: str,
) -> dict[str, Any]:
    token = get_token(resource="https://api.fabric.microsoft.com")
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    agent_url = (
        "https://api.fabric.microsoft.com/v1/workspaces/"
        f"{workspace_id}/dataAgents/{data_agent_id}"
    )
    datasources_url = f"{agent_url}/staging/datasources"
    datasources_response = requests.get(datasources_url, headers=headers, timeout=90)
    _raise_if_bad(datasources_response, "List Fabric Data Agent staging datasources")
    live_datasources = _json_or_error(datasources_response).get("value", [])
    desired_by_name = _load_data_agent_source_metadata(cfg)
    changed_sources: list[str] = []

    for source in live_datasources:
        display_name = str(source.get("displayName") or "").strip()
        desired = desired_by_name.get(display_name)
        if not desired:
            continue
        patch_body = {
            key: value
            for key, value in desired.items()
            if str(source.get(key) or "") != value
        }
        if not patch_body:
            continue
        datasource_id = str(source.get("id") or "").strip()
        if not datasource_id:
            raise RuntimeError(f"Data Agent datasource has no ID: {display_name}")
        patch_response = _request_with_retry(
            method="PATCH",
            url=f"{datasources_url}/{quote(datasource_id, safe='')}",
            headers=headers,
            body=patch_body,
            step=f"Patch Data Agent datasource metadata '{display_name}'",
            timeout=90,
            max_attempts=3,
            initial_delay_seconds=3,
        )
        _raise_if_bad(
            patch_response,
            f"Patch Data Agent datasource metadata '{display_name}'",
        )
        verify_response = requests.get(
            f"{datasources_url}/{quote(datasource_id, safe='')}",
            headers=headers,
            timeout=90,
        )
        _raise_if_bad(
            verify_response,
            f"Verify Data Agent datasource metadata '{display_name}'",
        )
        verified = _json_or_error(verify_response)
        mismatched = [
            key
            for key, value in patch_body.items()
            if str(verified.get(key) or "") != value
        ]
        if mismatched:
            raise RuntimeError(
                f"Data Agent datasource metadata did not persist for {display_name}: "
                f"{', '.join(mismatched)}"
            )
        changed_sources.append(display_name)

    if changed_sources:
        agent_response = requests.get(agent_url, headers=headers, timeout=90)
        _raise_if_bad(agent_response, "Get Fabric Data Agent publish description")
        description = str(
            _json_or_error(agent_response).get("properties", {}).get(
                "publishedDescription", ""
            )
        ).strip()
        publish_response = _request_with_retry(
            method="POST",
            url=f"{agent_url}/staging/publish",
            headers=headers,
            body={"publishedDescription": description} if description else {},
            step="Publish Fabric Data Agent datasource metadata",
            timeout=90,
            max_attempts=3,
            initial_delay_seconds=5,
        )
        _raise_if_bad(
            publish_response,
            "Publish Fabric Data Agent datasource metadata",
        )
        _wait_for_staging_publish(
            publish_response,
            headers=headers,
            step="Fabric Data Agent datasource metadata publish",
        )

    missing_sources = sorted(set(desired_by_name) - {
        str(source.get("displayName") or "").strip()
        for source in live_datasources
    })
    return {
        "status": "ok",
        "changedSources": sorted(changed_sources),
        "missingSources": missing_sources,
        "published": bool(changed_sources),
    }


def ensure_fabric_data_agent_fewshots(
    cfg: Cfg,
    *,
    workspace_id: str,
    data_agent_id: str,
) -> dict[str, Any]:
    token = get_token(resource="https://api.fabric.microsoft.com")
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    agent_url = (
        "https://api.fabric.microsoft.com/v1/workspaces/"
        f"{workspace_id}/dataAgents/{data_agent_id}"
    )
    datasources_url = f"{agent_url}/staging/datasources"
    response = requests.get(datasources_url, headers=headers, timeout=90)
    _raise_if_bad(response, "List Fabric Data Agent staging datasources")
    live_datasources = _json_or_error(response).get("value", [])
    desired_by_name = _load_data_agent_fewshots(cfg)
    changed_examples: list[str] = []

    for source in live_datasources:
        display_name = str(source.get("displayName") or "").strip()
        desired = desired_by_name.get(display_name)
        if not desired:
            continue
        datasource_id = str(source.get("id") or "").strip()
        fewshots_url = f"{datasources_url}/{quote(datasource_id, safe='')}/fewshots"
        live_response = requests.get(fewshots_url, headers=headers, timeout=90)
        _raise_if_bad(live_response, f"List Data Agent few-shots '{display_name}'")
        live_by_id = {
            str(item.get("id") or ""): item
            for item in _json_or_error(live_response).get("value", [])
        }

        for item in desired:
            fewshot_id = item["id"]
            live = live_by_id.get(fewshot_id)
            body = {"question": item["question"], "query": item["query"]}
            if live and all(str(live.get(key) or "") == value for key, value in body.items()):
                continue
            if live:
                write_response = requests.patch(
                    f"{fewshots_url}/{quote(fewshot_id, safe='')}",
                    headers=headers,
                    json=body,
                    timeout=90,
                )
                expected = {200}
            else:
                write_response = requests.post(
                    fewshots_url,
                    headers=headers,
                    json={"id": fewshot_id, **body},
                    timeout=90,
                )
                expected = {200, 201}
            if write_response.status_code not in expected:
                _raise_if_bad(
                    write_response,
                    f"Write Data Agent few-shot '{display_name}/{fewshot_id}'",
                )
            verify_response = requests.get(
                f"{fewshots_url}/{quote(fewshot_id, safe='')}",
                headers=headers,
                timeout=90,
            )
            _raise_if_bad(
                verify_response,
                f"Verify Data Agent few-shot '{display_name}/{fewshot_id}'",
            )
            verified = _json_or_error(verify_response)
            if any(str(verified.get(key) or "") != value for key, value in body.items()):
                raise RuntimeError(
                    f"Data Agent few-shot did not persist: {display_name}/{fewshot_id}"
                )
            changed_examples.append(f"{display_name}/{fewshot_id}")

    if changed_examples:
        agent_response = requests.get(agent_url, headers=headers, timeout=90)
        _raise_if_bad(agent_response, "Get Fabric Data Agent publish description")
        description = str(
            _json_or_error(agent_response).get("properties", {}).get(
                "publishedDescription", ""
            )
        ).strip()
        publish_response = _request_with_retry(
            method="POST",
            url=f"{agent_url}/staging/publish",
            headers=headers,
            body={"publishedDescription": description} if description else {},
            step="Publish Fabric Data Agent few-shots",
            timeout=90,
            max_attempts=3,
            initial_delay_seconds=5,
        )
        _raise_if_bad(publish_response, "Publish Fabric Data Agent few-shots")
        _wait_for_staging_publish(
            publish_response,
            headers=headers,
            step="Fabric Data Agent few-shot publish",
        )

    return {
        "status": "ok",
        "changedExamples": sorted(changed_examples),
        "published": bool(changed_examples),
    }


def _semantic_source_restore_body(snapshot: dict[str, Any]) -> dict[str, Any]:
    source = snapshot.get("source") or {}
    item_reference = source.get("itemReference")
    if not isinstance(item_reference, dict):
        raise RuntimeError("Semantic source snapshot has no itemReference")
    return {
        "type": "FabricItem",
        "itemReference": item_reference,
    }


def ensure_fabric_data_agent_semantic_source(
    cfg: Cfg,
    *,
    workspace_id: str,
    data_agent_id: str,
) -> dict[str, Any]:
    if cfg.fabric_data_agent_semantic_source == "preserve":
        return {"status": "preserved", "changed": False}

    token = get_token(resource="https://api.fabric.microsoft.com")
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    agent_url = (
        "https://api.fabric.microsoft.com/v1/workspaces/"
        f"{workspace_id}/dataAgents/{data_agent_id}"
    )
    datasources_url = f"{agent_url}/staging/datasources"
    response = requests.get(datasources_url, headers=headers, timeout=90)
    _raise_if_bad(response, "List Fabric Data Agent staging datasources")
    datasources = _json_or_error(response).get("value", [])
    semantic_source = next(
        (
            source
            for source in datasources
            if str(source.get("displayName") or "") == "HealthcareDemoHLS"
        ),
        None,
    )
    snapshot_path = _resolve_repo_relative_path(
        cfg.fabric_data_agent_semantic_source_snapshot_path
    )
    changed = False

    if cfg.fabric_data_agent_semantic_source == "remove" and semantic_source:
        if not snapshot_path.exists():
            _write_json(
                snapshot_path,
                {
                    "tsUtc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "workspaceId": workspace_id,
                    "dataAgentId": data_agent_id,
                    "source": semantic_source,
                },
            )
        datasource_id = str(semantic_source.get("id") or "").strip()
        delete_response = requests.delete(
            f"{datasources_url}/{quote(datasource_id, safe='')}",
            headers=headers,
            timeout=90,
        )
        _raise_if_bad(delete_response, "Remove semantic-model datasource from Data Agent")
        changed = True

    if cfg.fabric_data_agent_semantic_source == "restore" and not semantic_source:
        if not snapshot_path.exists():
            raise RuntimeError(f"Semantic source snapshot not found: {snapshot_path}")
        snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
        add_response = requests.post(
            datasources_url,
            headers=headers,
            json=_semantic_source_restore_body(snapshot),
            timeout=90,
        )
        if add_response.status_code not in (200, 201, 202, 409):
            _raise_if_bad(add_response, "Restore semantic-model datasource to Data Agent")
        if add_response.status_code == 202:
            _wait_for_staging_publish(
                add_response,
                headers=headers,
                step="Semantic-model datasource restore",
            )
        changed = add_response.status_code != 409

    if changed:
        agent_response = requests.get(agent_url, headers=headers, timeout=90)
        _raise_if_bad(agent_response, "Get Fabric Data Agent publish description")
        description = str(
            _json_or_error(agent_response).get("properties", {}).get(
                "publishedDescription", ""
            )
        ).strip()
        publish_response = _request_with_retry(
            method="POST",
            url=f"{agent_url}/staging/publish",
            headers=headers,
            body={"publishedDescription": description} if description else {},
            step="Publish Fabric Data Agent semantic-source change",
            timeout=90,
            max_attempts=3,
            initial_delay_seconds=5,
        )
        _raise_if_bad(
            publish_response,
            "Publish Fabric Data Agent semantic-source change",
        )
        _wait_for_staging_publish(
            publish_response,
            headers=headers,
            step="Fabric Data Agent semantic-source publish",
        )

    return {
        "status": cfg.fabric_data_agent_semantic_source,
        "changed": changed,
        "semanticSourcePresent": (
            cfg.fabric_data_agent_semantic_source == "restore"
            if changed
            else semantic_source is not None
        ),
        "snapshotPath": str(snapshot_path),
    }


def _lakehouse_primary_instructions(current: str) -> str:
    routing_block = """TWO DATA SOURCES - PICK EXACTLY ONE PER QUESTION
1. lakehouse_tables 'lh_gold_curated' (PRIMARY, default) - validated SQL for rates, percentages, totals, counts, averages, trends, KPI breakdowns, and row-level detail.
2. semantic_model 'HealthcareDemoHLS' (OPTIONAL) - curated DAX measures for questions that explicitly request a named semantic-model measure or report-specific calculation.

ROUTING
- Default to the lakehouse for every catalog query and every question about rates, counts, trends, breakdowns, totals, patient or claim detail.
- The lakehouse has validated example queries for denial rate, readmission rate and distribution, medication adherence, costs, diagnoses, providers, payers, and SDOH.
- Use the semantic model only when the user explicitly requests a named DAX measure or a calculation unavailable in the lakehouse examples.
- Never combine both sources in one answer. If a question needs separate grains, answer one part and offer the other as a follow-up.
- When ambiguous, prefer the lakehouse.

RESPONSE"""
    pattern = re.compile(
        r"TWO DATA SOURCES - PICK EXACTLY ONE PER QUESTION.*?\n\nRESPONSE",
        flags=re.DOTALL,
    )
    if pattern.search(current):
        return pattern.sub(routing_block, current, count=1)
    raise RuntimeError("Data Agent instructions do not contain the expected routing block")


def ensure_fabric_data_agent_routing(
    cfg: Cfg,
    *,
    workspace_id: str,
    data_agent_id: str,
) -> dict[str, Any]:
    if cfg.fabric_data_agent_routing == "preserve":
        return {"status": "preserved", "changed": False}

    token = get_token(resource="https://api.fabric.microsoft.com")
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    agent_url = (
        "https://api.fabric.microsoft.com/v1/workspaces/"
        f"{workspace_id}/dataAgents/{data_agent_id}"
    )
    settings_url = f"{agent_url}/staging/settings"
    settings_response = requests.get(settings_url, headers=headers, timeout=90)
    _raise_if_bad(settings_response, "Get Fabric Data Agent staging settings")
    current_settings = _json_or_error(settings_response)
    current_instructions = str(current_settings.get("aiInstructions") or "")
    snapshot_path = _resolve_repo_relative_path(
        cfg.fabric_data_agent_routing_snapshot_path
    )

    if cfg.fabric_data_agent_routing == "restore":
        if not snapshot_path.exists():
            raise RuntimeError(f"Data Agent routing snapshot not found: {snapshot_path}")
        snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
        desired_instructions = str(
            snapshot.get("settings", {}).get("aiInstructions") or ""
        )
        if not desired_instructions:
            raise RuntimeError(f"Data Agent routing snapshot has no instructions: {snapshot_path}")
    else:
        desired_instructions = _lakehouse_primary_instructions(current_instructions)
        if not snapshot_path.exists():
            _write_json(
                snapshot_path,
                {
                    "tsUtc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "workspaceId": workspace_id,
                    "dataAgentId": data_agent_id,
                    "settings": current_settings,
                },
            )

    if desired_instructions == current_instructions:
        return {
            "status": cfg.fabric_data_agent_routing,
            "changed": False,
            "snapshotPath": str(snapshot_path),
        }

    patch_response = _request_with_retry(
        method="PATCH",
        url=settings_url,
        headers=headers,
        body={"aiInstructions": desired_instructions},
        step="Patch Fabric Data Agent routing instructions",
        timeout=90,
        max_attempts=3,
        initial_delay_seconds=3,
    )
    _raise_if_bad(patch_response, "Patch Fabric Data Agent routing instructions")

    agent_response = requests.get(agent_url, headers=headers, timeout=90)
    _raise_if_bad(agent_response, "Get Fabric Data Agent publish description")
    description = str(
        _json_or_error(agent_response).get("properties", {}).get(
            "publishedDescription", ""
        )
    ).strip()
    publish_response = _request_with_retry(
        method="POST",
        url=f"{agent_url}/staging/publish",
        headers=headers,
        body={"publishedDescription": description} if description else {},
        step="Publish Fabric Data Agent routing instructions",
        timeout=90,
        max_attempts=3,
        initial_delay_seconds=5,
    )
    _raise_if_bad(publish_response, "Publish Fabric Data Agent routing instructions")
    _wait_for_staging_publish(
        publish_response,
        headers=headers,
        step="Fabric Data Agent routing publish",
    )
    return {
        "status": cfg.fabric_data_agent_routing,
        "changed": True,
        "snapshotPath": str(snapshot_path),
    }


def build_fabric_mcp_endpoint(
    cfg: Cfg,
    *,
    workspace_id: str,
    data_agent_id: str,
) -> str:
    if cfg.fabric_iq_mcp_endpoint_override:
        return cfg.fabric_iq_mcp_endpoint_override
    return (
        "https://api.fabric.microsoft.com/v1/mcp/workspaces/"
        f"{workspace_id}/dataagents/{data_agent_id}/agent"
    )


def active_fabric_connection_name(cfg: Cfg) -> str:
    if cfg.fabric_mode == "fabric_iq":
        return cfg.foundry_fabric_iq_connection_name
    return cfg.foundry_fabric_connection_name


def build_fabric_connection_properties(
    cfg: Cfg,
    *,
    workspace_id: str,
    data_agent_id: str,
) -> dict[str, Any]:
    if cfg.fabric_mode == "fabric_iq":
        return {
            "authType": "UserEntraToken",
            "category": "RemoteTool",
            "target": build_fabric_mcp_endpoint(
                cfg,
                workspace_id=workspace_id,
                data_agent_id=data_agent_id,
            ),
            "audience": "https://analysis.windows.net/powerbi/api",
            "isSharedToAll": True,
        }
    if cfg.fabric_mode == "legacy":
        return {
            "authType": "CustomKeys",
            "category": "CustomKeys",
            "target": "-",
            "credentials": {
                "keys": {
                    "workspace-id": workspace_id,
                    "artifact-id": data_agent_id,
                }
            },
            "metadata": {
                "type": "fabric_dataagent_preview",
            },
            "isSharedToAll": True,
        }
    raise ValueError("A Fabric connection is not available when FOUNDRY_FABRIC_MODE=disabled")


def build_fabric_tool(
    cfg: Cfg,
    *,
    fabric_connection_id: str,
    fabric_mcp_endpoint: str,
) -> dict[str, Any]:
    if cfg.fabric_mode == "fabric_iq":
        return {
            "type": "fabric_iq_preview",
            "project_connection_id": fabric_connection_id,
            "server_label": "healthcare-data",
            "server_url": fabric_mcp_endpoint,
            "require_approval": "never",
        }
    if cfg.fabric_mode == "legacy":
        return {
            "type": "fabric_dataagent_preview",
            "fabric_dataagent_preview": {
                "project_connections": [
                    {"project_connection_id": fabric_connection_id},
                ]
            },
        }
    raise ValueError("A Fabric tool is not available when FOUNDRY_FABRIC_MODE=disabled")


def create_or_update_foundry_connection(
    cfg: Cfg,
    *,
    connection_name: str,
    properties: dict[str, Any],
    api_version: str = "2025-06-01",
    max_attempts: int | None = None,
    initial_delay_seconds: int | None = None,
) -> str:
    mgmt_token = get_token(scope="https://management.azure.com/.default")
    headers = {"Authorization": f"Bearer {mgmt_token}", "Content-Type": "application/json"}
    body = {"properties": properties}

    configured_versions = [
        item.strip()
        for item in os.getenv("FOUNDRY_CONNECTION_API_VERSIONS", "").split(",")
        if item.strip()
    ]
    version_candidates = configured_versions or [
        api_version,
        "2026-05-15-preview",
        "2026-05-01",
        "2026-03-15-preview",
        "2026-03-01",
        "2025-12-01",
        "2025-10-01-preview",
        "2025-09-01",
        "2025-07-01-preview",
        "2025-06-01",
    ]
    version_candidates = list(dict.fromkeys(version_candidates))
    effective_max_attempts = max(
        1,
        max_attempts
        if max_attempts is not None
        else int(os.getenv("FOUNDRY_CONNECTION_MAX_ATTEMPTS", "2")),
    )
    effective_initial_delay = max(
        1,
        initial_delay_seconds
        if initial_delay_seconds is not None
        else int(os.getenv("FOUNDRY_CONNECTION_RETRY_INITIAL_DELAY_SECONDS", "5")),
    )
    last_error = ""
    resp: requests.Response | None = None

    for version in version_candidates:
        url = (
            "https://management.azure.com"
            f"/subscriptions/{cfg.subscription_id}"
            f"/resourceGroups/{cfg.resource_group}"
            f"/providers/Microsoft.CognitiveServices/accounts/{cfg.hub_name}"
            f"/projects/{cfg.project_name}/connections/{connection_name}"
            f"?api-version={version}"
        )

        resp = _request_with_retry(
            method="PUT",
            url=url,
            headers=headers,
            body=body,
            step=f"Create/Update Foundry connection '{connection_name}' (api={version})",
            timeout=90,
            max_attempts=effective_max_attempts,
            initial_delay_seconds=effective_initial_delay,
        )

        if 200 <= resp.status_code < 300:
            break

        payload = _json_or_error(resp)
        last_error = f"HTTP {resp.status_code} via api-version {version}: {json.dumps(payload)}"
        print(f"[WARN] Foundry connection api-version fallback: {last_error}")
        resp = None

    if resp is None:
        raise RuntimeError(
            f"Create/Update Foundry connection '{connection_name}' failed across api versions. {last_error}"
        )

    _raise_if_bad(resp, f"Create/Update Foundry connection '{connection_name}'")
    data = resp.json()
    conn_id = str(data.get("id", "")).strip()
    if not conn_id:
        conn_id = (
            f"/subscriptions/{cfg.subscription_id}/resourceGroups/{cfg.resource_group}"
            f"/providers/Microsoft.CognitiveServices/accounts/{cfg.hub_name}"
            f"/projects/{cfg.project_name}/connections/{connection_name}"
        )
    return conn_id


def create_foundry_connection_via_bicep(
    cfg: Cfg,
    *,
    connection_name: str,
    workspace_id: str,
    data_agent_id: str,
) -> str:
    template = Path(__file__).resolve().parents[2] / "infra" / "foundry_fabric_connection_probe.bicep"
    if not template.exists():
        raise RuntimeError(f"Foundry connection Bicep template not found: {template}")

    deployment_name = f"foundry-conn-{connection_name.lower().replace('_', '-')[:40]}"
    output = run(
        [
            "az",
            "deployment",
            "group",
            "create",
            "--name",
            deployment_name,
            "--resource-group",
            cfg.resource_group,
            "--template-file",
            str(template),
            "--parameters",
            f"hubName={cfg.hub_name}",
            f"projectName={cfg.project_name}",
            f"connectionName={connection_name}",
            f"workspaceId={workspace_id}",
            f"dataAgentId={data_agent_id}",
            "--query",
            "properties.outputs.connectionId.value",
            "-o",
            "tsv",
        ],
        check=True,
        timeout=int(os.getenv("FOUNDRY_CONNECTION_BICEP_TIMEOUT_SECONDS", "300")),
    )
    connection_id = output.strip()
    if not connection_id:
        connection_id = (
            f"/subscriptions/{cfg.subscription_id}/resourceGroups/{cfg.resource_group}"
            f"/providers/Microsoft.CognitiveServices/accounts/{cfg.hub_name}"
            f"/projects/{cfg.project_name}/connections/{connection_name}"
        )
    return connection_id


def _search_request_with_auth_fallback(
    cfg: Cfg,
    *,
    method: str,
    url: str,
    headers: dict[str, str],
    body: dict[str, Any] | None = None,
    timeout: int = 90,
) -> tuple[requests.Response, dict[str, str]]:
    request_kwargs: dict[str, Any] = {
        "method": method,
        "url": url,
        "headers": headers,
        "timeout": timeout,
    }
    if body is not None:
        request_kwargs["json"] = body
    response = requests.request(**request_kwargs)
    if response.status_code not in (401, 403) or "api-key" in headers:
        return response, headers

    admin_headers = {
        "api-key": get_search_admin_key(cfg),
        "Content-Type": "application/json",
    }
    request_kwargs["headers"] = admin_headers
    response = requests.request(**request_kwargs)
    return response, admin_headers


def _indexed_onelake_created_resources(payload: dict[str, Any]) -> dict[str, str]:
    raw = (
        payload.get("indexedOneLakeParameters", {}).get("createdResources", {})
        if isinstance(payload, dict)
        else {}
    )
    if not isinstance(raw, dict):
        return {}
    return {
        key: str(value).strip()
        for key, value in raw.items()
        if str(value).strip()
    }


def _knowledge_source_ingestion_state(
    payload: dict[str, Any],
) -> tuple[str, list[dict[str, Any]]]:
    current = payload.get("currentSynchronizationState") or {}
    last = payload.get("lastSynchronizationState") or {}

    def failed_count(state: dict[str, Any]) -> int:
        for key in ("itemsUpdatesFailed", "itemUpdatesFailed", "itemsFailed"):
            value = state.get(key)
            if value is not None:
                return int(value)
        return 0

    def inferred_status(state: dict[str, Any]) -> str:
        status = str(state.get("status") or "").strip().lower()
        if status:
            return status
        if state.get("endTime"):
            return "partialsuccess" if failed_count(state) else "success"
        return ""

    current_status = str(current.get("status") or "").strip().lower()
    last_status = inferred_status(last)
    current_is_running = bool(current) and not current.get("endTime") and current_status not in {
        "success",
        "succeeded",
        "completed",
        "failed",
        "partialsuccess",
        "transientfailure",
        "persistentfailure",
    }
    if current_is_running:
        status = current_status or "running"
    else:
        status = last_status or inferred_status(current)
    if not status:
        status = str(payload.get("synchronizationStatus") or "unknown").strip().lower()

    errors: list[dict[str, Any]] = []
    for state in (current, last):
        state_errors = state.get("errors") or []
        if isinstance(state_errors, list):
            errors.extend(item for item in state_errors if isinstance(item, dict))
        count = failed_count(state)
        if count and not state_errors:
            errors.append({"itemsFailed": count})
    return status, errors


def _replace_search_knowledge_source_kind_if_needed(
    cfg: Cfg,
    *,
    search_endpoint: str,
    knowledge_source_url: str,
    desired_kind: str,
    headers: dict[str, str],
) -> dict[str, str]:
    existing_response, active_headers = _search_request_with_auth_fallback(
        cfg,
        method="GET",
        url=knowledge_source_url,
        headers=headers,
    )
    if existing_response.status_code == 404:
        return active_headers
    _raise_if_bad(existing_response, "Get existing Search knowledge source")
    existing_kind = str(_json_or_error(existing_response).get("kind") or "").strip()
    if not existing_kind or existing_kind == desired_kind:
        return active_headers

    knowledge_base_url = (
        f"{search_endpoint}/knowledgebases/{cfg.search_knowledge_base_name}"
        "?api-version=2026-05-01-preview"
    )
    delete_kb_response, active_headers = _search_request_with_auth_fallback(
        cfg,
        method="DELETE",
        url=knowledge_base_url,
        headers=active_headers,
    )
    if delete_kb_response.status_code not in (200, 202, 204, 404):
        _raise_if_bad(delete_kb_response, "Delete incompatible Search knowledge base")

    delete_source_response, active_headers = _search_request_with_auth_fallback(
        cfg,
        method="DELETE",
        url=knowledge_source_url,
        headers=active_headers,
    )
    if delete_source_response.status_code not in (200, 202, 204, 404):
        _raise_if_bad(delete_source_response, "Delete incompatible Search knowledge source")
    print(
        "[OK] Replaced incompatible Search knowledge source kind: "
        f"{existing_kind} -> {desired_kind}"
    )
    return active_headers


def _trigger_search_indexer(
    cfg: Cfg,
    *,
    search_endpoint: str,
    indexer_name: str,
    headers: dict[str, str],
) -> dict[str, str]:
    if not indexer_name:
        return headers
    encoded_name = quote(indexer_name, safe="")
    status_url = (
        f"{search_endpoint}/indexers/{encoded_name}/status"
        "?api-version=2024-07-01"
    )
    status_response, active_headers = _search_request_with_auth_fallback(
        cfg,
        method="GET",
        url=status_url,
        headers=headers,
    )
    if status_response.status_code == 200:
        last_result = _json_or_error(status_response).get("lastResult") or {}
        prior_failures = int(last_result.get("itemsFailed") or 0)
        if last_result.get("endTime") and prior_failures:
            reset_url = (
                f"{search_endpoint}/indexers/{encoded_name}/reset"
                "?api-version=2024-07-01"
            )
            reset_response, active_headers = _search_request_with_auth_fallback(
                cfg,
                method="POST",
                url=reset_url,
                headers=active_headers,
            )
            if reset_response.status_code not in (200, 202, 204):
                _raise_if_bad(reset_response, "Reset indexed OneLake Search indexer")
            print(
                "[OK] Reset indexed OneLake indexer after prior item failures: "
                f"{prior_failures}"
            )
    elif status_response.status_code != 404:
        _raise_if_bad(status_response, "Get indexed OneLake Search indexer status")

    run_url = (
        f"{search_endpoint}/indexers/{encoded_name}/run"
        "?api-version=2024-07-01"
    )
    response, active_headers = _search_request_with_auth_fallback(
        cfg,
        method="POST",
        url=run_url,
        headers=active_headers,
    )
    if response.status_code not in (200, 202, 204, 409):
        _raise_if_bad(response, "Run indexed OneLake Search indexer")
    if response.status_code == 409:
        print(f"[INFO] Indexed OneLake indexer is already running: {indexer_name}")
    else:
        print(f"[OK] Indexed OneLake indexer run requested: {indexer_name}")
    return active_headers


def wait_for_indexed_onelake_ingestion(
    cfg: Cfg,
    *,
    search_endpoint: str,
    knowledge_source_url: str,
    initial_source_payload: dict[str, Any],
    headers: dict[str, str],
) -> dict[str, Any]:
    status_url = (
        f"{search_endpoint}/knowledgesources/{cfg.search_knowledge_source_name}/status"
        "?api-version=2026-05-01-preview"
    )
    deadline = time.monotonic() + cfg.search_knowledge_ingestion_timeout_seconds
    source_payload = initial_source_payload
    created_resources = _indexed_onelake_created_resources(source_payload)
    last_status = "unknown"
    last_errors: list[dict[str, Any]] = []
    active_headers = headers
    started_at = time.monotonic()

    while time.monotonic() < deadline:
        status_response, active_headers = _search_request_with_auth_fallback(
            cfg,
            method="GET",
            url=status_url,
            headers=active_headers,
        )
        _raise_if_bad(status_response, "Get indexed OneLake knowledge source status")
        status_payload = _json_or_error(status_response)
        last_status, last_errors = _knowledge_source_ingestion_state(status_payload)

        if not created_resources:
            source_response, active_headers = _search_request_with_auth_fallback(
                cfg,
                method="GET",
                url=knowledge_source_url,
                headers=active_headers,
            )
            _raise_if_bad(source_response, "Get indexed OneLake knowledge source")
            source_payload = _json_or_error(source_response)
            created_resources = _indexed_onelake_created_resources(source_payload)

        if last_status in {
            "failed",
            "partialsuccess",
            "transientfailure",
            "persistentfailure",
        }:
            if time.monotonic() - started_at < 60:
                print(
                    "[STATUS] Waiting for the new indexed OneLake run to replace "
                    f"the prior {last_status} result"
                )
                time.sleep(cfg.search_knowledge_ingestion_poll_seconds)
                continue
            error_summary = json.dumps(last_errors[:5], sort_keys=True)
            raise RuntimeError(
                f"Indexed OneLake ingestion ended with status {last_status}: {error_summary}"
            )

        index_name = created_resources.get("index", "")
        if last_status in {"success", "succeeded", "completed"} and index_name:
            count_url = (
                f"{search_endpoint}/indexes/{quote(index_name, safe='')}/docs/$count"
                "?api-version=2024-07-01"
            )
            count_response, active_headers = _search_request_with_auth_fallback(
                cfg,
                method="GET",
                url=count_url,
                headers=active_headers,
            )
            if count_response.status_code == 200:
                document_count = int(count_response.text.strip() or "0")
                if document_count > 0:
                    print(
                        "[OK] Indexed OneLake knowledge source is ready: "
                        f"{document_count} indexed chunks"
                    )
                    return {
                        "ingestionStatus": last_status,
                        "documentCount": document_count,
                        "createdResources": created_resources,
                    }
            elif count_response.status_code not in (404, 503):
                _raise_if_bad(count_response, "Count indexed OneLake documents")

        print(
            "[STATUS] Indexed OneLake ingestion "
            f"status={last_status} resourcesReady={bool(created_resources)}"
        )
        time.sleep(cfg.search_knowledge_ingestion_poll_seconds)

    error_summary = json.dumps(last_errors[:5], sort_keys=True)
    raise RuntimeError(
        "Indexed OneLake knowledge source did not become ready within "
        f"{cfg.search_knowledge_ingestion_timeout_seconds}s "
        f"(status={last_status}, errors={error_summary})"
    )


def build_indexed_onelake_knowledge_source_body(
    *,
    name: str,
    workspace_id: str,
    lakehouse_id: str,
    target_path: str,
    model_resource_uri: str,
    embedding_deployment_name: str,
    embedding_model_name: str,
    ingestion_interval: str,
) -> dict[str, Any]:
    ingestion_parameters: dict[str, Any] = {
        "identity": None,
        "disableImageVerbalization": True,
        "embeddingModel": {
            "kind": "azureOpenAI",
            "azureOpenAIParameters": {
                "resourceUri": model_resource_uri.rstrip("/"),
                "deploymentId": embedding_deployment_name,
                "modelName": embedding_model_name,
            },
        },
        "contentExtractionMode": "minimal",
    }
    if ingestion_interval:
        ingestion_parameters["ingestionSchedule"] = {
            "interval": ingestion_interval,
        }

    return {
        "name": name,
        "kind": "indexedOneLake",
        "description": "Authoritative healthcare policy and clinical guidance from OneLake.",
        "indexedOneLakeParameters": {
            "fabricWorkspaceId": workspace_id,
            "lakehouseId": lakehouse_id,
            "targetPath": target_path,
            "ingestionParameters": ingestion_parameters,
        },
    }


def build_search_knowledge_base_body(cfg: Cfg) -> dict[str, Any]:
    source_reference: dict[str, Any] = {
        "name": cfg.search_knowledge_source_name,
    }
    body: dict[str, Any] = {
        "name": cfg.search_knowledge_base_name,
        "description": "Healthcare Foundry IQ knowledge base for policy and clinical guidance.",
        "outputMode": "extractiveData",
        "knowledgeSources": [source_reference],
        "retrievalReasoningEffort": {"kind": "minimal"},
    }
    if cfg.search_knowledge_mode != "onelake":
        return body

    body["models"] = [
        {
            "kind": "azureOpenAI",
            "azureOpenAIParameters": {
                "resourceUri": cfg.foundry_model_resource_uri,
                "deploymentId": cfg.foundry_model_deployment_name,
                "modelName": cfg.foundry_model_name,
            },
        }
    ]
    body["retrievalReasoningEffort"] = {
        "kind": cfg.search_knowledge_retrieval_reasoning_effort,
    }
    return body


def create_or_update_search_knowledge_artifacts(
    cfg: Cfg,
    *,
    workspace_id: str,
    data_agent_id: str,
    lakehouse_id: str = "",
) -> tuple[str, dict[str, Any]]:
    search_endpoint = f"https://{cfg.search_service_name}.search.windows.net"
    search_token = get_token(scope="https://search.azure.com/.default")
    headers = {"Authorization": f"Bearer {search_token}", "Content-Type": "application/json"}

    if cfg.search_knowledge_mode == "local_index":
        create_or_update_healthcare_policy_index(
            cfg,
            search_endpoint=search_endpoint,
            headers=headers,
        )

    ks_url = (
        f"{search_endpoint}/knowledgesources/{cfg.search_knowledge_source_name}"
        "?api-version=2026-05-01-preview"
    )
    if cfg.search_knowledge_mode == "local_index":
        ks_body = {
            "name": cfg.search_knowledge_source_name,
            "kind": "searchIndex",
            "description": "Authoritative healthcare policy and clinical guidance documents.",
            "searchIndexParameters": {
                "searchIndexName": cfg.search_knowledge_index_name,
                "semanticConfigurationName": "healthcare-policy-semantic",
                "sourceDataFields": [
                    {"name": "title"},
                    {"name": "content"},
                    {"name": "source_path"},
                    {"name": "category"},
                ],
                "searchFields": [
                    {"name": "title"},
                    {"name": "content"},
                ],
            },
        }
    elif cfg.search_knowledge_mode == "onelake":
        if not lakehouse_id:
            raise RuntimeError(
                f"Fabric lakehouse ID is required for OneLake knowledge mode: {cfg.fabric_lakehouse_name}"
            )
        ks_body = build_indexed_onelake_knowledge_source_body(
            name=cfg.search_knowledge_source_name,
            workspace_id=workspace_id,
            lakehouse_id=lakehouse_id,
            target_path=cfg.search_knowledge_onelake_target_path,
            model_resource_uri=cfg.foundry_model_resource_uri,
            embedding_deployment_name=cfg.foundry_embedding_deployment_name,
            embedding_model_name=cfg.foundry_embedding_model_name,
            ingestion_interval=cfg.search_knowledge_ingestion_interval,
        )
    else:
        ks_body = {
            "name": cfg.search_knowledge_source_name,
            "kind": "fabricDataAgent",
            "description": "Fabric Data Agent knowledge source for healthcare orchestrator.",
            "fabricDataAgentParameters": {
                "workspaceId": workspace_id,
                "dataAgentId": data_agent_id,
            },
        }
    headers = _replace_search_knowledge_source_kind_if_needed(
        cfg,
        search_endpoint=search_endpoint,
        knowledge_source_url=ks_url,
        desired_kind=ks_body["kind"],
        headers=headers,
    )
    ks_resp, headers = _search_request_with_auth_fallback(
        cfg,
        method="PUT",
        url=ks_url,
        headers=headers,
        body=ks_body,
    )
    _raise_if_bad(ks_resp, "Create/Update Search knowledge source")
    knowledge_source_details: dict[str, Any] = {
        "mode": cfg.search_knowledge_mode,
        "kind": ks_body["kind"],
    }
    if cfg.search_knowledge_mode == "onelake":
        source_payload = _json_or_error(ks_resp)
        created_resources = _indexed_onelake_created_resources(source_payload)
        if not created_resources:
            source_response, headers = _search_request_with_auth_fallback(
                cfg,
                method="GET",
                url=ks_url,
                headers=headers,
            )
            _raise_if_bad(source_response, "Get indexed OneLake knowledge source")
            source_payload = _json_or_error(source_response)
            created_resources = _indexed_onelake_created_resources(source_payload)
        if not created_resources.get("indexer") or not created_resources.get("index"):
            raise RuntimeError(
                "Indexed OneLake knowledge source did not report its generated indexer and index"
            )
        headers = _trigger_search_indexer(
            cfg,
            search_endpoint=search_endpoint,
            indexer_name=created_resources.get("indexer", ""),
            headers=headers,
        )
        ingestion_details = wait_for_indexed_onelake_ingestion(
            cfg,
            search_endpoint=search_endpoint,
            knowledge_source_url=ks_url,
            initial_source_payload=source_payload,
            headers=headers,
        )
        knowledge_source_details.update(
            {
                "lakehouseId": lakehouse_id,
                "targetPath": cfg.search_knowledge_onelake_target_path,
                **ingestion_details,
            }
        )

    kb_url = (
        f"{search_endpoint}/knowledgebases/{cfg.search_knowledge_base_name}"
        "?api-version=2026-05-01-preview"
    )
    kb_resp, headers = _search_request_with_auth_fallback(
        cfg,
        method="PUT",
        url=kb_url,
        headers=headers,
        body=build_search_knowledge_base_body(cfg),
    )
    _raise_if_bad(kb_resp, "Create/Update Search knowledge base")

    return (
        f"{search_endpoint}/knowledgebases/{cfg.search_knowledge_base_name}/mcp"
        "?api-version=2026-05-01-preview",
        knowledge_source_details,
    )


def _healthcare_document_chunks(content: str, max_chars: int = 8000) -> list[str]:
    paragraphs = [part.strip() for part in content.split("\n\n") if part.strip()]
    chunks: list[str] = []
    current: list[str] = []
    current_length = 0

    for paragraph in paragraphs:
        if len(paragraph) > max_chars:
            if current:
                chunks.append("\n\n".join(current))
                current = []
                current_length = 0
            chunks.extend(
                paragraph[offset : offset + max_chars]
                for offset in range(0, len(paragraph), max_chars)
            )
            continue

        added_length = len(paragraph) + (2 if current else 0)
        if current and current_length + added_length > max_chars:
            chunks.append("\n\n".join(current))
            current = []
            current_length = 0
            added_length = len(paragraph)
        current.append(paragraph)
        current_length += added_length

    if current:
        chunks.append("\n\n".join(current))
    return chunks


def _load_healthcare_policy_documents(cfg: Cfg) -> list[dict[str, Any]]:
    root = _resolve_repo_relative_path(cfg.search_knowledge_directory)
    if not root.exists():
        raise RuntimeError(f"Healthcare knowledge directory not found: {root}")

    documents: list[dict[str, Any]] = []
    for path in sorted(root.rglob("*.md")):
        relative = path.relative_to(root)
        content = path.read_text(encoding="utf-8")
        title = next(
            (
                line.lstrip("# ").strip()
                for line in content.splitlines()
                if line.startswith("#") and line.lstrip("# ").strip()
            ),
            path.stem.replace("_", " "),
        )
        category = relative.parts[0] if len(relative.parts) > 1 else "general"
        for chunk_index, chunk in enumerate(_healthcare_document_chunks(content)):
            digest = hashlib.sha256(
                f"{relative.as_posix()}:{chunk_index}".encode("utf-8")
            ).hexdigest()
            documents.append(
                {
                    "@search.action": "mergeOrUpload",
                    "id": digest,
                    "title": title,
                    "content": chunk,
                    "source_path": relative.as_posix(),
                    "category": category,
                }
            )
    if not documents:
        raise RuntimeError(f"No healthcare Markdown documents found under: {root}")
    return documents


def create_or_update_healthcare_policy_index(
    cfg: Cfg,
    *,
    search_endpoint: str,
    headers: dict[str, str],
) -> None:
    index_api_version = "2024-07-01"
    index_url = (
        f"{search_endpoint}/indexes/{cfg.search_knowledge_index_name}"
        f"?api-version={index_api_version}"
    )
    index_body = {
        "name": cfg.search_knowledge_index_name,
        "fields": [
            {
                "name": "id",
                "type": "Edm.String",
                "key": True,
                "filterable": True,
                "retrievable": True,
            },
            {
                "name": "title",
                "type": "Edm.String",
                "searchable": True,
                "retrievable": True,
            },
            {
                "name": "content",
                "type": "Edm.String",
                "searchable": True,
                "retrievable": True,
            },
            {
                "name": "source_path",
                "type": "Edm.String",
                "filterable": True,
                "retrievable": True,
            },
            {
                "name": "category",
                "type": "Edm.String",
                "searchable": True,
                "filterable": True,
                "retrievable": True,
            },
        ],
        "semantic": {
            "configurations": [
                {
                    "name": "healthcare-policy-semantic",
                    "prioritizedFields": {
                        "titleField": {"fieldName": "title"},
                        "prioritizedContentFields": [{"fieldName": "content"}],
                        "prioritizedKeywordsFields": [{"fieldName": "category"}],
                    },
                }
            ]
        },
    }
    index_response = requests.put(index_url, headers=headers, json=index_body, timeout=90)
    if index_response.status_code in (401, 403):
        admin_headers = {
            "api-key": get_search_admin_key(cfg),
            "Content-Type": "application/json",
        }
        index_response = requests.put(
            index_url, headers=admin_headers, json=index_body, timeout=90
        )
        upload_headers = admin_headers
    else:
        upload_headers = headers
    _raise_if_bad(index_response, "Create/Update healthcare policy Search index")

    documents = _load_healthcare_policy_documents(cfg)
    upload_url = (
        f"{search_endpoint}/indexes/{cfg.search_knowledge_index_name}/docs/index"
        f"?api-version={index_api_version}"
    )
    for offset in range(0, len(documents), 100):
        batch = documents[offset : offset + 100]
        upload_response = requests.post(
            upload_url,
            headers=upload_headers,
            json={"value": batch},
            timeout=120,
        )
        _raise_if_bad(upload_response, "Upload healthcare policy Search documents")
        failed = [
            item
            for item in _json_or_error(upload_response).get("value", [])
            if not item.get("status")
        ]
        if failed:
            raise RuntimeError(
                f"Healthcare policy document upload had {len(failed)} failed items"
            )
    print(
        f"[OK] Healthcare policy index ready: {cfg.search_knowledge_index_name} "
        f"({len(documents)} chunks)"
    )


def read_orchestrator_instructions(path_str: str) -> str:
    path = Path(path_str)
    if not path.is_absolute():
        path = Path(__file__).resolve().parents[2] / path
    if not path.exists():
        raise RuntimeError(f"Instructions file not found: {path}")

    raw = path.read_text(encoding="utf-8")
    lines = raw.splitlines()
    if len(lines) >= 2 and lines[0].strip() == "---":
        try:
            end_idx = lines[1:].index("---") + 1
            return "\n".join(lines[end_idx + 1 :]).strip()
        except ValueError:
            return raw.strip()
    return raw.strip()


def upsert_orchestrator_agent(
    cfg: Cfg,
    *,
    fabric_connection_id: str,
    fabric_mcp_endpoint: str,
    kb_connection_id: str,
    kb_mcp_endpoint: str,
) -> None:
    foundry_token = get_token(scope="https://ai.azure.com/.default")
    headers = {"Authorization": f"Bearer {foundry_token}", "Content-Type": "application/json"}
    project_endpoint = f"https://{cfg.hub_name}.services.ai.azure.com/api/projects/{cfg.project_name}"
    instructions = read_orchestrator_instructions(cfg.instructions_file)

    tools: list[dict[str, Any]] = []
    if cfg.include_web_search_tool:
        tools.append({"type": "web_search_preview"})
    tools.append(
        build_fabric_tool(
            cfg,
            fabric_connection_id=fabric_connection_id,
            fabric_mcp_endpoint=fabric_mcp_endpoint,
        )
    )
    tools.append(
        {
            "type": "mcp",
            "server_label": "knowledge-base",
            "server_url": kb_mcp_endpoint,
            "require_approval": "never",
            "allowed_tools": ["knowledge_base_retrieve"],
            "project_connection_id": kb_connection_id,
        }
    )

    definition = {
        "kind": "prompt",
        "model": cfg.foundry_model_deployment_name,
        "instructions": instructions,
        "tools": tools,
    }

    get_url = f"{project_endpoint}/agents/{cfg.orchestrator_agent_name}?api-version=v1"
    get_resp = requests.get(get_url, headers=headers, timeout=60)

    if get_resp.status_code == 404:
        create_url = f"{project_endpoint}/agents?api-version=v1"
        create_body = {
            "name": cfg.orchestrator_agent_name,
            "display_name": cfg.orchestrator_agent_name,
            "description": "Healthcare orchestrator agent (automated by harness).",
            "definition": definition,
        }
        resp = requests.post(create_url, headers=headers, json=create_body, timeout=90)
        _raise_if_bad(resp, "Create Foundry orchestrator agent")
        return

    _raise_if_bad(get_resp, "Get Foundry orchestrator agent")
    version_url = (
        f"{project_endpoint}/agents/{cfg.orchestrator_agent_name}/versions?api-version=v1"
    )
    version_body = {
        "display_name": cfg.orchestrator_agent_name,
        "description": "Healthcare orchestrator agent version (automated by harness).",
        "definition": definition,
    }
    version_resp = requests.post(version_url, headers=headers, json=version_body, timeout=90)
    _raise_if_bad(version_resp, "Create Foundry orchestrator agent version")


def upsert_kb_only_orchestrator_agent(
    cfg: Cfg,
    *,
    kb_connection_id: str,
    kb_mcp_endpoint: str,
) -> None:
    foundry_token = get_token(scope="https://ai.azure.com/.default")
    headers = {"Authorization": f"Bearer {foundry_token}", "Content-Type": "application/json"}
    project_endpoint = f"https://{cfg.hub_name}.services.ai.azure.com/api/projects/{cfg.project_name}"
    instructions = """
You are the non-IQ comparison agent for a healthcare analytics demonstration.

You have access to the healthcare policy knowledge base, but you do not have access to live
Fabric data, patient records, claims, encounters, or operational metrics. Use the knowledge base
for policy, clinical guideline, compliance, denial-management, medication-adherence, quality,
and provider-network questions. Cite the retrieved source document in every substantive answer.

When a user asks for live numbers, rates, patient lists, payer breakdowns, or other Fabric data,
state clearly that this non-IQ agent cannot access governed operational data and that the IQ
orchestrator or the Fabric Data Agent is required. Never estimate, invent, or infer operational
values. This limitation is intentional for comparison with the IQ-enabled orchestrator.
""".strip()

    tools: list[dict[str, Any]] = []
    if cfg.include_web_search_tool:
        tools.append({"type": "web_search_preview"})
    tools.append(
        {
            "type": "mcp",
            "server_label": "knowledge-base",
            "server_url": kb_mcp_endpoint,
            "require_approval": "never",
            "allowed_tools": ["knowledge_base_retrieve"],
            "project_connection_id": kb_connection_id,
        }
    )

    definition = {
        "kind": "prompt",
        "model": cfg.foundry_model_deployment_name,
        "instructions": instructions,
        "tools": tools,
    }

    get_url = f"{project_endpoint}/agents/{cfg.kb_only_agent_name}?api-version=v1"
    get_resp = requests.get(get_url, headers=headers, timeout=60)

    if get_resp.status_code == 404:
        create_url = f"{project_endpoint}/agents?api-version=v1"
        create_body = {
            "name": cfg.kb_only_agent_name,
            "display_name": cfg.kb_only_agent_name,
            "description": "Healthcare non-IQ comparison agent with policy knowledge but no Fabric data tool.",
            "definition": definition,
        }
        resp = requests.post(create_url, headers=headers, json=create_body, timeout=90)
        _raise_if_bad(resp, "Create KB-only Foundry orchestrator agent")
        return

    _raise_if_bad(get_resp, "Get KB-only Foundry orchestrator agent")
    version_url = f"{project_endpoint}/agents/{cfg.kb_only_agent_name}/versions?api-version=v1"
    version_body = {
        "display_name": cfg.kb_only_agent_name,
        "description": "Healthcare non-IQ comparison agent version.",
        "definition": definition,
    }
    version_resp = requests.post(version_url, headers=headers, json=version_body, timeout=90)
    _raise_if_bad(version_resp, "Create KB-only Foundry orchestrator agent version")


def run_automation(cfg: Cfg) -> None:
    fabric_connection_name = active_fabric_connection_name(cfg)
    status_report: dict[str, Any] = {
        "tsUtc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "hubName": cfg.hub_name,
        "projectName": cfg.project_name,
        "fabricWorkspaceName": cfg.fabric_workspace_name,
        "fabricDataAgentName": cfg.fabric_data_agent_name,
        "fabricMode": cfg.fabric_mode,
        "fabricConnection": {"name": fabric_connection_name, "status": "notStarted"},
        "dataAgentSourceSelection": {"status": "notStarted"},
        "dataAgentSemanticSource": {"status": "notStarted"},
        "dataAgentSourceMetadata": {"status": "notStarted"},
        "dataAgentFewshots": {"status": "notStarted"},
        "dataAgentRouting": {"status": "notStarted"},
        "knowledgeSource": {"name": cfg.search_knowledge_source_name, "status": "notStarted"},
        "knowledgeBase": {"name": cfg.search_knowledge_base_name, "status": "notStarted"},
        "kbConnection": {"name": cfg.foundry_kb_connection_name, "status": "notStarted"},
        "orchestratorAgent": {"name": cfg.orchestrator_agent_name, "status": "notStarted"},
        "kbOnlyAgent": {"name": cfg.kb_only_agent_name, "status": "notStarted"},
    }

    print("[STEP] Foundry completion automation: resolving Fabric workspace and Data Agent IDs")
    workspace_id, data_agent_id = find_fabric_workspace_and_agent_ids(cfg)
    print(f"[OK] Fabric workspace resolved: {workspace_id}")
    print(f"[OK] Fabric data agent resolved: {data_agent_id}")
    status_report["workspaceId"] = workspace_id
    status_report["dataAgentId"] = data_agent_id
    lakehouse_id = ""
    if cfg.search_knowledge_mode == "onelake":
        print(
            "[STEP] Foundry completion automation: resolving OneLake lakehouse "
            f"{cfg.fabric_lakehouse_name}"
        )
        lakehouse_id = find_fabric_item_id(
            workspace_id=workspace_id,
            item_type="Lakehouse",
            display_name=cfg.fabric_lakehouse_name,
        )
        status_report["lakehouseId"] = lakehouse_id
        print(f"[OK] OneLake lakehouse resolved: {lakehouse_id}")

    print("[STEP] Foundry completion automation: checking Data Agent staging table selection")
    try:
        source_selection = ensure_fabric_data_agent_table_selection(
            cfg,
            workspace_id=workspace_id,
            data_agent_id=data_agent_id,
        )
        status_report["dataAgentSourceSelection"] = source_selection
        if source_selection["published"]:
            print(
                "[OK] Data Agent staging tables selected and published: "
                f"{', '.join(source_selection['changedTables'])}"
            )
        else:
            print("[OK] Data Agent staging table selection already satisfied")
    except Exception as ex:
        source_selection_error = str(ex)
        print(f"[WARN] Data Agent staging table selection could not be repaired: {source_selection_error}")
        status_report["dataAgentSourceSelection"] = {
            "status": "failed",
            "error": source_selection_error,
        }
        append_blocker_log(
            cfg,
            stage="fabric_data_agent_source_selection",
            message="Data Agent staging table selection preflight failed.",
            details={
                "workspaceId": workspace_id,
                "dataAgentId": data_agent_id,
                "lakehouseName": cfg.fabric_lakehouse_name,
                "requiredTables": list(cfg.fabric_data_agent_tables),
                "error": source_selection_error,
            },
        )
        if cfg.enforce_success:
            raise

    print("[STEP] Foundry completion automation: checking Data Agent few-shots")
    try:
        fewshot_result = ensure_fabric_data_agent_fewshots(
            cfg,
            workspace_id=workspace_id,
            data_agent_id=data_agent_id,
        )
        status_report["dataAgentFewshots"] = fewshot_result
        print(
            "[OK] Data Agent few-shots ready "
            f"(changed={fewshot_result['changedExamples']})"
        )
    except Exception as ex:
        fewshot_error = str(ex)
        print(f"[WARN] Data Agent few-shots could not be synchronized: {fewshot_error}")
        status_report["dataAgentFewshots"] = {
            "status": "failed",
            "error": fewshot_error,
        }
        append_blocker_log(
            cfg,
            stage="fabric_data_agent_fewshots",
            message="Data Agent few-shot synchronization failed.",
            details={
                "workspaceId": workspace_id,
                "dataAgentId": data_agent_id,
                "error": fewshot_error,
            },
        )
        if cfg.enforce_success:
            raise

    print(
        "[STEP] Foundry completion automation: checking Data Agent semantic source "
        f"(mode={cfg.fabric_data_agent_semantic_source})"
    )
    try:
        semantic_source_result = ensure_fabric_data_agent_semantic_source(
            cfg,
            workspace_id=workspace_id,
            data_agent_id=data_agent_id,
        )
        status_report["dataAgentSemanticSource"] = semantic_source_result
        print(
            "[OK] Data Agent semantic source ready: "
            f"{semantic_source_result['status']} "
            f"(present={semantic_source_result['semanticSourcePresent']})"
        )
    except Exception as ex:
        semantic_source_error = str(ex)
        print(
            "[WARN] Data Agent semantic source could not be reconciled: "
            f"{semantic_source_error}"
        )
        status_report["dataAgentSemanticSource"] = {
            "status": "failed",
            "error": semantic_source_error,
        }
        append_blocker_log(
            cfg,
            stage="fabric_data_agent_semantic_source",
            message="Data Agent semantic-source reconciliation failed.",
            details={
                "workspaceId": workspace_id,
                "dataAgentId": data_agent_id,
                "mode": cfg.fabric_data_agent_semantic_source,
                "error": semantic_source_error,
            },
        )
        if cfg.enforce_success:
            raise

    print("[STEP] Foundry completion automation: checking Data Agent datasource metadata")
    try:
        metadata_result = ensure_fabric_data_agent_source_metadata(
            cfg,
            workspace_id=workspace_id,
            data_agent_id=data_agent_id,
        )
        status_report["dataAgentSourceMetadata"] = metadata_result
        print(
            "[OK] Data Agent datasource metadata ready "
            f"(changed={metadata_result['changedSources']})"
        )
    except Exception as ex:
        metadata_error = str(ex)
        print(f"[WARN] Data Agent datasource metadata could not be updated: {metadata_error}")
        status_report["dataAgentSourceMetadata"] = {
            "status": "failed",
            "error": metadata_error,
        }
        append_blocker_log(
            cfg,
            stage="fabric_data_agent_source_metadata",
            message="Data Agent datasource metadata synchronization failed.",
            details={
                "workspaceId": workspace_id,
                "dataAgentId": data_agent_id,
                "error": metadata_error,
            },
        )
        if cfg.enforce_success:
            raise

    print(
        "[STEP] Foundry completion automation: checking Data Agent routing "
        f"(mode={cfg.fabric_data_agent_routing})"
    )
    try:
        routing_result = ensure_fabric_data_agent_routing(
            cfg,
            workspace_id=workspace_id,
            data_agent_id=data_agent_id,
        )
        status_report["dataAgentRouting"] = routing_result
        print(
            "[OK] Data Agent routing ready: "
            f"{routing_result['status']} (changed={routing_result['changed']})"
        )
    except Exception as ex:
        routing_error = str(ex)
        print(f"[WARN] Data Agent routing could not be updated: {routing_error}")
        status_report["dataAgentRouting"] = {
            "status": "failed",
            "error": routing_error,
        }
        append_blocker_log(
            cfg,
            stage="fabric_data_agent_routing",
            message="Data Agent routing update failed.",
            details={
                "workspaceId": workspace_id,
                "dataAgentId": data_agent_id,
                "mode": cfg.fabric_data_agent_routing,
                "error": routing_error,
            },
        )
        if cfg.enforce_success:
            raise

    fabric_connection_id = ""
    fabric_connection_error = ""
    fabric_mcp_endpoint = build_fabric_mcp_endpoint(
        cfg,
        workspace_id=workspace_id,
        data_agent_id=data_agent_id,
    )
    if cfg.fabric_mode == "disabled":
        print("[INFO] Foundry Fabric connection disabled by FOUNDRY_FABRIC_MODE")
        status_report["fabricConnection"]["status"] = "disabled"
    else:
        fabric_connection_properties = build_fabric_connection_properties(
            cfg,
            workspace_id=workspace_id,
            data_agent_id=data_agent_id,
        )
        print(
            "[STEP] Foundry completion automation: creating Foundry Fabric connection "
            f"(mode={cfg.fabric_mode})"
        )
        try:
            fabric_connection_id = create_or_update_foundry_connection(
                cfg,
                connection_name=fabric_connection_name,
                properties=fabric_connection_properties,
                api_version="2025-10-01-preview" if cfg.fabric_mode == "fabric_iq" else "2025-06-01",
            )
            print(f"[OK] Foundry Fabric connection ready: {fabric_connection_id}")
            status_report["fabricConnection"]["status"] = "ok"
            status_report["fabricConnection"]["id"] = fabric_connection_id
            status_report["fabricConnection"]["target"] = fabric_mcp_endpoint
        except Exception as ex:
            fabric_connection_error = str(ex)
            if cfg.fabric_mode == "legacy" and to_bool(
                os.getenv("FOUNDRY_CONNECTION_BICEP_FALLBACK", "true"), default=True
            ):
                print("[STEP] Retrying Fabric connection through the documented Bicep child-resource route")
                try:
                    fabric_connection_id = create_foundry_connection_via_bicep(
                        cfg,
                        connection_name=fabric_connection_name,
                        workspace_id=workspace_id,
                        data_agent_id=data_agent_id,
                    )
                    fabric_connection_error = ""
                    print(f"[OK] Foundry Fabric connection created through Bicep: {fabric_connection_id}")
                    status_report["fabricConnection"]["status"] = "ok"
                    status_report["fabricConnection"]["method"] = "bicep"
                    status_report["fabricConnection"]["id"] = fabric_connection_id
                except Exception as bicep_ex:
                    fabric_connection_error = f"Direct REST: {fabric_connection_error}; Bicep: {bicep_ex}"

            if not fabric_connection_id:
                print(f"[WARN] Foundry Fabric connection could not be created automatically: {fabric_connection_error}")
                print("       Continuing with Search/KB automation.")
                status_report["fabricConnection"]["status"] = "failed"
                status_report["fabricConnection"]["error"] = fabric_connection_error
                append_blocker_log(
                    cfg,
                    stage="foundry_fabric_connection",
                    message="Foundry Fabric project connection creation failed.",
                    details={
                        "hubName": cfg.hub_name,
                        "projectName": cfg.project_name,
                        "connectionName": fabric_connection_name,
                        "fabricMode": cfg.fabric_mode,
                        "workspaceId": workspace_id,
                        "dataAgentId": data_agent_id,
                        "error": fabric_connection_error,
                    },
                )

    print("[STEP] Foundry completion automation: creating knowledge source and knowledge base")
    kb_mcp_endpoint, knowledge_source_details = create_or_update_search_knowledge_artifacts(
        cfg,
        workspace_id=workspace_id,
        data_agent_id=data_agent_id,
        lakehouse_id=lakehouse_id,
    )
    print(f"[OK] Search knowledge artifacts ready: {cfg.search_knowledge_base_name}")
    status_report["knowledgeSource"].update(
        {"status": "ok", **knowledge_source_details}
    )
    status_report["knowledgeBase"]["status"] = "ok"
    status_report["knowledgeBase"]["mcpEndpoint"] = kb_mcp_endpoint

    print("[STEP] Foundry completion automation: creating Foundry KB MCP connection")
    kb_connection_id = create_or_update_foundry_connection(
        cfg,
        connection_name=cfg.foundry_kb_connection_name,
        properties={
            "authType": "ProjectManagedIdentity",
            "category": "RemoteTool",
            "target": kb_mcp_endpoint,
            "audience": "https://search.azure.com/",
            "metadata": {
                "ApiType": "Azure",
            },
            "isSharedToAll": True,
        },
        api_version="2025-10-01-preview",
    )
    print(f"[OK] Foundry KB MCP connection ready: {kb_connection_id}")
    status_report["kbConnection"]["status"] = "ok"
    status_report["kbConnection"]["id"] = kb_connection_id

    if fabric_connection_id:
        print("[STEP] Foundry completion automation: creating/updating orchestrator agent")
        upsert_orchestrator_agent(
            cfg,
            fabric_connection_id=fabric_connection_id,
            fabric_mcp_endpoint=fabric_mcp_endpoint,
            kb_connection_id=kb_connection_id,
            kb_mcp_endpoint=kb_mcp_endpoint,
        )
        print(f"[OK] Foundry orchestrator agent upserted: {cfg.orchestrator_agent_name}")
        status_report["orchestratorAgent"]["status"] = "ok"
    else:
        print("[WARN] Skipping orchestrator agent upsert because Fabric project connection is unavailable.")
        status_report["orchestratorAgent"]["status"] = "skipped"
        status_report["orchestratorAgent"]["reason"] = "fabric connection unavailable"

    if cfg.allow_kb_only_agent_fallback:
        print("[STEP] Creating/updating non-IQ comparison agent")
        upsert_kb_only_orchestrator_agent(
            cfg,
            kb_connection_id=kb_connection_id,
            kb_mcp_endpoint=kb_mcp_endpoint,
        )
        print(f"[OK] Non-IQ comparison agent upserted: {cfg.kb_only_agent_name}")
        status_report["kbOnlyAgent"]["status"] = "ok"
    else:
        status_report["kbOnlyAgent"]["status"] = "disabled"

    print("[SUMMARY] Foundry completion automation finished successfully")
    print(f"          fabric_mode={cfg.fabric_mode}")
    print(f"          fabric_connection={fabric_connection_name}")
    print(f"          knowledge_source={cfg.search_knowledge_source_name}")
    print(f"          knowledge_base={cfg.search_knowledge_base_name}")
    print(f"          kb_connection={cfg.foundry_kb_connection_name}")
    print(f"          orchestrator_agent={cfg.orchestrator_agent_name}")

    _write_json(_resolve_repo_relative_path(cfg.status_report_path), status_report)
    print(f"[OK] Status report written: {cfg.status_report_path}")

    if fabric_connection_error and cfg.enforce_success:
        raise RuntimeError(
            "Fabric project connection creation failed while enforce_success=true. "
            f"Details: {fabric_connection_error}"
        )


def main() -> int:
    try:
        cfg = load_cfg()
    except Exception as ex:
        print(f"[ERROR] Foundry completion automation config error: {ex}")
        return 1

    try:
        run(["az", "version"], check=True)
    except Exception:
        print("[ERROR] Azure CLI is required for token acquisition.")
        return 1

    try:
        run_automation(cfg)
        return 0
    except Exception as ex:
        print(f"[ERROR] Foundry completion automation failed: {ex}")
        if cfg.enforce_success:
            return 1
        print("[WARN] FOUNDRY_AUTOMATION_ENFORCE_SUCCESS=false, continuing despite failure.")
        return 0


if __name__ == "__main__":
    sys.exit(main())