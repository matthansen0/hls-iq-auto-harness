#!/usr/bin/env python3
"""Generate an ignored, environment-specific Fabric IQ demo handoff."""

from __future__ import annotations

import argparse
import ast
import base64
import json
import os
import re
import subprocess
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote

import requests


REPO_ROOT = Path(__file__).resolve().parents[2]
HANDOFF_DIR = REPO_ROOT / "handoffs"
FABRIC_API = "https://api.fabric.microsoft.com/v1"

SAFE_ENV_KEYS = {
    "AZURE_ENV_NAME",
    "AZURE_LOCATION",
    "AZURE_RESOURCE_GROUP",
    "AZURE_SUBSCRIPTION_ID",
    "FABRIC_CAPACITY_NAME",
    "FABRIC_CAPACITY_SKU",
    "FABRIC_DATA_AGENT_NAME",
    "FABRIC_DATA_AGENT_ROUTING",
    "FABRIC_DATA_AGENT_SEMANTIC_SOURCE",
    "FABRIC_WORKSPACE_ID",
    "FABRIC_WORKSPACE_NAME",
    "FOUNDRY_CHAT_DEPLOYMENT_NAME",
    "FOUNDRY_CHAT_SKU_NAME",
    "FOUNDRY_EMBEDDING_CAPACITY",
    "FOUNDRY_EMBEDDING_DEPLOYMENT_NAME",
    "FOUNDRY_FABRIC_MODE",
    "FOUNDRY_KB_ONLY_AGENT_NAME",
    "FOUNDRY_ORCHESTRATOR_AGENT_NAME",
    "FUNCTIONAL_TEST_OUTPUT_PATH",
    "HUB_NAME",
    "LOCATION",
    "PROJECT_NAME",
    "RUN_POSTDEPLOY_VALIDATION",
    "SEARCH_KNOWLEDGE_BASE_NAME",
    "SEARCH_KNOWLEDGE_INDEX_NAME",
    "SEARCH_KNOWLEDGE_MODE",
    "SEARCH_KNOWLEDGE_ONELAKE_TARGET_PATH",
    "SEARCH_KNOWLEDGE_SOURCE_NAME",
    "SEARCH_SERVICE_NAME",
    "SEMANTIC_MODEL_HEALTH_LOG_PATH",
    "SEMANTIC_MODEL_NAME",
}


def run(command: list[str]) -> tuple[int, str]:
    result = subprocess.run(command, capture_output=True, text=True)
    return result.returncode, result.stdout.strip()


def parse_azd_values(raw: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in raw.splitlines():
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if value.startswith(('"', "'")) and value.endswith(value[0]):
            try:
                parsed = ast.literal_eval(value)
                value = str(parsed)
            except (SyntaxError, ValueError):
                value = value[1:-1]
        if key in SAFE_ENV_KEYS:
            values[key] = value
    return values


def load_values(environment: str) -> dict[str, str]:
    values = {key: os.environ[key] for key in SAFE_ENV_KEYS if os.getenv(key)}
    if environment:
        code, raw = run(["azd", "env", "get-values", "--environment", environment])
        if code == 0:
            values.update(parse_azd_values(raw))
    if environment:
        values["AZURE_ENV_NAME"] = environment
    return values


def load_json(relative_path: str, default_path: str) -> dict[str, Any]:
    path = Path(relative_path or default_path)
    if not path.is_absolute():
        path = REPO_ROOT / path
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}
    return payload if isinstance(payload, dict) else {}


def resolve_workspace_id(values: dict[str, str]) -> str:
    configured = values.get("FABRIC_WORKSPACE_ID", "").strip()
    if configured:
        return configured
    workspace_name = values.get("FABRIC_WORKSPACE_NAME", "").strip()
    if not workspace_name:
        return ""
    code, token = run(
        [
            "az",
            "account",
            "get-access-token",
            "--resource",
            "https://api.fabric.microsoft.com",
            "--query",
            "accessToken",
            "-o",
            "tsv",
        ]
    )
    if code != 0 or not token:
        return ""
    try:
        response = requests.get(
            f"{FABRIC_API}/workspaces",
            headers={"Authorization": f"Bearer {token}"},
            timeout=30,
        )
        response.raise_for_status()
        for workspace in response.json().get("value", []):
            if workspace.get("displayName") == workspace_name:
                return str(workspace.get("id", ""))
    except (requests.RequestException, ValueError):
        return ""
    return ""


def encode_subscription_id(subscription_id: str) -> str:
    try:
        raw = uuid.UUID(subscription_id).bytes
    except ValueError:
        return quote(subscription_id, safe="")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def markdown_link(label: str, url: str) -> str:
    return f"[{label}]({url})" if url else label


def validation_summary(functional: dict[str, Any]) -> str:
    summary = functional.get("summary")
    if not isinstance(summary, dict):
        return "Not run or no result file was available."
    return (
        f"{summary.get('passed', 0)} passed, {summary.get('failed', 0)} failed, "
        f"{summary.get('skipped', 0)} skipped, "
        f"{summary.get('consent_required', 0)} consent requests, and "
        f"{summary.get('approval_required', 0)} approval requests."
    )


def certification_state(
    values: dict[str, str],
    functional: dict[str, Any],
    semantic: dict[str, Any],
) -> str:
    if values.get("RUN_POSTDEPLOY_VALIDATION", "true").lower() != "true":
        return "SKIPPED"
    summary = functional.get("summary")
    if not isinstance(summary, dict):
        return "FAILED OR INCOMPLETE"
    if (
        semantic.get("status") == "passed"
        and summary.get("passed", 0) > 0
        and summary.get("failed", 0) == 0
        and summary.get("skipped", 0) == 0
        and summary.get("consent_required", 0) == 0
        and summary.get("approval_required", 0) == 0
    ):
        return "PASSED"
    return "FAILED OR INCOMPLETE"


def semantic_counts(semantic: dict[str, Any]) -> list[tuple[str, Any]]:
    for key in ("counts", "daxCounts", "dax_counts"):
        candidate = semantic.get(key)
        if isinstance(candidate, dict):
            return [(str(name), value) for name, value in candidate.items()]
    tests = semantic.get("tests")
    if isinstance(tests, dict):
        candidate = tests.get("daxCounts") or tests.get("counts")
        if isinstance(candidate, dict):
            return [(str(name), value) for name, value in candidate.items()]
    return []


def safe_filename(environment: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", environment).strip("-.")
    return cleaned or "healthcare-demo"


def build_handoff(
    values: dict[str, str],
    workspace_id: str,
    functional: dict[str, Any],
    semantic: dict[str, Any],
    generated_utc: str,
    foundry_status: dict[str, Any] | None = None,
) -> str:
    foundry_status = foundry_status or {}
    environment = values.get("AZURE_ENV_NAME", "healthcare-demo")
    subscription = values.get("AZURE_SUBSCRIPTION_ID", "")
    resource_group = values.get("AZURE_RESOURCE_GROUP", "")
    hub_name = values.get("HUB_NAME", "")
    project_name = values.get("PROJECT_NAME", "HealthcareDemo-HLS")
    workspace_name = values.get("FABRIC_WORKSPACE_NAME", "HealthcareDemo-WS")
    data_agent_name = values.get("FABRIC_DATA_AGENT_NAME", "HealthcareHLSAgent")
    iq_agent = values.get("FOUNDRY_ORCHESTRATOR_AGENT_NAME", "HealthcareOrchestratorAgent2")
    non_iq_agent = values.get("FOUNDRY_KB_ONLY_AGENT_NAME", "HealthcareOrchestratorNonIQ")
    knowledge_source = foundry_status.get("knowledgeSource") or {}
    knowledge_mode = values.get("SEARCH_KNOWLEDGE_MODE", "onelake")
    knowledge_target = values.get(
        "SEARCH_KNOWLEDGE_ONELAKE_TARGET_PATH", "healthcare_knowledge"
    )
    knowledge_count = knowledge_source.get("documentCount", "not available")

    fabric_url = (
        f"https://app.fabric.microsoft.com/groups/{quote(workspace_id, safe='')}/list"
        if workspace_id
        else ""
    )
    foundry_root = ""
    if subscription and resource_group and hub_name and project_name:
        route = ",".join(
            (
                encode_subscription_id(subscription),
                quote(resource_group, safe=""),
                "",
                quote(hub_name, safe=""),
                quote(project_name, safe=""),
            )
        )
        foundry_root = f"https://ai.azure.com/nextgen/r/{route}/build"
    iq_url = f"{foundry_root}/agents/{quote(iq_agent, safe='')}/build" if foundry_root else ""
    non_iq_url = f"{foundry_root}/agents/{quote(non_iq_agent, safe='')}/build" if foundry_root else ""

    counts = semantic_counts(semantic)
    count_rows = "\n".join(f"| {name} | {value:,} |" if isinstance(value, int) else f"| {name} | {value} |" for name, value in counts)
    if not count_rows:
        count_rows = "| DAX canary | No count result available |"

    return f"""# Live IQ Demo Handoff

Generated: **{generated_utc}**  
Environment: **{environment}**

> This is a local generated artifact. It is intentionally excluded from Git.

## Deployment

| Component | Value |
|---|---|
| Resource group | `{resource_group or 'not available'}` |
| Region | `{values.get('AZURE_LOCATION') or values.get('LOCATION') or 'not available'}` |
| Fabric capacity | `{values.get('FABRIC_CAPACITY_NAME', 'not available')}` (`{values.get('FABRIC_CAPACITY_SKU', 'F64')}`) |
| Fabric workspace | `{workspace_name}` |
| Fabric Data Agent | `{data_agent_name}` |
| Foundry account/project | `{hub_name or 'not available'}` / `{project_name}` |
| Orchestrator model | `{values.get('FOUNDRY_CHAT_DEPLOYMENT_NAME', 'gpt-5.4')}` (`{values.get('FOUNDRY_CHAT_SKU_NAME', 'GlobalStandard')}`) |
| Embedding model | `{values.get('FOUNDRY_EMBEDDING_DEPLOYMENT_NAME', 'text-embedding-ada-002')}` (`{values.get('FOUNDRY_EMBEDDING_CAPACITY', '120')}K TPM) |
| Search service / knowledge source | `{values.get('SEARCH_SERVICE_NAME', 'not available')}` / `{values.get('SEARCH_KNOWLEDGE_SOURCE_NAME', 'healthcare-policy-ks')}` (`{knowledge_mode}`) |
| OneLake source / indexed chunks | `lh_gold_curated/Files/{knowledge_target}/` / `{knowledge_count}` |

## Demo Links

- Fabric workspace: {markdown_link(workspace_name, fabric_url)}
- Foundry project: {markdown_link(project_name, foundry_root)}
- IQ orchestrator: {markdown_link(iq_agent, iq_url)}
- Non-IQ comparison: {markdown_link(non_iq_agent, non_iq_url)}

## Demo Stations

### Direct Fabric Data Agent

Open `{data_agent_name}` in the Fabric workspace. This is the governed data-only path.

Try:

1. `Show me denial rates by payer using all available data with no date filter`
2. `How many encounters are in each readmission risk category?`
3. `Show me medication adherence rates by drug class`

### Non-IQ Foundry Comparison

Open `{non_iq_agent}`. It has the policy knowledge base but no Fabric data tool.

Try:

1. `What does the denial appeal guide recommend for missing documentation?`
2. `Show me denial rates by payer`

The first prompt should return cited policy guidance. The second must state that live Fabric metrics are unavailable rather than estimating values.

### IQ Foundry Orchestrator

Open `{iq_agent}`. It combines governed Fabric IQ data with cited policy retrieval.

Try:

1. `Show me denial rates by payer using all available data with no date filter`
2. `What does the denial appeal guide recommend for missing documentation?`
3. `Show me denial rates by payer and explain the appeal process for the top denial reasons.`

## Certification

- Deployment certification: **{certification_state(values, functional, semantic)}**
- Functional suite: **{validation_summary(functional)}**
- Direct Lake canary: **{semantic.get('status', 'not available')}**
- Data Agent routing: `{values.get('FABRIC_DATA_AGENT_ROUTING', 'lakehouse_primary')}`
- Data Agent semantic source: `{values.get('FABRIC_DATA_AGENT_SEMANTIC_SOURCE', 'remove')}`

| DAX canary | Count |
|---|---:|
{count_rows}

Re-run certification:

```bash
set -a && eval "$(azd env get-values --environment {environment})" && set +a
python3 scripts/automation/functional_test_suite.py --workspace-name "$FABRIC_WORKSPACE_NAME" --project-endpoint "https://${{HUB_NAME}}.services.ai.azure.com/api/projects/${{PROJECT_NAME}}" --agent-name "$FOUNDRY_ORCHESTRATOR_AGENT_NAME" --comparison-agent-name "$FOUNDRY_KB_ONLY_AGENT_NAME" --search-service-name "$SEARCH_SERVICE_NAME" --knowledge-base-name "$SEARCH_KNOWLEDGE_BASE_NAME" --timeout-seconds 360 --enforce-success
```

## Cost Control

The F64 Fabric capacity is the primary idle-cost resource. Pause it only when no demo or test is running:

```bash
az rest --method post --url "https://management.azure.com/subscriptions/{subscription or '<subscription-id>'}/resourceGroups/{resource_group or '<resource-group>'}/providers/Microsoft.Fabric/capacities/{values.get('FABRIC_CAPACITY_NAME', '<capacity-name>')}/suspend?api-version=2023-11-01"
```

Resume it before using Fabric or either data path:

```bash
az rest --method post --url "https://management.azure.com/subscriptions/{subscription or '<subscription-id>'}/resourceGroups/{resource_group or '<resource-group>'}/providers/Microsoft.Fabric/capacities/{values.get('FABRIC_CAPACITY_NAME', '<capacity-name>')}/resume?api-version=2023-11-01"
```

## Known Legacy Limitation

The legacy `fabric_dataagent_preview` / `CustomKeys` project connection can return a Foundry credential/account-RP HTTP 500. The certified Fabric IQ `RemoteTool` path bypasses that backend and is the default.

## Certified Configuration

Keep `FABRIC_DATA_AGENT_ROUTING=lakehouse_primary` and `FABRIC_DATA_AGENT_SEMANTIC_SOURCE=remove`. The Direct Lake semantic model remains the Power BI and DAX baseline; it is intentionally not attached to the Data Agent.
"""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--environment", default=os.getenv("AZURE_ENV_NAME", "healthcare-demo"))
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    values = load_values(args.environment)
    workspace_id = resolve_workspace_id(values)
    if values.get("RUN_POSTDEPLOY_VALIDATION", "true").lower() == "true":
        functional = load_json(values.get("FUNCTIONAL_TEST_OUTPUT_PATH", ""), "logs/functional_test_latest.json")
        semantic = load_json(values.get("SEMANTIC_MODEL_HEALTH_LOG_PATH", ""), "logs/semantic_model_health_canary.json")
    else:
        functional = {}
        semantic = {}
    foundry_status = load_json("", "logs/foundry_completion_status.json")
    generated_utc = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    content = build_handoff(
        values,
        workspace_id,
        functional,
        semantic,
        generated_utc,
        foundry_status,
    )

    HANDOFF_DIR.mkdir(parents=True, exist_ok=True)
    output = HANDOFF_DIR / f"{safe_filename(args.environment)}-live-iq-demo.md"
    output.write_text(content, encoding="utf-8")
    print(f"[done] Your handoff file is here: {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())