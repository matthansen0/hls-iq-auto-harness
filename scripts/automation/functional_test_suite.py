#!/usr/bin/env python3
"""Run redacted functional tests for the IQ and direct-MCP demo paths."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
from urllib.parse import quote

import requests

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parents[1]
sys.path.insert(0, str(SCRIPT_DIR))

import semantic_model_health_canary as semantic_canary  # noqa: E402


FABRIC_API = "https://api.fabric.microsoft.com/v1"
DEFAULT_KPI_QUERIES = (
    "Show me denial rates by payer using all available data with no date filter",
    "How many encounters are in each readmission risk category?",
    "Show me medication adherence rates by drug class",
)
FAILURE_PHRASES = (
    "could not retrieve",
    "couldn't retrieve",
    "unable to retrieve",
    "semantic model was unavailable",
    "semantic model is unavailable",
    "not refreshed",
    "no data found",
)
EXPECTED_ARTIFACTS = {
    "Lakehouse": (
        "lh_bronze_raw",
        "lh_silver_stage",
        "lh_silver_ods",
        "lh_gold_curated",
    ),
    "SemanticModel": ("HealthcareDemoHLS",),
    "DataAgent": ("HealthcareHLSAgent",),
}


@dataclass
class TestResult:
    name: str
    status: str
    durationSeconds: float
    message: str
    details: dict[str, Any]


class FunctionalTestError(RuntimeError):
    """Raised when a functional prerequisite or protocol call fails."""


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def run_command(command: list[str]) -> str:
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip()
        raise FunctionalTestError(f"Command failed: {' '.join(command)}\n{detail}")
    return result.stdout.strip()


def get_token(resource: str) -> str:
    return run_command(
        [
            "az",
            "account",
            "get-access-token",
            "--resource",
            resource,
            "--query",
            "accessToken",
            "-o",
            "tsv",
        ]
    )


def request_json(
    method: str,
    url: str,
    *,
    token: str,
    body: dict[str, Any] | None = None,
    timeout: int = 90,
) -> tuple[requests.Response, dict[str, Any]]:
    response = requests.request(
        method,
        url,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        json=body,
        timeout=timeout,
    )
    try:
        payload = response.json() if response.content else {}
    except ValueError:
        payload = {"raw": response.text[:2000]}
    if not 200 <= response.status_code < 300:
        raise FunctionalTestError(
            f"{method} {url} failed (HTTP {response.status_code}): "
            f"{json.dumps(payload, sort_keys=True)}"
        )
    return response, payload


def run_test(name: str, function: Callable[[], tuple[str, str, dict[str, Any]]]) -> TestResult:
    started = time.monotonic()
    try:
        status, message, details = function()
    except Exception as exc:
        return TestResult(
            name=name,
            status="failed",
            durationSeconds=round(time.monotonic() - started, 3),
            message=str(exc),
            details={"errorType": type(exc).__name__},
        )
    return TestResult(
        name=name,
        status=status,
        durationSeconds=round(time.monotonic() - started, 3),
        message=message,
        details=details,
    )


def resolve_workspace_id(fabric_token: str, workspace_name: str) -> str:
    _, payload = request_json("GET", f"{FABRIC_API}/workspaces", token=fabric_token)
    for workspace in payload.get("value", []):
        if workspace.get("displayName") == workspace_name:
            return str(workspace.get("id", "")).strip()
    raise FunctionalTestError(f"Fabric workspace not found: {workspace_name}")


def list_workspace_items(fabric_token: str, workspace_id: str) -> list[dict[str, Any]]:
    _, payload = request_json(
        "GET",
        f"{FABRIC_API}/workspaces/{quote(workspace_id, safe='')}/items",
        token=fabric_token,
    )
    return list(payload.get("value", []))


def find_item(items: list[dict[str, Any]], item_type: str, display_name: str) -> str:
    for item in items:
        if item.get("type") == item_type and item.get("displayName") == display_name:
            return str(item.get("id", "")).strip()
    raise FunctionalTestError(f"Missing Fabric item: {item_type}/{display_name}")


def test_artifact_inventory(items: list[dict[str, Any]]) -> tuple[str, str, dict[str, Any]]:
    available = {
        (str(item.get("type", "")), str(item.get("displayName", ""))) for item in items
    }
    missing = [
        f"{item_type}/{display_name}"
        for item_type, names in EXPECTED_ARTIFACTS.items()
        for display_name in names
        if (item_type, display_name) not in available
    ]
    counts: dict[str, int] = {}
    for item in items:
        item_type = str(item.get("type", "Unknown"))
        counts[item_type] = counts.get(item_type, 0) + 1
    if missing:
        return "failed", f"Missing {len(missing)} required Fabric artifacts", {
            "missing": missing,
            "countsByType": counts,
        }
    return "passed", "Required Fabric artifacts are present", {"countsByType": counts}


class StreamableHttpMcpClient:
    def __init__(self, url: str, token: str, timeout_seconds: int) -> None:
        self.url = url
        self.timeout_seconds = timeout_seconds
        self.session = requests.Session()
        self.session.headers.update(
            {
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
                "Accept": "application/json, text/event-stream",
                "MCP-Protocol-Version": "2025-03-26",
            }
        )
        self.session_id = ""
        self.request_id = 0

    def _next_id(self) -> int:
        self.request_id += 1
        return self.request_id

    @staticmethod
    def _decode_response(response: requests.Response) -> dict[str, Any]:
        if not response.content or not response.text.strip():
            return {}
        content_type = response.headers.get("Content-Type", "").lower()
        if "text/event-stream" not in content_type:
            try:
                return response.json()
            except ValueError as exc:
                preview = response.text.strip()[:500]
                raise FunctionalTestError(
                    "MCP response was not JSON "
                    f"(HTTP {response.status_code}, content-type={content_type or 'missing'}): "
                    f"{preview or '<empty>'}"
                ) from exc

        events: list[dict[str, Any]] = []
        for line in response.text.splitlines():
            if not line.startswith("data:"):
                continue
            raw = line[5:].strip()
            if not raw or raw == "[DONE]":
                continue
            try:
                events.append(json.loads(raw))
            except json.JSONDecodeError:
                continue
        if not events:
            raise FunctionalTestError("MCP response contained no decodable SSE data events")
        return events[-1]

    def _post(self, payload: dict[str, Any], *, expect_response: bool = True) -> dict[str, Any]:
        headers = dict(self.session.headers)
        if self.session_id:
            headers["Mcp-Session-Id"] = self.session_id
        response = self.session.post(
            self.url,
            headers=headers,
            json=payload,
            timeout=self.timeout_seconds,
        )
        if not 200 <= response.status_code < 300:
            raise FunctionalTestError(
                f"MCP {payload.get('method')} failed (HTTP {response.status_code}): "
                f"{response.text[:2000]}"
            )
        session_id = response.headers.get("Mcp-Session-Id", "").strip()
        if session_id:
            self.session_id = session_id
        if not expect_response:
            return {}
        decoded = self._decode_response(response)
        if decoded.get("error"):
            raise FunctionalTestError(
                f"MCP {payload.get('method')} returned an error: "
                f"{json.dumps(decoded['error'], sort_keys=True)}"
            )
        return decoded

    def initialize(self) -> dict[str, Any]:
        response = self._post(
            {
                "jsonrpc": "2.0",
                "id": self._next_id(),
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-03-26",
                    "capabilities": {},
                    "clientInfo": {
                        "name": "hls-iq-functional-tests",
                        "version": "1.0.0",
                    },
                },
            }
        )
        result = response.get("result", {})
        protocol_version = str(result.get("protocolVersion") or "").strip()
        if protocol_version:
            self.session.headers["MCP-Protocol-Version"] = protocol_version
        self._post(
            {
                "jsonrpc": "2.0",
                "method": "notifications/initialized",
                "params": {},
            },
            expect_response=False,
        )
        return result

    def list_tools(self) -> list[dict[str, Any]]:
        response = self._post(
            {
                "jsonrpc": "2.0",
                "id": self._next_id(),
                "method": "tools/list",
                "params": {},
            }
        )
        return list(response.get("result", {}).get("tools", []))

    def call_tool(self, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        response = self._post(
            {
                "jsonrpc": "2.0",
                "id": self._next_id(),
                "method": "tools/call",
                "params": {"name": tool_name, "arguments": arguments},
            }
        )
        return dict(response.get("result", {}))


def result_text(result: dict[str, Any]) -> str:
    blocks = result.get("content", [])
    return "\n".join(
        str(block.get("text", ""))
        for block in blocks
        if isinstance(block, dict) and block.get("type") == "text"
    ).strip()


def first_tool_argument(tool: dict[str, Any]) -> str:
    properties = tool.get("inputSchema", {}).get("properties", {})
    if not properties:
        raise FunctionalTestError(f"MCP tool has no input properties: {tool.get('name')}")
    preferred = ("userQuestion", "question", "query", "input")
    for name in preferred:
        if name in properties:
            return name
    return str(next(iter(properties)))


def build_tool_arguments(tool: dict[str, Any], question: str) -> dict[str, Any]:
    argument_name = first_tool_argument(tool)
    property_schema = tool.get("inputSchema", {}).get("properties", {}).get(
        argument_name, {}
    )
    if property_schema.get("type") == "array":
        return {argument_name: [question]}
    return {argument_name: question}


def test_mcp_query(
    *,
    endpoint: str,
    token: str,
    question: str,
    timeout_seconds: int,
    allowed_tool_name: str = "",
) -> tuple[str, str, dict[str, Any]]:
    client = StreamableHttpMcpClient(endpoint, token, timeout_seconds)
    server = client.initialize()
    tools = client.list_tools()
    if not tools:
        raise FunctionalTestError("MCP tools/list returned no tools")
    tool = next(
        (candidate for candidate in tools if candidate.get("name") == allowed_tool_name),
        tools[0],
    )
    result = client.call_tool(
        str(tool.get("name", "")), build_tool_arguments(tool, question)
    )
    text = result_text(result)
    lowered = text.lower()
    failed_phrase = next((phrase for phrase in FAILURE_PHRASES if phrase in lowered), "")
    details = {
        "serverName": server.get("serverInfo", {}).get("name") or server.get("name"),
        "toolName": tool.get("name"),
        "responseCharacters": len(text),
        "isError": bool(result.get("isError")),
        "failurePhrase": failed_phrase,
    }
    if result.get("isError") or not text or failed_phrase:
        return "failed", "MCP call did not return a governed answer", details
    return "passed", "MCP call returned a governed answer", details


def extract_response_text(payload: Any) -> str:
    texts: list[str] = []

    def walk(value: Any) -> None:
        if isinstance(value, dict):
            if value.get("type") in {"output_text", "text"} and isinstance(value.get("text"), str):
                texts.append(value["text"])
            for child in value.values():
                walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)

    walk(payload)
    return "\n".join(dict.fromkeys(texts)).strip()


def foundry_tool_evidence(payload: dict[str, Any]) -> dict[str, Any]:
    output = payload.get("output", [])
    completed_calls = [
        item
        for item in output
        if isinstance(item, dict)
        and item.get("type") == "mcp_call"
        and item.get("status") == "completed"
    ]
    server_labels = [str(item.get("server_label") or "") for item in completed_calls]
    tool_names = [str(item.get("name") or "") for item in completed_calls]
    annotation_count = sum(
        len(content.get("annotations") or [])
        for item in output
        if isinstance(item, dict)
        for content in item.get("content", [])
        if isinstance(content, dict)
    )
    return {
        "completedToolCalls": len(completed_calls),
        "serverLabels": server_labels,
        "toolNames": tool_names,
        "citationAnnotations": annotation_count,
    }


def foundry_response(
    *,
    project_endpoint: str,
    agent_name: str,
    question: str,
    timeout_seconds: int,
) -> tuple[dict[str, Any], float]:
    token = get_token("https://ai.azure.com")
    url = f"{project_endpoint.rstrip('/')}/openai/v1/responses"
    started = time.monotonic()
    _, payload = request_json(
        "POST",
        url,
        token=token,
        body={
            "input": question,
            "agent_reference": {"type": "agent_reference", "name": agent_name},
            "background": True,
        },
        timeout=timeout_seconds,
    )
    response_id = str(payload.get("id", "")).strip()
    status = str(payload.get("status", "")).lower()
    while status in {"queued", "in_progress"} and time.monotonic() - started < timeout_seconds:
        time.sleep(5)
        _, payload = request_json(
            "GET",
            f"{url}/{quote(response_id, safe='')}",
            token=token,
            timeout=min(90, timeout_seconds),
        )
        status = str(payload.get("status", "")).lower()
    return payload, time.monotonic() - started


def test_foundry_agent(
    *,
    project_endpoint: str,
    agent_name: str,
    question: str,
    expected_signals: tuple[str, ...],
    timeout_seconds: int,
) -> tuple[str, str, dict[str, Any]]:
    payload, elapsed = foundry_response(
        project_endpoint=project_endpoint,
        agent_name=agent_name,
        question=question,
        timeout_seconds=timeout_seconds,
    )
    status = str(payload.get("status", "")).lower()
    serialized = json.dumps(payload, sort_keys=True).lower()
    text = extract_response_text(payload)
    consent_requested = "oauth_consent_request" in serialized or "consent_link" in serialized
    approval_requested = "mcp_approval_request" in serialized
    evidence = foundry_tool_evidence(payload)
    signals = {
        signal: (
            "healthcare-data" in evidence["serverLabels"]
            if signal == "fabric"
            else "knowledge-base" in evidence["serverLabels"]
            if signal == "mcp"
            else False
        )
        for signal in expected_signals
    }
    details = {
        "responseId": payload.get("id"),
        "responseStatus": status,
        "responseCharacters": len(text),
        "elapsedSeconds": round(elapsed, 3),
        "consentRequested": consent_requested,
        "approvalRequested": approval_requested,
        "signals": signals,
        **evidence,
    }
    if consent_requested:
        return "consent_required", "Foundry requires user OAuth consent", details
    if approval_requested:
        return "approval_required", "Foundry requires MCP tool approval", details
    if status != "completed" or not text:
        return "failed", "Foundry response did not complete with text", details
    if expected_signals and not all(signals.values()):
        return "failed", "Foundry response omitted expected tool-call signals", details
    if "mcp" in expected_signals and evidence["citationAnnotations"] == 0:
        return "failed", "Foundry knowledge response omitted citation annotations", details
    return "passed", "Foundry agent completed with expected tool signals", details


def _percentage_values(text: str, labels: list[str]) -> dict[str, float]:
    values: dict[str, float] = {}
    for label in labels:
        match = re.search(
            re.escape(label) + r"[^\n]{0,160}?(\d{1,2}(?:\.\d+)?)%",
            text,
            flags=re.IGNORECASE,
        )
        if match:
            values[label] = float(match.group(1))
    return values


def test_denial_rate_consistency(
    *,
    workspace_id: str,
    dataset_id: str,
    data_agent_endpoint: str,
    fabric_token: str,
    project_endpoint: str,
    agent_name: str,
    timeout_seconds: int,
) -> tuple[str, str, dict[str, Any]]:
    power_bi_token = get_token("https://analysis.windows.net/powerbi/api")
    dax = (
        'EVALUATE SUMMARIZECOLUMNS(dim_payer[payer_name], '
        '"TotalClaims", [Total Claims], "DenialRate", [Denial Rate]) '
        'ORDER BY [DenialRate] DESC'
    )
    _, dax_payload = request_json(
        "POST",
        f"https://api.powerbi.com/v1.0/myorg/groups/{quote(workspace_id, safe='')}"
        f"/datasets/{quote(dataset_id, safe='')}/executeQueries",
        token=power_bi_token,
        body={
            "queries": [{"query": dax}],
            "serializerSettings": {"includeNulls": True},
        },
        timeout=timeout_seconds,
    )
    rows = dax_payload.get("results", [{}])[0].get("tables", [{}])[0].get("rows", [])
    expected = {
        str(row.get("dim_payer[payer_name]")): float(row.get("[DenialRate]")) * 100
        for row in rows
        if row.get("dim_payer[payer_name]") is not None
        and row.get("[DenialRate]") is not None
    }
    if not expected:
        raise FunctionalTestError("DAX denial-rate baseline returned no payer rows")

    question = "Show me denial rates by payer using all available data with no date filter"
    direct_client = StreamableHttpMcpClient(
        data_agent_endpoint, fabric_token, timeout_seconds
    )
    direct_client.initialize()
    direct_tool = direct_client.list_tools()[0]
    direct_result = direct_client.call_tool(
        str(direct_tool.get("name") or ""),
        build_tool_arguments(direct_tool, question),
    )
    direct_text = result_text(direct_result)

    iq_payload, _ = foundry_response(
        project_endpoint=project_endpoint,
        agent_name=agent_name,
        question=question,
        timeout_seconds=timeout_seconds,
    )
    if str(iq_payload.get("status") or "").lower() != "completed":
        raise FunctionalTestError(
            f"IQ consistency response failed: {json.dumps(iq_payload.get('error'))}"
        )
    iq_text = next(
        (
            str(item.get("output") or "")
            for item in iq_payload.get("output", [])
            if item.get("type") == "mcp_call"
            and item.get("status") == "completed"
            and item.get("server_label") == "healthcare-data"
        ),
        "",
    )

    labels = list(expected)
    direct_values = _percentage_values(direct_text, labels)
    iq_values = _percentage_values(iq_text, labels)
    tolerance = 0.11
    direct_mismatches = {
        label: abs(direct_values.get(label, -1000) - expected[label])
        for label in labels
        if label not in direct_values
        or abs(direct_values[label] - expected[label]) > tolerance
    }
    iq_mismatches = {
        label: abs(iq_values.get(label, -1000) - expected[label])
        for label in labels
        if label not in iq_values
        or abs(iq_values[label] - expected[label]) > tolerance
    }
    forbidden_periods = (
        "12 month",
        "calendar year",
        "year 2026",
        "in 2026",
        "current year",
        "last year",
    )
    direct_filters = [term for term in forbidden_periods if term in direct_text.lower()]
    iq_filters = [term for term in forbidden_periods if term in iq_text.lower()]
    details = {
        "expectedPayers": len(expected),
        "directPayers": len(direct_values),
        "iqPayers": len(iq_values),
        "directMismatchCount": len(direct_mismatches),
        "iqMismatchCount": len(iq_mismatches),
        "directImplicitDateFilters": direct_filters,
        "iqImplicitDateFilters": iq_filters,
    }
    if direct_mismatches or iq_mismatches or direct_filters or iq_filters:
        return "failed", "Direct or IQ payer rates diverged from the DAX baseline", details
    return "passed", "Direct and IQ payer rates match the full-data DAX baseline", details


def test_non_iq_data_limitation(
    *,
    project_endpoint: str,
    agent_name: str,
    timeout_seconds: int,
) -> tuple[str, str, dict[str, Any]]:
    payload, elapsed = foundry_response(
        project_endpoint=project_endpoint,
        agent_name=agent_name,
        question="Show me denial rates by payer",
        timeout_seconds=timeout_seconds,
    )
    status = str(payload.get("status", "")).lower()
    serialized = json.dumps(payload, sort_keys=True).lower()
    text = extract_response_text(payload)
    lowered = text.lower()
    limitation_phrases = (
        "cannot access",
        "do not have access",
        "does not have access",
        "iq orchestrator",
        "fabric data agent",
    )
    limitation_found = next(
        (phrase for phrase in limitation_phrases if phrase in lowered), ""
    )
    fabric_tool_used = (
        "healthcare-data" in serialized
        or "fabric_iq_preview" in serialized
        or "dataagent_healthcarehlsagent" in serialized
    )
    details = {
        "responseId": payload.get("id"),
        "responseStatus": status,
        "responseCharacters": len(text),
        "elapsedSeconds": round(elapsed, 3),
        "limitationPhrase": limitation_found,
        "fabricToolUsed": fabric_tool_used,
    }
    if status != "completed" or not text:
        return "failed", "Non-IQ agent did not complete with text", details
    if fabric_tool_used or not limitation_found:
        return "failed", "Non-IQ agent did not preserve its intentional data limitation", details
    return "passed", "Non-IQ agent clearly reported its Fabric data limitation", details


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace-name", default=os.getenv("FABRIC_WORKSPACE_NAME", "HealthcareDemo-WS"))
    parser.add_argument("--workspace-id", default=os.getenv("FABRIC_WORKSPACE_ID", ""))
    parser.add_argument("--dataset-name", default="HealthcareDemoHLS")
    parser.add_argument("--data-agent-name", default=os.getenv("FABRIC_DATA_AGENT_NAME", "HealthcareHLSAgent"))
    parser.add_argument("--project-endpoint", default=os.getenv("FOUNDRY_PROJECT_ENDPOINT", ""))
    parser.add_argument("--agent-name", default=os.getenv("FOUNDRY_ORCHESTRATOR_AGENT_NAME", "HealthcareOrchestratorAgent2"))
    parser.add_argument(
        "--comparison-agent-name",
        default=os.getenv("FOUNDRY_KB_ONLY_AGENT_NAME", "HealthcareOrchestratorNonIQ"),
    )
    parser.add_argument("--search-service-name", default=os.getenv("SEARCH_SERVICE_NAME", ""))
    parser.add_argument("--knowledge-base-name", default=os.getenv("SEARCH_KNOWLEDGE_BASE_NAME", "healthcareknowledgebase"))
    parser.add_argument("--timeout-seconds", type=int, default=180)
    parser.add_argument("--skip-semantic-model", action="store_true")
    parser.add_argument("--skip-data-agent", action="store_true")
    parser.add_argument("--skip-knowledge-base", action="store_true")
    parser.add_argument("--skip-foundry-agent", action="store_true")
    parser.add_argument("--skip-comparison-agent", action="store_true")
    parser.add_argument("--skip-consistency", action="store_true")
    parser.add_argument("--output", default="logs/functional_test_latest.json")
    parser.add_argument("--enforce-success", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    output = Path(args.output)
    if not output.is_absolute():
        output = REPO_ROOT / output
    tests: list[TestResult] = []
    context: dict[str, Any] = {"tsUtc": utc_now()}

    try:
        fabric_token = get_token("https://api.fabric.microsoft.com")
        workspace_id = args.workspace_id.strip() or resolve_workspace_id(
            fabric_token, args.workspace_name
        )
        items = list_workspace_items(fabric_token, workspace_id)
        context.update({"workspaceId": workspace_id, "workspaceName": args.workspace_name})
        tests.append(run_test("fabric_artifact_inventory", lambda: test_artifact_inventory(items)))

        dataset_id = find_item(items, "SemanticModel", args.dataset_name)
        data_agent_id = find_item(items, "DataAgent", args.data_agent_name)
        context.update({"datasetId": dataset_id, "dataAgentId": data_agent_id})

        if not args.skip_semantic_model:
            def semantic_test() -> tuple[str, str, dict[str, Any]]:
                power_bi_token = get_token("https://analysis.windows.net/powerbi/api")
                info = semantic_canary.get_dataset_info(
                    power_bi_token, workspace_id, dataset_id
                )
                counts = semantic_canary.execute_dax_counts(
                    power_bi_token,
                    workspace_id,
                    dataset_id,
                    timeout_seconds=args.timeout_seconds,
                )
                passed = all(value > 0 for value in counts.values())
                return (
                    "passed" if passed else "failed",
                    "Direct Lake DAX returned aggregate counts" if passed else "Direct Lake DAX counts were empty",
                    {"configuredBy": info.get("configuredBy"), "counts": counts},
                )

            tests.append(run_test("direct_lake_dax", semantic_test))

        if not args.skip_data_agent:
            endpoint = (
                f"{FABRIC_API}/mcp/workspaces/{workspace_id}/dataagents/{data_agent_id}/agent"
            )
            for index, question in enumerate(DEFAULT_KPI_QUERIES, start=1):
                tests.append(
                    run_test(
                        f"direct_data_agent_mcp_kpi_{index}",
                        lambda question=question: test_mcp_query(
                            endpoint=endpoint,
                            token=fabric_token,
                            question=question,
                            timeout_seconds=args.timeout_seconds,
                        ),
                    )
                )

        if not args.skip_knowledge_base:
            if not args.search_service_name:
                tests.append(
                    TestResult(
                        "search_kb_mcp",
                        "skipped",
                        0.0,
                        "SEARCH_SERVICE_NAME was not provided",
                        {},
                    )
                )
            else:
                kb_endpoint = (
                    f"https://{args.search_service_name}.search.windows.net/knowledgebases/"
                    f"{args.knowledge_base_name}/mcp?api-version=2026-05-01-preview"
                )
                tests.append(
                    run_test(
                        "search_kb_mcp",
                        lambda: test_mcp_query(
                            endpoint=kb_endpoint,
                            token=get_token("https://search.azure.com"),
                            question="What does the appeal process guide recommend for missing documentation denials?",
                            timeout_seconds=args.timeout_seconds,
                            allowed_tool_name="knowledge_base_retrieve",
                        ),
                    )
                )

        if not args.skip_foundry_agent:
            if not args.project_endpoint:
                tests.append(
                    TestResult(
                        "foundry_iq_agent",
                        "skipped",
                        0.0,
                        "FOUNDRY_PROJECT_ENDPOINT was not provided",
                        {},
                    )
                )
            else:
                foundry_cases = (
                    (
                        "foundry_iq_data_only",
                        "Show me denial rates by payer using all available data with no date filter",
                        ("fabric",),
                    ),
                    (
                        "foundry_iq_knowledge_only",
                        "What does the denial appeal guide recommend for missing documentation?",
                        ("mcp",),
                    ),
                    (
                        "foundry_iq_hybrid",
                        "Show me denial rates by payer and explain the appeal process for the top denial reasons.",
                        ("fabric", "mcp"),
                    ),
                )
                for name, question, signals in foundry_cases:
                    tests.append(
                        run_test(
                            name,
                            lambda question=question, signals=signals: test_foundry_agent(
                                project_endpoint=args.project_endpoint,
                                agent_name=args.agent_name,
                                question=question,
                                expected_signals=signals,
                                timeout_seconds=args.timeout_seconds,
                            ),
                        )
                    )

                if not args.skip_comparison_agent:
                    tests.append(
                        run_test(
                            "non_iq_knowledge_only",
                            lambda: test_foundry_agent(
                                project_endpoint=args.project_endpoint,
                                agent_name=args.comparison_agent_name,
                                question="What does the denial appeal guide recommend for missing documentation?",
                                expected_signals=("mcp",),
                                timeout_seconds=args.timeout_seconds,
                            ),
                        )
                    )

                if (
                    not args.skip_consistency
                    and not args.skip_semantic_model
                    and not args.skip_data_agent
                ):
                    data_agent_endpoint = (
                        f"{FABRIC_API}/mcp/workspaces/{workspace_id}/dataagents/"
                        f"{data_agent_id}/agent"
                    )
                    tests.append(
                        run_test(
                            "denial_rate_cross_path_consistency",
                            lambda: test_denial_rate_consistency(
                                workspace_id=workspace_id,
                                dataset_id=dataset_id,
                                data_agent_endpoint=data_agent_endpoint,
                                fabric_token=fabric_token,
                                project_endpoint=args.project_endpoint,
                                agent_name=args.agent_name,
                                timeout_seconds=args.timeout_seconds,
                            ),
                        )
                    )
                    tests.append(
                        run_test(
                            "non_iq_data_limitation",
                            lambda: test_non_iq_data_limitation(
                                project_endpoint=args.project_endpoint,
                                agent_name=args.comparison_agent_name,
                                timeout_seconds=args.timeout_seconds,
                            ),
                        )
                    )
    except Exception as exc:
        tests.append(
            TestResult(
                name="suite_setup",
                status="failed",
                durationSeconds=0.0,
                message=str(exc),
                details={"errorType": type(exc).__name__},
            )
        )

    payload = {
        **context,
        "tests": [asdict(test) for test in tests],
        "summary": {
            status: sum(1 for test in tests if test.status == status)
            for status in (
                "passed",
                "failed",
                "skipped",
                "consent_required",
                "approval_required",
            )
        },
    }
    payload["status"] = (
        "passed"
        if payload["summary"]["failed"] == 0
        and payload["summary"]["consent_required"] == 0
        and payload["summary"]["approval_required"] == 0
        else "failed"
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")

    for test in tests:
        print(f"[{test.status.upper()}] {test.name}: {test.message}")
    print(f"[INFO] Functional test report: {output}")
    print(f"[SUMMARY] {json.dumps(payload['summary'], sort_keys=True)}")
    if args.enforce_success and payload["status"] != "passed":
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
