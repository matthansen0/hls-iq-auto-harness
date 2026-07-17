#!/usr/bin/env python3
"""Validate Direct Lake ownership, framing refresh, and aggregate DAX access."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote

import requests


FABRIC_API = "https://api.fabric.microsoft.com/v1"
POWER_BI_API = "https://api.powerbi.com/v1.0/myorg"
FABRIC_RESOURCE = "https://api.fabric.microsoft.com"
POWER_BI_RESOURCE = "https://analysis.windows.net/powerbi/api"
TERMINAL_REFRESH_STATUSES = {"completed", "failed", "cancelled", "canceled", "disabled"}


class CanaryError(RuntimeError):
    """Raised when a canary prerequisite or request fails."""


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _run(command: list[str]) -> str:
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip()
        raise CanaryError(f"Command failed: {' '.join(command)}\n{detail}")
    return result.stdout.strip()


def get_access_token(resource: str) -> str:
    return _run(
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


def _request_json(
    method: str,
    url: str,
    *,
    token: str,
    body: dict[str, Any] | None = None,
    timeout: int = 90,
) -> tuple[requests.Response, dict[str, Any]]:
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    response = requests.request(method, url, headers=headers, json=body, timeout=timeout)
    try:
        payload = response.json() if response.content else {}
    except ValueError:
        payload = {"raw": response.text[:2000]}
    if not 200 <= response.status_code < 300:
        raise CanaryError(
            f"{method} {url} failed (HTTP {response.status_code}): "
            f"{json.dumps(payload, sort_keys=True)}"
        )
    return response, payload


def resolve_workspace_id(token: str, workspace_name: str) -> str:
    _, payload = _request_json("GET", f"{FABRIC_API}/workspaces", token=token)
    for workspace in payload.get("value", []):
        if workspace.get("displayName") == workspace_name:
            return str(workspace.get("id", "")).strip()
    raise CanaryError(f"Fabric workspace not found: {workspace_name}")


def resolve_semantic_model_id(token: str, workspace_id: str, model_name: str) -> str:
    encoded_workspace = quote(workspace_id, safe="")
    _, payload = _request_json(
        "GET",
        f"{FABRIC_API}/workspaces/{encoded_workspace}/items?type=SemanticModel",
        token=token,
    )
    for item in payload.get("value", []):
        if item.get("displayName") == model_name:
            return str(item.get("id", "")).strip()
    raise CanaryError(f"Semantic model not found: {model_name}")


def get_dataset_info(token: str, workspace_id: str, dataset_id: str) -> dict[str, Any]:
    _, payload = _request_json(
        "GET",
        f"{POWER_BI_API}/groups/{quote(workspace_id, safe='')}/datasets/"
        f"{quote(dataset_id, safe='')}",
        token=token,
    )
    return {
        "id": payload.get("id"),
        "name": payload.get("name"),
        "configuredBy": payload.get("configuredBy"),
        "isRefreshable": payload.get("isRefreshable"),
        "isEffectiveIdentityRequired": payload.get("isEffectiveIdentityRequired"),
        "isEffectiveIdentityRolesRequired": payload.get("isEffectiveIdentityRolesRequired"),
    }


def take_over_dataset(token: str, workspace_id: str, dataset_id: str) -> None:
    _request_json(
        "POST",
        f"{POWER_BI_API}/groups/{quote(workspace_id, safe='')}/datasets/"
        f"{quote(dataset_id, safe='')}/Default.TakeOver",
        token=token,
    )


def list_refreshes(
    token: str,
    workspace_id: str,
    dataset_id: str,
    *,
    top: int = 10,
) -> list[dict[str, Any]]:
    _, payload = _request_json(
        "GET",
        f"{POWER_BI_API}/groups/{quote(workspace_id, safe='')}/datasets/"
        f"{quote(dataset_id, safe='')}/refreshes?$top={top}",
        token=token,
    )
    return list(payload.get("value", []))


def _refresh_identity(refresh: dict[str, Any]) -> tuple[str, str]:
    return (
        str(refresh.get("requestId") or refresh.get("id") or ""),
        str(refresh.get("startTime") or ""),
    )


def trigger_and_wait_for_refresh(
    token: str,
    workspace_id: str,
    dataset_id: str,
    *,
    timeout_seconds: int,
    poll_seconds: int,
) -> dict[str, Any]:
    before = {_refresh_identity(item) for item in list_refreshes(token, workspace_id, dataset_id)}
    response, _ = _request_json(
        "POST",
        f"{POWER_BI_API}/groups/{quote(workspace_id, safe='')}/datasets/"
        f"{quote(dataset_id, safe='')}/refreshes",
        token=token,
        body={"type": "Full"},
    )
    request_id = response.headers.get("RequestId") or response.headers.get("x-ms-request-id") or ""
    deadline = time.monotonic() + timeout_seconds
    latest: dict[str, Any] = {}

    while time.monotonic() < deadline:
        refreshes = list_refreshes(token, workspace_id, dataset_id)
        matching = next(
            (
                item
                for item in refreshes
                if request_id
                and str(item.get("requestId") or item.get("id") or "").lower()
                == request_id.lower()
            ),
            None,
        )
        if matching is None:
            matching = next((item for item in refreshes if _refresh_identity(item) not in before), None)
        if matching is not None:
            latest = matching
            status = str(matching.get("status") or "").strip().lower()
            if status in TERMINAL_REFRESH_STATUSES:
                return _summarize_refresh(matching)
        time.sleep(max(2, poll_seconds))

    if latest:
        summary = _summarize_refresh(latest)
        summary["status"] = "TimedOut"
        return summary
    return {
        "status": "TimedOut",
        "requestId": request_id,
        "error": "No matching refresh appeared before the timeout.",
    }


def _summarize_refresh(refresh: dict[str, Any]) -> dict[str, Any]:
    return {
        "requestId": refresh.get("requestId") or refresh.get("id"),
        "status": refresh.get("status"),
        "refreshType": refresh.get("refreshType"),
        "startTime": refresh.get("startTime"),
        "endTime": refresh.get("endTime"),
        "serviceExceptionJson": refresh.get("serviceExceptionJson"),
    }


def execute_dax_counts(
    token: str,
    workspace_id: str,
    dataset_id: str,
    *,
    timeout_seconds: int,
) -> dict[str, int]:
    dax = (
        'EVALUATE ROW("Patients", COUNTROWS(dim_patient), '
        '"Claims", COUNTROWS(fact_claim), '
        '"Encounters", COUNTROWS(fact_encounter))'
    )
    _, payload = _request_json(
        "POST",
        f"{POWER_BI_API}/groups/{quote(workspace_id, safe='')}/datasets/"
        f"{quote(dataset_id, safe='')}/executeQueries",
        token=token,
        body={
            "queries": [{"query": dax}],
            "serializerSettings": {"includeNulls": True},
        },
        timeout=timeout_seconds,
    )
    try:
        row = payload["results"][0]["tables"][0]["rows"][0]
    except (KeyError, IndexError, TypeError) as exc:
        raise CanaryError(f"DAX response did not contain a result row: {json.dumps(payload)}") from exc

    counts: dict[str, int] = {}
    for raw_name, value in row.items():
        name = str(raw_name).replace("[", "").replace("]", "")
        counts[name] = int(value)
    return counts


def write_result(path: Path, result: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result, indent=2, sort_keys=True), encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace-id", default="", help="Fabric workspace GUID")
    parser.add_argument(
        "--workspace-name",
        default="HealthcareDemo-WS",
        help="Workspace name used when --workspace-id is omitted",
    )
    parser.add_argument("--dataset-id", default="", help="Semantic model/dataset GUID")
    parser.add_argument(
        "--dataset-name",
        default="HealthcareDemoHLS",
        help="Semantic model name used when --dataset-id is omitted",
    )
    parser.add_argument(
        "--take-over",
        action="store_true",
        help="Transfer model ownership to the authenticated CLI user before testing",
    )
    parser.add_argument(
        "--refresh",
        action="store_true",
        help="Trigger and wait for a full Direct Lake framing refresh",
    )
    parser.add_argument("--refresh-timeout-seconds", type=int, default=600)
    parser.add_argument("--poll-seconds", type=int, default=10)
    parser.add_argument("--dax-timeout-seconds", type=int, default=120)
    parser.add_argument(
        "--output",
        default="logs/semantic_model_health_canary.json",
        help="Redacted JSON result path",
    )
    parser.add_argument(
        "--enforce-success",
        action="store_true",
        help="Return nonzero when refresh or DAX validation fails",
    )
    return parser.parse_args()


def run_canary(args: argparse.Namespace) -> dict[str, Any]:
    fabric_token = get_access_token(FABRIC_RESOURCE)
    power_bi_token = get_access_token(POWER_BI_RESOURCE)
    workspace_id = args.workspace_id.strip() or resolve_workspace_id(
        fabric_token, args.workspace_name
    )
    dataset_id = args.dataset_id.strip() or resolve_semantic_model_id(
        fabric_token, workspace_id, args.dataset_name
    )

    result: dict[str, Any] = {
        "tsUtc": _utc_now(),
        "workspaceId": workspace_id,
        "datasetId": dataset_id,
        "datasetName": args.dataset_name,
        "takeOverRequested": bool(args.take_over),
        "refreshRequested": bool(args.refresh),
    }
    result["datasetBefore"] = get_dataset_info(power_bi_token, workspace_id, dataset_id)

    if args.take_over:
        take_over_dataset(power_bi_token, workspace_id, dataset_id)
    result["datasetAfter"] = get_dataset_info(power_bi_token, workspace_id, dataset_id)

    if args.refresh:
        result["refresh"] = trigger_and_wait_for_refresh(
            power_bi_token,
            workspace_id,
            dataset_id,
            timeout_seconds=max(30, args.refresh_timeout_seconds),
            poll_seconds=max(2, args.poll_seconds),
        )

    result["daxCounts"] = execute_dax_counts(
        power_bi_token,
        workspace_id,
        dataset_id,
        timeout_seconds=max(10, args.dax_timeout_seconds),
    )
    refresh_status = str(result.get("refresh", {}).get("status") or "Completed").lower()
    counts_ok = all(value > 0 for value in result["daxCounts"].values())
    result["status"] = (
        "passed"
        if counts_ok and refresh_status in {"completed", "succeeded"}
        else "failed"
    )
    return result


def main() -> int:
    args = parse_args()
    output_path = Path(args.output)
    if not output_path.is_absolute():
        output_path = Path(__file__).resolve().parents[2] / output_path

    try:
        result = run_canary(args)
    except Exception as exc:
        result = {
            "tsUtc": _utc_now(),
            "status": "failed",
            "errorType": type(exc).__name__,
            "error": str(exc),
        }
        write_result(output_path, result)
        print(f"[FAIL] Semantic model health canary: {exc}")
        print(f"[INFO] Result: {output_path}")
        return 1 if args.enforce_success else 0

    write_result(output_path, result)
    print(f"[{'OK' if result['status'] == 'passed' else 'FAIL'}] Semantic model canary: {result['status']}")
    print(f"[INFO] Dataset owner: {result['datasetAfter'].get('configuredBy')}")
    print(f"[INFO] DAX counts: {json.dumps(result['daxCounts'], sort_keys=True)}")
    print(f"[INFO] Result: {output_path}")
    if args.enforce_success and result["status"] != "passed":
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
