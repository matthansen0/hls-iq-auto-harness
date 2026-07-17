# Automation Scripts

This folder contains automation used by the AZD post-provision flow.

Operational documentation: [AZD Automation Guide](../../docs/AZD_AUTOMATION_GUIDE.md)

## Scripts
- `automate_foundry_remaining.py`: Idempotent automation for Foundry/Fabric integration after infra + launcher.
	- Resolves Fabric workspace + Data Agent IDs
	- Ensures the governed `lh_gold_curated` analytical tables are selected and published in the Data Agent staging source
	- Synchronizes datasource instructions and few-shot examples from source control with read-after-write verification
	- Applies reversible lakehouse-only routing and semantic-source removal for deterministic IQ behavior
	- Creates/updates Foundry Fabric project connection
	- Creates/updates the indexed OneLake policy knowledge source, generated Search ingestion pipeline, and knowledge base
	- Creates/updates Foundry KB MCP connection
	- Creates/updates IQ and non-IQ comparison agents with versioned instructions
- `foundry_self_heal.py`: Retries only the Fabric connection and full orchestrator agent after the rest of the deployment is healthy.
- `create_foundry_support_bundle.py`: Creates a redacted ZIP with correlation IDs, status, resource metadata, and failure logs.
- `generate_live_iq_handoff.py`: Writes an allow-listed, environment-specific demo handoff under the ignored `handoffs/` directory.
- `semantic_model_health_canary.py`: Resolves `HealthcareDemoHLS`, records its owner, optionally performs an explicit takeover and framing refresh, and validates aggregate DAX counts.
- `functional_test_suite.py`: Certifies required Fabric artifacts, Direct Lake DAX, direct Data Agent MCP KPI answers, Search KB MCP, and Foundry IQ data/knowledge/hybrid behavior.

## Default Execution Path
- `scripts/azd/postprovision.py` runs `automate_foundry_remaining.py` when `AUTOMATE_FOUNDRY_REMAINING=true`.
- `FOUNDRY_FABRIC_MODE=fabric_iq` is the default for new environments. Use `legacy` only for comparison/regression evidence or `disabled` for KB-only deployment.
- Fabric IQ mode creates a `RemoteTool` connection to the published Data Agent MCP endpoint with delegated `UserEntraToken` authentication. Legacy mode retains the `CustomKeys` connection.
- `run_all.sh` seeds `FOUNDRY_AUTOMATION_ENFORCE_SUCCESS=true` so OneLake propagation and Foundry failures enter the retry path before certification.
- A redacted support bundle is generated automatically after exhausted Foundry retries unless `FOUNDRY_SUPPORT_BUNDLE_ON_FAILURE=false`.
- `FOUNDRY_CONNECTION_BICEP_FALLBACK=true` also tries the documented `accounts/projects/connections` Bicep child-resource route after REST failure.
- `FOUNDRY_CONNECTION_BICEP_TIMEOUT_SECONDS` bounds that fallback (default `300`).
- `FOUNDRY_CONNECTION_MAX_ATTEMPTS` and `FOUNDRY_CONNECTION_RETRY_INITIAL_DELAY_SECONDS` bound REST retries.
- `FOUNDRY_CONNECTION_API_VERSIONS` can override the provider API-version list for controlled experiments.
- `FABRIC_DATA_AGENT_LAKEHOUSE_NAME` selects the lakehouse source to repair (default `lh_gold_curated`).
- `FABRIC_DATA_AGENT_TABLES` is an optional comma-separated override for the analytical table selection.
- `FABRIC_DATA_AGENT_PUBLISH_TIMEOUT_SECONDS` bounds an asynchronous Data Agent staging publish (default `300`).
- `FABRIC_DATA_AGENT_ROUTING=lakehouse_primary` keeps KPI and detail prompts on validated NL2SQL; `restore` applies the saved pre-change instructions.
- `FABRIC_DATA_AGENT_SEMANTIC_SOURCE=remove` keeps the Data Agent structurally lakehouse-only; `restore` re-adds the snapshotted semantic source.
- `SEARCH_KNOWLEDGE_MODE=onelake` ingests `lh_gold_curated/Files/healthcare_knowledge/` through an `indexedOneLake` source and is the default. `local_index` is retained as a deterministic recovery fallback; `fabric_data_agent` remains experimental.
- `FOUNDRY_CHAT_DEPLOYMENT_NAME=gpt-5.4`, `FOUNDRY_CHAT_SKU_NAME=GlobalStandard`, and `FOUNDRY_CHAT_CAPACITY=100` provide the certified orchestration rate ceiling.
- `RUN_POSTDEPLOY_VALIDATION=true` runs Direct Lake and IQ/non-IQ functional certification after post-provisioning.
- `SEMANTIC_MODEL_TAKEOVER=true` transfers semantic-model ownership to the authenticated deployment operator before framing. This is the certified `run_all.sh` default; call the canary without `--take-over` for read-only diagnosis.
- `FUNCTIONAL_TEST_ENFORCE_SUCCESS=true` makes missing artifacts, failed DAX, MCP, Foundry calls, missing citations, approval/consent interruptions, or DAX/direct/IQ aggregate drift fail the run.
- After validation, `run_all.sh` always generates `handoffs/<environment>-live-iq-demo.md` and prints its absolute path. Handoffs are local artifacts and never belong in Git.

## Recovery and Diagnostics

Retry only the blocked connection and full agent after a platform repair:

```bash
python3 scripts/automation/foundry_self_heal.py --environment <azd-env>
```

Create a redacted support package:

```bash
python3 scripts/automation/create_foundry_support_bundle.py --environment <azd-env>
```

The legacy `CustomKeys` backend failure and validated Fabric IQ workaround are tracked in [GitHub issue #1](https://github.com/matthansen0/hls-iq-auto-harness/issues/1).

Run the Direct Lake canary without changing ownership:

```bash
python3 scripts/automation/semantic_model_health_canary.py \
	--workspace-name <fabric-workspace> \
	--dataset-name HealthcareDemoHLS \
	--refresh \
	--enforce-success
```

Run the IQ and direct-MCP functional suite:

```bash
set -a && eval "$(azd env get-values)" && set +a
python3 scripts/automation/functional_test_suite.py \
	--workspace-name "$FABRIC_WORKSPACE_NAME" \
	--project-endpoint "https://${HUB_NAME}.services.ai.azure.com/api/projects/${PROJECT_NAME}" \
	--search-service-name "$SEARCH_SERVICE_NAME" \
	--enforce-success
```

## Manual Fallback (Only If Automation Fails)
If tenant/region API behavior blocks specific calls, use manual fallback documented in:
- `docs/AZD_AUTOMATION_GUIDE.md`
- `fabric-main/FOUNDRY_IQ_SETUP_GUIDE.md`
