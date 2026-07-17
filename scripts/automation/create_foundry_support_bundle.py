#!/usr/bin/env python3
"""Create a redacted support bundle for Foundry connection failures."""

from __future__ import annotations

import argparse
import ast
import json
import os
import re
import subprocess
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
UUID_RE = re.compile(r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-5][0-9a-fA-F]{3}-[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}\b")
OPERATION_RE = re.compile(r"(?:operation|request)\\?['\"]?\s*:\s*\\?['\"]([0-9a-fA-F]{12,})")
SAFE_ENV_KEYS = {
    "AZURE_ENV_NAME",
    "AZURE_LOCATION",
    "AZURE_RESOURCE_GROUP",
    "AZURE_SUBSCRIPTION_ID",
    "FABRIC_WORKSPACE_NAME",
    "FABRIC_DATA_AGENT_NAME",
    "FOUNDRY_FABRIC_CONNECTION_NAME",
    "FOUNDRY_KB_CONNECTION_NAME",
    "FOUNDRY_ORCHESTRATOR_AGENT_NAME",
    "FOUNDRY_KB_ONLY_AGENT_NAME",
    "PROJECT_NAME",
    "HUB_NAME",
    "SEARCH_SERVICE_NAME",
    "SEARCH_KNOWLEDGE_SOURCE_NAME",
    "SEARCH_KNOWLEDGE_BASE_NAME",
    "LOCATION",
}
LOG_FILES = (
    "logs/foundry_connection_blockers.jsonl",
    "logs/foundry_completion_status.json",
    "logs/foundry_self_heal_status.json",
    "logs/foundry_region_matrix.json",
    "logs/probe_cleanup_report.json",
    "logs/postprovision_failure_events.jsonl",
)


def run(command: list[str]) -> tuple[int, str, str]:
    result = subprocess.run(command, capture_output=True, text=True)
    return result.returncode, result.stdout.strip(), result.stderr.strip()


def parse_azd_values(raw: str) -> dict[str, str]:
    values: dict[str, str] = {}
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
            values[key] = value
    return values


def redact_text(text: str) -> str:
    text = re.sub(r"Bearer\s+[A-Za-z0-9._~+/-]+", "Bearer <redacted>", text, flags=re.IGNORECASE)
    text = re.sub(r"(api[-_]?key|password|secret|token|connectionstring)\s*[:=]\s*[^,\s}\"]+", r"\1=<redacted>", text, flags=re.IGNORECASE)
    return text


def collect_ids(text: str) -> dict[str, list[str]]:
    redacted = redact_text(text)
    uuids = sorted(set(UUID_RE.findall(redacted)))
    pairs = sorted(set(OPERATION_RE.findall(redacted)))
    return {"uuidCandidates": uuids, "operationOrRequestCandidates": pairs}


def write_json(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def copy_redacted(source: Path, destination: Path) -> str:
    text = source.read_text(encoding="utf-8", errors="replace")
    destination.write_text(redact_text(text), encoding="utf-8")
    return text


def resource_snapshot(subscription: str, resource_group: str, resource_id: str) -> dict[str, Any]:
    if not resource_id:
        return {"status": "notConfigured"}
    code, stdout, stderr = run(
        [
            "az",
            "resource",
            "show",
            "--ids",
            resource_id,
            "--query",
            "{id:id,name:name,type:type,location:location,provisioningState:properties.provisioningState,customSubDomainName:properties.customSubDomainName,allowProjectManagement:properties.allowProjectManagement}",
            "-o",
            "json",
        ]
    )
    if code != 0:
        return {"status": "unavailable", "error": stderr[:500]}
    try:
        return json.loads(stdout)
    except json.JSONDecodeError:
        return {"status": "unavailable", "raw": stdout[:1000]}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Create a redacted Foundry support bundle")
    parser.add_argument("--environment", default=os.getenv("AZURE_ENV_NAME", ""))
    parser.add_argument("--output-dir", default="logs/support-bundles")
    parser.add_argument("--no-zip", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if not args.environment:
        print("[ERROR] --environment or AZURE_ENV_NAME is required.")
        return 2

    code, raw_values, stderr = run(["azd", "env", "get-values", "--environment", args.environment])
    if code != 0:
        print(f"[ERROR] Could not read azd environment: {stderr}")
        return 2
    values = parse_azd_values(raw_values)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    bundle_dir = REPO_ROOT / args.output_dir / f"{args.environment}-{timestamp}"
    bundle_dir.mkdir(parents=True, exist_ok=True)

    safe_values = {key: values[key] for key in sorted(SAFE_ENV_KEYS) if key in values}
    safe_values["environment"] = args.environment
    write_json(bundle_dir / "environment.json", safe_values)

    account_payload: dict[str, Any] = {"status": "unavailable"}
    account_code, account_out, account_err = run(
        [
            "az",
            "account",
            "show",
            "--query",
            "{subscriptionId:id,tenantId:tenantId,userType:user.type}",
            "-o",
            "json",
        ]
    )
    if account_code == 0:
        try:
            account_payload = json.loads(account_out)
        except json.JSONDecodeError:
            account_payload = {"raw": account_out[:1000]}
    else:
        account_payload["error"] = account_err[:500]
    write_json(bundle_dir / "account.json", account_payload)

    hub_id = ""
    if values.get("AZURE_SUBSCRIPTION_ID") and values.get("AZURE_RESOURCE_GROUP") and values.get("HUB_NAME"):
        hub_id = (
            f"/subscriptions/{values['AZURE_SUBSCRIPTION_ID']}/resourceGroups/{values['AZURE_RESOURCE_GROUP']}"
            f"/providers/Microsoft.CognitiveServices/accounts/{values['HUB_NAME']}"
        )
    search_id = ""
    if values.get("AZURE_SUBSCRIPTION_ID") and values.get("AZURE_RESOURCE_GROUP") and values.get("SEARCH_SERVICE_NAME"):
        search_id = (
            f"/subscriptions/{values['AZURE_SUBSCRIPTION_ID']}/resourceGroups/{values['AZURE_RESOURCE_GROUP']}"
            f"/providers/Microsoft.Search/searchServices/{values['SEARCH_SERVICE_NAME']}"
        )
    write_json(
        bundle_dir / "resources.json",
        {
            "hub": resource_snapshot(values.get("AZURE_SUBSCRIPTION_ID", ""), values.get("AZURE_RESOURCE_GROUP", ""), hub_id),
            "search": resource_snapshot(values.get("AZURE_SUBSCRIPTION_ID", ""), values.get("AZURE_RESOURCE_GROUP", ""), search_id),
        },
    )

    provider_payload: dict[str, Any] = {"status": "unavailable"}
    provider_code, provider_out, provider_err = run(
        [
            "az",
            "provider",
            "show",
            "-n",
            "Microsoft.CognitiveServices",
            "--query",
            "resourceTypes[?resourceType=='accounts/projects'].{locations:locations,apiVersions:apiVersions}",
            "-o",
            "json",
        ]
    )
    if provider_code == 0:
        try:
            provider_payload = json.loads(provider_out)
        except json.JSONDecodeError:
            provider_payload = {"raw": provider_out[:2000]}
    else:
        provider_payload["error"] = provider_err[:1000]
    write_json(bundle_dir / "provider_metadata.json", provider_payload)

    all_log_text = ""
    copied_logs: list[str] = []
    for relative in LOG_FILES:
        source = REPO_ROOT / relative
        if not source.exists():
            continue
        destination = bundle_dir / "logs" / Path(relative).name
        destination.parent.mkdir(parents=True, exist_ok=True)
        all_log_text += copy_redacted(source, destination) + "\n"
        copied_logs.append(relative)

    write_json(bundle_dir / "correlation_ids.json", collect_ids(all_log_text))

    git_code, git_out, git_err = run(["git", "rev-parse", "HEAD"])
    status_code, status_out, status_err = run(["git", "status", "--short"])
    write_json(
        bundle_dir / "repository.json",
        {
            "commit": git_out if git_code == 0 else "unknown",
            "status": status_out if status_code == 0 else status_err,
        },
    )

    summary = (
        "This bundle is redacted and contains environment metadata, resource snapshots, "
        "Foundry/Fabric failure logs, and extracted correlation identifiers."
    )
    (bundle_dir / "README.txt").write_text(summary + "\n", encoding="utf-8")
    manifest = {
        "createdUtc": timestamp,
        "environment": args.environment,
        "bundleDirectory": str(bundle_dir),
        "includedLogs": copied_logs,
        "redaction": "Bearer tokens and secret-like key values were removed.",
    }
    write_json(bundle_dir / "manifest.json", manifest)

    if not args.no_zip:
        archive = bundle_dir.with_suffix(".zip")
        with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            for path in bundle_dir.rglob("*"):
                if path.is_file():
                    zf.write(path, path.relative_to(bundle_dir.parent))
        print(f"[OK] Support archive written: {archive}")
    print(f"[OK] Support bundle written: {bundle_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
