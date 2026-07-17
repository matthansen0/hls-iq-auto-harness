from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

import requests


REPO_ROOT = Path(__file__).resolve().parents[1]
AUTOMATION_DIR = REPO_ROOT / "scripts" / "automation"
AZD_DIR = REPO_ROOT / "scripts" / "azd"
sys.path.insert(0, str(AUTOMATION_DIR))
sys.path.insert(0, str(AZD_DIR))

import automate_foundry_remaining as foundry  # noqa: E402
import functional_test_suite as functional  # noqa: E402
import generate_live_iq_handoff as handoff  # noqa: E402
import postprovision  # noqa: E402
import semantic_model_health_canary as semantic  # noqa: E402


BASE_ENV = {
    "AZURE_SUBSCRIPTION_ID": "subscription",
    "AZURE_RESOURCE_GROUP": "resource-group",
    "HUB_NAME": "foundry-account",
    "SEARCH_SERVICE_NAME": "search-service",
}


class LauncherNotebookTests(unittest.TestCase):
    def test_magic_pip_install_is_replaced_with_retrying_python(self) -> None:
        raw = {
            "cells": [
                {
                    "cell_type": "code",
                    "source": [
                        "# setup\n",
                        f"{postprovision._FABRIC_LAUNCHER_PIP_INSTALL}\n",
                        "import notebookutils\n",
                    ],
                }
            ]
        }

        changed = postprovision._harden_launcher_notebook(raw)
        source = "".join(raw["cells"][0]["source"])

        self.assertTrue(changed)
        self.assertNotIn("%pip install", source)
        self.assertIn("subprocess.check_call(cmd)", source)
        self.assertIn("import notebookutils", source)


class FabricModeTests(unittest.TestCase):
    def load_config(self, mode: str) -> foundry.Cfg:
        with patch.dict(os.environ, {**BASE_ENV, "FOUNDRY_FABRIC_MODE": mode}, clear=True):
            return foundry.load_cfg()

    def test_fabric_iq_connection_and_tool_payloads(self) -> None:
        cfg = self.load_config("fabric_iq")
        endpoint = foundry.build_fabric_mcp_endpoint(
            cfg, workspace_id="workspace", data_agent_id="data-agent"
        )
        properties = foundry.build_fabric_connection_properties(
            cfg, workspace_id="workspace", data_agent_id="data-agent"
        )
        tool = foundry.build_fabric_tool(
            cfg,
            fabric_connection_id="connection-id",
            fabric_mcp_endpoint=endpoint,
        )

        self.assertEqual(properties["category"], "RemoteTool")
        self.assertEqual(properties["authType"], "UserEntraToken")
        self.assertEqual(properties["audience"], "https://analysis.windows.net/powerbi/api")
        self.assertEqual(properties["target"], endpoint)
        self.assertEqual(tool["type"], "fabric_iq_preview")
        self.assertEqual(tool["project_connection_id"], "connection-id")
        self.assertEqual(tool["require_approval"], "never")

    def test_legacy_connection_and_tool_payloads(self) -> None:
        cfg = self.load_config("legacy")
        properties = foundry.build_fabric_connection_properties(
            cfg, workspace_id="workspace", data_agent_id="data-agent"
        )
        tool = foundry.build_fabric_tool(
            cfg,
            fabric_connection_id="connection-id",
            fabric_mcp_endpoint="unused",
        )

        self.assertEqual(properties["category"], "CustomKeys")
        self.assertEqual(properties["credentials"]["keys"]["workspace-id"], "workspace")
        self.assertEqual(tool["type"], "fabric_dataagent_preview")

    def test_invalid_fabric_mode_is_rejected(self) -> None:
        with patch.dict(
            os.environ,
            {**BASE_ENV, "FOUNDRY_FABRIC_MODE": "unknown"},
            clear=True,
        ):
            with self.assertRaisesRegex(ValueError, "FOUNDRY_FABRIC_MODE"):
                foundry.load_cfg()

    def test_disabled_mode_has_no_connection_payload(self) -> None:
        cfg = self.load_config("disabled")
        with self.assertRaisesRegex(ValueError, "disabled"):
            foundry.build_fabric_connection_properties(
                cfg, workspace_id="workspace", data_agent_id="data-agent"
            )

    def test_indexed_onelake_knowledge_source_uses_managed_identity_models(self) -> None:
        body = foundry.build_indexed_onelake_knowledge_source_body(
            name="healthcare-policy-ks",
            workspace_id="workspace-id",
            lakehouse_id="lakehouse-id",
            target_path="healthcare_knowledge",
            model_resource_uri="https://foundry-account.openai.azure.com/",
            embedding_deployment_name="text-embedding-ada-002",
            embedding_model_name="text-embedding-ada-002",
            ingestion_interval="P1D",
        )

        self.assertEqual(body["kind"], "indexedOneLake")
        parameters = body["indexedOneLakeParameters"]
        self.assertEqual(parameters["fabricWorkspaceId"], "workspace-id")
        self.assertEqual(parameters["lakehouseId"], "lakehouse-id")
        self.assertEqual(parameters["targetPath"], "healthcare_knowledge")
        ingestion = parameters["ingestionParameters"]
        self.assertIsNone(ingestion["identity"])
        self.assertTrue(ingestion["disableImageVerbalization"])
        self.assertEqual(ingestion["ingestionSchedule"], {"interval": "P1D"})
        model = ingestion["embeddingModel"]["azureOpenAIParameters"]
        self.assertEqual(model["resourceUri"], "https://foundry-account.openai.azure.com")
        self.assertEqual(model["deploymentId"], "text-embedding-ada-002")
        self.assertNotIn("apiKey", model)
        self.assertNotIn("authIdentity", model)

    def test_indexed_onelake_created_resources_are_read_from_persisted_source(self) -> None:
        resources = foundry._indexed_onelake_created_resources(
            {
                "indexedOneLakeParameters": {
                    "createdResources": {
                        "datasource": "policy-datasource",
                        "indexer": "policy-indexer",
                        "skillset": "policy-skillset",
                        "index": "policy-index",
                    }
                }
            }
        )

        self.assertEqual(resources["indexer"], "policy-indexer")
        self.assertEqual(resources["index"], "policy-index")

    def test_onelake_is_the_default_knowledge_mode(self) -> None:
        with patch.dict(os.environ, BASE_ENV, clear=True):
            cfg = foundry.load_cfg()

        self.assertEqual(cfg.search_knowledge_mode, "onelake")
        self.assertEqual(cfg.search_knowledge_onelake_target_path, "healthcare_knowledge")
        self.assertEqual(cfg.search_knowledge_ingestion_interval, "P1D")
        self.assertEqual(cfg.search_knowledge_retrieval_reasoning_effort, "medium")

    def test_onelake_knowledge_base_uses_current_chat_model(self) -> None:
        with patch.dict(
            os.environ,
            {
                **BASE_ENV,
                "SEARCH_KNOWLEDGE_MODE": "onelake",
                "FOUNDRY_CHAT_DEPLOYMENT_NAME": "gpt-5.4-deployment",
                "FOUNDRY_CHAT_MODEL_NAME": "gpt-5.4",
            },
            clear=True,
        ):
            cfg = foundry.load_cfg()

        body = foundry.build_search_knowledge_base_body(cfg)
        self.assertEqual(body["outputMode"], "extractiveData")
        self.assertEqual(body["retrievalReasoningEffort"], {"kind": "medium"})
        self.assertEqual(body["knowledgeSources"], [{"name": "healthcare-policy-ks"}])
        model = body["models"][0]["azureOpenAIParameters"]
        self.assertEqual(model["deploymentId"], "gpt-5.4-deployment")
        self.assertEqual(model["modelName"], "gpt-5.4")
        self.assertNotIn("apiKey", model)
        self.assertNotIn("authIdentity", model)

    def test_knowledge_source_ingestion_state_prefers_active_run(self) -> None:
        status, errors = foundry._knowledge_source_ingestion_state(
            {
                "synchronizationStatus": "active",
                "currentSynchronizationState": {
                    "startTime": "2025-01-01T00:00:00Z",
                    "itemUpdatesProcessed": 4,
                    "errors": [],
                },
                "lastSynchronizationState": {"status": "success"},
            }
        )

        self.assertEqual(status, "running")
        self.assertEqual(errors, [])

    def test_knowledge_source_ingestion_state_reports_terminal_errors(self) -> None:
        error = {"key": "policy.md", "errorMessage": "Permission denied"}
        status, errors = foundry._knowledge_source_ingestion_state(
            {
                "lastSynchronizationState": {
                    "status": "partialSuccess",
                    "errors": [error],
                }
            }
        )

        self.assertEqual(status, "partialsuccess")
        self.assertEqual(errors, [error])

    def test_knowledge_source_ingestion_state_infers_success_without_status(self) -> None:
        status, errors = foundry._knowledge_source_ingestion_state(
            {
                "synchronizationStatus": "active",
                "currentSynchronizationState": None,
                "lastSynchronizationState": {
                    "startTime": "2025-01-01T00:00:00Z",
                    "endTime": "2025-01-01T00:01:00Z",
                    "itemsUpdatesProcessed": 26,
                    "itemsUpdatesFailed": 0,
                    "errors": [],
                },
            }
        )

        self.assertEqual(status, "success")
        self.assertEqual(errors, [])

    def test_knowledge_source_ingestion_state_infers_partial_success_from_count(self) -> None:
        status, errors = foundry._knowledge_source_ingestion_state(
            {
                "synchronizationStatus": "active",
                "currentSynchronizationState": None,
                "lastSynchronizationState": {
                    "endTime": "2025-01-01T00:01:00Z",
                    "itemsUpdatesProcessed": 26,
                    "itemsUpdatesFailed": 8,
                    "errors": [],
                },
            }
        )

        self.assertEqual(status, "partialsuccess")
        self.assertEqual(errors, [{"itemsFailed": 8}])

    def test_lakehouse_primary_routing_replaces_only_routing_block(self) -> None:
        original = (
            "Header\n\n"
            "TWO DATA SOURCES - PICK EXACTLY ONE PER QUESTION\n"
            "1. semantic model\n\n"
            "ROUTING\n- semantic first\n\n"
            "RESPONSE\n- Query first\n\nFooter"
        )
        updated = foundry._lakehouse_primary_instructions(original)
        self.assertIn("lakehouse_tables 'lh_gold_curated' (PRIMARY, default)", updated)
        self.assertIn("RESPONSE\n- Query first", updated)
        self.assertTrue(updated.endswith("Footer"))

    def test_lakehouse_primary_routing_is_idempotent(self) -> None:
        original = (
            "TWO DATA SOURCES - PICK EXACTLY ONE PER QUESTION\n"
            "1. semantic model\n\nROUTING\n- semantic first\n\nRESPONSE\n- Query first"
        )
        once = foundry._lakehouse_primary_instructions(original)
        twice = foundry._lakehouse_primary_instructions(once)
        self.assertEqual(once, twice)

    def test_repository_datasource_metadata_contains_lakehouse_rules(self) -> None:
        cfg = self.load_config("fabric_iq")
        metadata = foundry._load_data_agent_source_metadata(cfg)
        lakehouse = metadata["lh_gold_curated"]
        self.assertIn("NEVER add date filters", lakehouse["instructions"])
        self.assertIn("PRIMARY governed SQL source", lakehouse["description"])
        semantic_model = metadata["HealthcareDemoHLS"]
        self.assertIn("OPTIONAL semantic-model source", semantic_model["description"])
        self.assertIn("OPTIONAL SOURCE FOR EXPLICIT NAMED DAX MEASURES ONLY", semantic_model["instructions"])

    def test_semantic_source_restore_body_uses_item_reference(self) -> None:
        body = foundry._semantic_source_restore_body(
            {
                "source": {
                    "itemReference": {
                        "referenceType": "ById",
                        "itemId": "semantic-model-id",
                        "workspaceId": "workspace-id",
                    }
                }
            }
        )
        self.assertEqual(body["type"], "FabricItem")
        self.assertEqual(body["itemReference"]["itemId"], "semantic-model-id")

    def test_repository_fewshot_has_explicit_denial_rate_grain(self) -> None:
        cfg = self.load_config("fabric_iq")
        fewshots = foundry._load_data_agent_fewshots(cfg)["lh_gold_curated"]
        denial = next(item for item in fewshots if item["id"] == "607669a7-e39a-4456-bff4-80f32949f6d9")
        self.assertIn("all available data with no date filter", denial["question"])
        self.assertNotIn("WHERE", denial["query"].upper())


class McpProtocolTests(unittest.TestCase):
    def response(self, content: str, content_type: str) -> requests.Response:
        response = requests.Response()
        response.status_code = 200
        response._content = content.encode("utf-8")
        response.headers["Content-Type"] = content_type
        return response

    def test_decode_json_response(self) -> None:
        response = self.response(
            '{"jsonrpc":"2.0","id":1,"result":{"tools":[]}}',
            "application/json",
        )
        payload = functional.StreamableHttpMcpClient._decode_response(response)
        self.assertEqual(payload["result"]["tools"], [])

    def test_decode_sse_response_uses_last_data_event(self) -> None:
        response = self.response(
            'event: message\ndata: {"jsonrpc":"2.0","id":1,"result":{"step":1}}\n\n'
            'event: message\ndata: {"jsonrpc":"2.0","id":1,"result":{"step":2}}\n\n',
            "text/event-stream",
        )
        payload = functional.StreamableHttpMcpClient._decode_response(response)
        self.assertEqual(payload["result"]["step"], 2)

    def test_decode_whitespace_only_response(self) -> None:
        response = self.response("\n", "application/json")
        payload = functional.StreamableHttpMcpClient._decode_response(response)
        self.assertEqual(payload, {})

    def test_decode_non_json_response_reports_protocol_context(self) -> None:
        response = self.response("Accepted", "text/plain")
        with self.assertRaisesRegex(functional.FunctionalTestError, "content-type=text/plain"):
            functional.StreamableHttpMcpClient._decode_response(response)

    def test_first_tool_argument_prefers_documented_names(self) -> None:
        tool = {
            "name": "DataAgent",
            "inputSchema": {
                "properties": {
                    "other": {"type": "string"},
                    "userQuestion": {"type": "string"},
                }
            },
        }
        self.assertEqual(functional.first_tool_argument(tool), "userQuestion")

    def test_build_tool_arguments_uses_scalar_for_data_agent(self) -> None:
        tool = {
            "inputSchema": {
                "properties": {"userQuestion": {"type": "string"}}
            }
        }
        self.assertEqual(
            functional.build_tool_arguments(tool, "Question?"),
            {"userQuestion": "Question?"},
        )

    def test_build_tool_arguments_uses_array_for_search_kb(self) -> None:
        tool = {
            "inputSchema": {
                "properties": {
                    "queries": {"type": "array", "items": {"type": "string"}}
                }
            }
        }
        self.assertEqual(
            functional.build_tool_arguments(tool, "Question?"),
            {"queries": ["Question?"]},
        )

    def test_notification_acknowledgement_does_not_require_json(self) -> None:
        client = functional.StreamableHttpMcpClient(
            "https://example.invalid/mcp", "token", 10
        )
        response = self.response("Accepted", "text/plain; charset=utf-8")
        response.status_code = 202
        with patch.object(client.session, "post", return_value=response):
            payload = client._post(
                {"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}},
                expect_response=False,
            )
        self.assertEqual(payload, {})


class FunctionalAssertionTests(unittest.TestCase):
    def test_artifact_inventory_passes_with_required_items(self) -> None:
        items = [
            {"type": item_type, "displayName": display_name}
            for item_type, names in functional.EXPECTED_ARTIFACTS.items()
            for display_name in names
        ]
        status, _, details = functional.test_artifact_inventory(items)
        self.assertEqual(status, "passed")
        self.assertFalse(details.get("missing"))

    def test_artifact_inventory_lists_missing_items(self) -> None:
        status, _, details = functional.test_artifact_inventory([])
        self.assertEqual(status, "failed")
        self.assertIn("SemanticModel/HealthcareDemoHLS", details["missing"])

    def test_refresh_summary_excludes_unneeded_payload_fields(self) -> None:
        summary = semantic._summarize_refresh(
            {
                "requestId": "request",
                "status": "Completed",
                "refreshType": "ViaApi",
                "startTime": "start",
                "endTime": "end",
                "serviceExceptionJson": None,
                "unexpectedSensitiveField": "not-copied",
            }
        )
        self.assertEqual(summary["requestId"], "request")
        self.assertNotIn("unexpectedSensitiveField", summary)

    def test_foundry_tool_evidence_requires_completed_calls_and_counts_citations(self) -> None:
        evidence = functional.foundry_tool_evidence(
            {
                "output": [
                    {
                        "type": "mcp_call",
                        "status": "completed",
                        "name": "DataAgent_HealthcareHLSAgent",
                        "server_label": "healthcare-data",
                    },
                    {
                        "type": "mcp_call",
                        "status": "calling",
                        "name": "ignored",
                        "server_label": "knowledge-base",
                    },
                    {
                        "type": "message",
                        "content": [
                            {"type": "output_text", "annotations": [{}, {}]}
                        ],
                    },
                ]
            }
        )
        self.assertEqual(evidence["completedToolCalls"], 1)
        self.assertEqual(evidence["serverLabels"], ["healthcare-data"])
        self.assertEqual(evidence["citationAnnotations"], 2)

    def test_percentage_values_extracts_named_rates(self) -> None:
        values = functional._percentage_values(
            "Aetna: 15.9%\nMedicare Part A | 15.3%",
            ["Aetna", "Medicare Part A", "Cigna"],
        )
        self.assertEqual(values, {"Aetna": 15.9, "Medicare Part A": 15.3})


class HandoffTests(unittest.TestCase):
    def test_parse_azd_values_allows_deployment_metadata_only(self) -> None:
        values = handoff.parse_azd_values(
            'AZURE_ENV_NAME="demo"\n'
            'FABRIC_WORKSPACE_NAME="Healthcare Demo"\n'
            'SEARCH_ADMIN_KEY="must-not-appear"\n'
        )
        self.assertEqual(values["AZURE_ENV_NAME"], "demo")
        self.assertEqual(values["FABRIC_WORKSPACE_NAME"], "Healthcare Demo")
        self.assertNotIn("SEARCH_ADMIN_KEY", values)

    def test_build_handoff_contains_links_and_certification(self) -> None:
        content = handoff.build_handoff(
            {
                "AZURE_ENV_NAME": "demo",
                "AZURE_SUBSCRIPTION_ID": "11111111-1111-4111-8111-111111111111",
                "AZURE_RESOURCE_GROUP": "rg-demo",
                "HUB_NAME": "hubdemo",
                "PROJECT_NAME": "HealthcareDemo-HLS",
                "FABRIC_WORKSPACE_NAME": "HealthcareDemo-WS",
                "FOUNDRY_ORCHESTRATOR_AGENT_NAME": "HealthcareOrchestratorAgent2",
                "FOUNDRY_EMBEDDING_CAPACITY": "120",
                "FOUNDRY_EMBEDDING_DEPLOYMENT_NAME": "text-embedding-ada-002",
                "SEARCH_KNOWLEDGE_MODE": "onelake",
                "SEARCH_KNOWLEDGE_ONELAKE_TARGET_PATH": "healthcare_knowledge",
            },
            "00000000-0000-0000-0000-000000000001",
            {
                "summary": {
                    "passed": 12,
                    "failed": 0,
                    "skipped": 0,
                    "consent_required": 0,
                    "approval_required": 0,
                }
            },
            {"status": "passed", "counts": {"Patients": 10000}},
            "2025-01-01T00:00:00Z",
            {"knowledgeSource": {"documentCount": 42}},
        )
        self.assertIn("groups/00000000-0000-0000-0000-000000000001/list", content)
        self.assertIn("12 passed, 0 failed", content)
        self.assertIn("Deployment certification: **PASSED**", content)
        self.assertIn("| Patients | 10,000 |", content)
        self.assertIn("text-embedding-ada-002", content)
        self.assertIn("120K TPM", content)
        self.assertIn("lh_gold_curated/Files/healthcare_knowledge/", content)
        self.assertIn("/ `42`", content)
        self.assertIn("This is a local generated artifact", content)

    def test_build_handoff_marks_disabled_validation_as_skipped(self) -> None:
        content = handoff.build_handoff(
            {
                "AZURE_ENV_NAME": "demo",
                "RUN_POSTDEPLOY_VALIDATION": "false",
            },
            "",
            {},
            {},
            "2025-01-01T00:00:00Z",
        )
        self.assertIn("Deployment certification: **SKIPPED**", content)
        self.assertIn("Not run or no result file was available", content)

    def test_load_values_prefers_explicit_azd_environment(self) -> None:
        with patch.dict(
            os.environ,
            {"FABRIC_WORKSPACE_NAME": "stale-shell-workspace"},
            clear=True,
        ), patch.object(
            handoff,
            "run",
            return_value=(
                0,
                'AZURE_ENV_NAME="target"\nFABRIC_WORKSPACE_NAME="target-workspace"',
            ),
        ):
            values = handoff.load_values("target")
        self.assertEqual(values["AZURE_ENV_NAME"], "target")
        self.assertEqual(values["FABRIC_WORKSPACE_NAME"], "target-workspace")

    def test_safe_filename_rejects_path_segments(self) -> None:
        self.assertEqual(handoff.safe_filename("../demo env"), "demo-env")

    def test_run_all_finalization_generates_handoff(self) -> None:
        run_all = (REPO_ROOT / "scripts" / "azd" / "run_all.sh").read_text()
        self.assertIn("generate_live_iq_handoff.py --environment", run_all)
        self.assertIn('set_if_missing FOUNDRY_AUTOMATION_ENFORCE_SUCCESS "true"', run_all)
        self.assertIn('set_if_missing SEARCH_KNOWLEDGE_MODE "onelake"', run_all)
        self.assertIn('set_if_missing FOUNDRY_EMBEDDING_CAPACITY "120"', run_all)
        self.assertIn(
            'set_if_missing SEARCH_KNOWLEDGE_ONELAKE_TARGET_PATH "healthcare_knowledge"',
            run_all,
        )
        self.assertIn('"${semantic_args[@]}" || return $?', run_all)
        self.assertIn('"${functional_args[@]}" || return $?', run_all)
        self.assertIn('"${FUNCTIONAL_TEST_OUTPUT_PATH:-logs/functional_test_latest.json}"', run_all)
        self.assertGreaterEqual(run_all.count("finalize_deployment"), 3)


if __name__ == "__main__":
    unittest.main()
