# HLS IQ Automation Harness

Automation orchestration layer for the [Fabric Payer-Provider Healthcare Demo](https://github.com/rasgiza/Fabric-Payer-Provider-HealthCare-Demo), built to streamline deployment, configuration, and operational excellence for the healthcare intelligence solution. The bundled submodule points directly to that upstream repository and is pinned to a tested upstream commit. Harness-specific deployment behavior remains in this root repository.

## Overview

This repository provides:
- **Automated deployment pipelines** via Azure Developer CLI (AzD)
- **Orchestrator agent scaffolding** for Healthcare IQ
- **Notebook launcher automation** and dev container setup
- **Post-provisioning configuration** and verification scripts

The **Fabric main demo** is pulled in as a submodule (`fabric-main/`), allowing this automation harness to orchestrate and extend its capabilities without duplicating content.

### Why The Healthcare Knowledge Corpus Is Tracked

`fabric-main/healthcare_knowledge/` is runtime test/demo data, not deployment implementation. The 26 Markdown files total about 273 KB, contain no real patient data, and let every environment build the same governed policy source without downloading tenant-specific or mutable external content. The launcher uploads this corpus to `lh_gold_curated/Files/healthcare_knowledge/`, and Azure AI Search ingests that OneLake folder.

The corpus is required for the certified comparison:

- An indexed OneLake knowledge source generates the Search data source, skillset, vector index, and indexer.
- The IQ orchestrator uses the generated chunks for completed, cited policy MCP calls and hybrid data-plus-policy answers.
- The non-IQ agent uses the same corpus to demonstrate policy retrieval while correctly declining live-data questions.
- Four of the 12 live certification checks directly require the Search corpus; deployment also fails closed when the configured corpus is absent.

Removing it would intentionally change this project into a data-only Fabric demo and eliminate the IQ/non-IQ policy comparison. Keep policy documents environment-neutral and synthetic; never add organization-confidential guidance or PHI.


## Quick Start

### Prerequisites
- **Docker** (for dev container)
- **VS Code** with Dev Containers extension

#### Enable following Fabric Tenant settings: 
- Service Principals can use Fabric APIs
- User can create Graph
- User can create Ontology
- Enable Operations Agent
- Users can access OneLake data with apps external to Fabric


### Deploy in 4 Steps

1. **Clone the repository:**
   ```bash
   git clone --recursive https://github.com/matthansen0/hls-iq-auto-harness.git
   cd hls-iq-auto-harness
   ```

2. **Open in dev container** (VS Code):
   - Open the folder in VS Code
   - Click "Reopen in Container" when prompted
   - (Or use Command Palette: `Dev Containers: Reopen in Container`)
   - Wait for container to build and start (~2-3 min)

3. **Authenticate to Azure:**
   ```bash
   az login --use-device-code
   azd auth login --use-device-code
   ```

4. **Deploy everything:**
   ```bash
   bash scripts/azd/run_all.sh
   ```

That's it. The deployment runs the Foundry/Fabric integration and certification steps automatically. On completion, the CLI prints the path to an environment-specific demo handoff under `handoffs/`; that directory is intentionally excluded from Git.

See [AZD_AUTOMATION_GUIDE.md](docs/AZD_AUTOMATION_GUIDE.md) for troubleshooting and detailed information.

4. **Cleanup:**
   ```bash
   bash scripts/azd/cleanup.sh --env-name <your-env-name> --remove-env
   ```

When you're all done, you can pause the Fabric capacity, or if you want you can use the cleanup script to fully cleanup the Azure and Fabric environments. 

### Documentation (`docs/`)
- **AZD_AUTOMATION_GUIDE.md** — Step-by-step deployment and automation documentation

## Workflow

### Standard Deployment

1. Open in dev container: `Dev Containers: Reopen in Container`
2. Login: `az login --use-device-code && azd auth login --use-device-code`
3. Run: `./scripts/azd/run_all.sh`
4. Monitor the deployment logs

## Foundry Completion Automation

`scripts/azd/postprovision.py` now invokes `scripts/automation/automate_foundry_remaining.py` by default.

This automates:
- Fabric Data Agent discovery by name in the deployed workspace
- Data Agent table selection, lakehouse-only routing, datasource metadata, and few-shot synchronization
- Foundry Fabric IQ `RemoteTool` connection to the published Data Agent MCP endpoint
- Azure AI Search `indexedOneLake` policy knowledge source, generated ingestion pipeline, and readiness validation
- Azure AI Search extractive knowledge base creation/update
- Foundry project RemoteTool connection to the KB MCP endpoint
- IQ orchestrator create/update with:
   - `fabric_iq_preview`
  - `mcp` (`knowledge_base_retrieve`)
  - optional `web_search_preview`
- Non-IQ policy-only comparison agent create/update
- Direct Lake, direct MCP, KB MCP, IQ/non-IQ, citation, and cross-path consistency tests

## Certification

The deployment fails closed unless its Direct Lake, Fabric Data Agent, OneLake Search, knowledge-base MCP, IQ, and non-IQ checks pass. Certification covers:

- Non-interactive Azure and Fabric provisioning
- Launcher import and verified execution
- Direct Lake ownership, framing refresh, and DAX counts
- Governed direct-MCP KPI queries
- Indexed OneLake ingestion with a nonempty generated vector index
- Data-only, policy-only, and hybrid orchestrator behavior
- Policy citation annotations and approval/consent checks
- DAX, direct-MCP, and Fabric IQ denial-rate consistency
- Explicit live-data refusal by the non-IQ comparison agent

A standard F64 deployment is approximately 40 minutes. Each run writes its environment-specific resources, prompts, URLs, and certification results to the ignored `handoffs/<environment>-live-iq-demo.md` file.

### Legacy Compatibility

- The legacy `fabric_dataagent_preview` / `CustomKeys` connection still returns the Foundry credential/account-RP HTTP 500.
- This no longer blocks the demo because Fabric IQ uses the published MCP endpoint through a `RemoteTool` connection.
- Tracking: [Issue #1](https://github.com/matthansen0/hls-iq-auto-harness/issues/1)

### Environment knobs (optional)

- `AUTOMATE_FOUNDRY_REMAINING` (default: `true`)
- `FOUNDRY_AUTOMATION_ENFORCE_SUCCESS` (seeded as `true` by `run_all.sh`)
- `FOUNDRY_SUPPORT_BUNDLE_ON_FAILURE` (default: `true`)
- `FOUNDRY_SUPPORT_BUNDLE_DIR` (default: `logs/support-bundles`)
- `FOUNDRY_CONNECTION_BICEP_FALLBACK` (default: `true`)
- `FOUNDRY_CONNECTION_BICEP_TIMEOUT_SECONDS` (default: `300`)
- `FOUNDRY_CONNECTION_MAX_ATTEMPTS` (default: `2` per API version)
- `FOUNDRY_CONNECTION_RETRY_INITIAL_DELAY_SECONDS` (default: `5`)
- `FOUNDRY_CONNECTION_API_VERSIONS` (optional comma-separated override; defaults to current and supported preview versions)
- `FOUNDRY_FABRIC_MODE` (`fabric_iq` by default; `legacy` and `disabled` are supported)
- `FOUNDRY_FABRIC_IQ_CONNECTION_NAME` (default: `healthcare-fabric-iq`)
- `FOUNDRY_FABRIC_IQ_MCP_ENDPOINT` (optional public or workspace-private Data Agent MCP endpoint override)
- `FOUNDRY_CHAT_DEPLOYMENT_NAME` (default: `gpt-5.4`)
- `FOUNDRY_CHAT_MODEL_VERSION` (default: `2026-03-05`)
- `FOUNDRY_CHAT_SKU_NAME` (default: `GlobalStandard`)
- `FOUNDRY_CHAT_CAPACITY` (default: `100`; pay-per-token TPM ceiling)
- `FOUNDRY_MODEL_RESOURCE_URI` (default: `https://<hub>.openai.azure.com`)
- `FOUNDRY_EMBEDDING_DEPLOYMENT_NAME` (default: `text-embedding-ada-002`)
- `FOUNDRY_EMBEDDING_MODEL_NAME` (default: `text-embedding-ada-002`)
- `FOUNDRY_EMBEDDING_CAPACITY` (default: `120`; 120K TPM for parallel OneLake ingestion)
- `FOUNDRY_SELF_HEAL_MAX_ATTEMPTS` (default: `3`)
- `FOUNDRY_SELF_HEAL_RETRY_DELAY_SECONDS` (default: `60`)
- `FABRIC_DATA_AGENT_NAME` (default: `HealthcareHLSAgent`)
- `FABRIC_DATA_AGENT_LAKEHOUSE_NAME` (default: `lh_gold_curated`)
- `FABRIC_DATA_AGENT_TABLES` (optional comma-separated override; defaults to the 13 documented analytical tables)
- `FABRIC_DATA_AGENT_PUBLISH_TIMEOUT_SECONDS` (default: `300`)
- `FABRIC_DATA_AGENT_ROUTING` (`lakehouse_primary` by default; `preserve` and `restore` supported)
- `FABRIC_DATA_AGENT_SEMANTIC_SOURCE` (`remove` by default; `preserve` and `restore` supported)
- `FABRIC_DATA_AGENT_DEFINITION_DIRECTORY` (repository draft definition used for metadata/few-shot sync)
- `FOUNDRY_FABRIC_CONNECTION_NAME` (default: `HealthcareHLSAgent`)
- `SEARCH_KNOWLEDGE_MODE` (`onelake` by default; `local_index` is the deterministic recovery fallback and `fabric_data_agent` is experimental)
- `SEARCH_KNOWLEDGE_SOURCE_NAME` (default: `healthcare-policy-ks`)
- `SEARCH_KNOWLEDGE_BASE_NAME` (default: `healthcareknowledgebase`)
- `SEARCH_KNOWLEDGE_ONELAKE_TARGET_PATH` (default: `healthcare_knowledge`, relative to the lakehouse `Files` root)
- `SEARCH_KNOWLEDGE_INGESTION_INTERVAL` (default: `P1D`)
- `SEARCH_KNOWLEDGE_INGESTION_TIMEOUT_SECONDS` (default: `900`)
- `SEARCH_KNOWLEDGE_INGESTION_POLL_SECONDS` (default: `15`)
- `SEARCH_KNOWLEDGE_RETRIEVAL_REASONING_EFFORT` (default: `medium`)
- `SEARCH_KNOWLEDGE_INDEX_NAME` and `SEARCH_KNOWLEDGE_DIRECTORY` configure the explicit `local_index` fallback
- `FOUNDRY_KB_CONNECTION_NAME` (default: `healthcare-kb-connection`)
- `FOUNDRY_ORCHESTRATOR_AGENT_NAME` (default: `HealthcareOrchestratorAgent2`)
- `FOUNDRY_INCLUDE_WEB_SEARCH` (default: `true`)
- `FOUNDRY_ORCHESTRATOR_INSTRUCTIONS_FILE` (default: `config/orchestrator_instructions.md`)
- `RUN_POSTDEPLOY_VALIDATION` (default: `true` in `run_all.sh`)
- `SEMANTIC_MODEL_TAKEOVER` (default: `true` in `run_all.sh`; makes the deployment operator the model owner before framing)
- `SEMANTIC_MODEL_REFRESH` (default: `true`)
- `FUNCTIONAL_TEST_ENFORCE_SUCCESS` (default: `true`)
- `FUNCTIONAL_TEST_TIMEOUT_SECONDS` (default: `180`)

### Recovery Automation

When the Fabric project connection is the only blocked step, retry the narrow path without rerunning infrastructure or the launcher:

```bash
python3 scripts/automation/foundry_self_heal.py --environment <azd-env>
```

After a failed run, the post-provision hook creates a redacted support bundle under `logs/support-bundles/`. It includes correlation IDs and resource metadata but excludes bearer tokens and secret-like values.

## Manual Fallback Steps (In Order)

If automation reports warnings, use this ordered checklist.

1. Verify Fabric tenant settings are enabled:
   - Service principals can use Fabric APIs
   - Users can create Graph
   - Users can create Ontology
   - Enable Operations Agent
   - Users can access OneLake data with apps external to Fabric
   - Copilot/Azure OpenAI tenant switches required for Data Agent usage

2. Confirm Foundry project exists in the same tenant/region pairing you plan to use.
   - Foundry project creation requires the hub `customSubDomainName`; the infrastructure template sets it to the hub name.
   - If project creation fails with `Account must set CustomSubDomainName before creating projects.`, update the hub first and rerun postprovision.

3. Confirm Fabric Data Agent is present and published.
   - Open `HealthcareHLSAgent` in Fabric.
   - Validate data sources and instructions.
   - Publish, then capture `workspace_id` and `artifact_id` from URL path `/groups/<workspace_id>/aiskills/<artifact_id>`.
   - Verify GET returns non-empty `properties.publishedDescription`, MCP `initialize` and `tools/list` return HTTP 200, and a bounded data-only query completes before classifying runtime health.
   - The certified demo keeps the Data Agent lakehouse-only and synchronizes 13 selected tables, source instructions, and few-shots from source control.
   - The semantic model remains active for the Power BI report but is removed from Data Agent routing to prevent source-selection drift and long tool calls.

4. Create or repair Foundry Fabric connection.
   - Preferred: Fabric IQ `RemoteTool`, `UserEntraToken`, Data Agent MCP target, Power BI audience.
   - Keep `require_approval=never` for the read-only demo tool.
   - The legacy `CustomKeys` route is comparison evidence only and remains platform-blocked.

5. Create or repair Search knowledge artifacts.
   - Ensure Search uses `aadOrApiKey` so managed-identity and operator bearer tokens work.
   - Ensure the Search managed identity is a Contributor in the Fabric workspace.
   - Ensure the Markdown corpus exists under `lh_gold_curated/Files/healthcare_knowledge/`.
   - Ensure the knowledge source is `indexedOneLake`, ingestion completed, and its generated index is nonempty.
   - Ensure the KB references that source with extractive output, the current chat model, and `medium` reasoning. `includeReferenceSourceData` is a retrieve-request option, not a persisted KB source-reference property.
   - If AAD calls to Search return `401/403`, use Search admin-key fallback and rerun postprovision.
   - Use `SEARCH_KNOWLEDGE_MODE=local_index` only as an explicit recovery fallback.

6. Create or repair Foundry KB MCP project connection.
   - Category: `RemoteTool`
   - Auth type: `ProjectManagedIdentity`
   - Target: `https://<search>.search.windows.net/knowledgebases/<kb>/mcp?api-version=2026-05-01-preview`
   - Audience: `https://search.azure.com/`

7. Recreate orchestrator agent version.
   - Ensure instructions come from `config/orchestrator_instructions.md`.
   - Ensure tools include `fabric_iq_preview` and MCP `knowledge_base_retrieve`.

8. Validate end-to-end behavior in Foundry playground.
   - Data-only query
   - Knowledge-only query
   - Hybrid query (must decompose into separate calls)

9. If user-identity access issues remain, verify permissions.
   - Foundry uses identity passthrough for Fabric Data Agent runtime calls.
   - Ensure end users have access to Data Agent and underlying sources.

10. Rerun automation after any manual repair:
   ```bash
   set -a && eval "$(azd env get-values)" && set +a
   python3 scripts/azd/postprovision.py
   ```


## Documentation

- [AZD Automation Guide](docs/AZD_AUTOMATION_GUIDE.md) — Comprehensive deployment details
- Generated `handoffs/<environment>-live-iq-demo.md` — Local environment URLs, prompts, validation, and operations
- [Contributing](CONTRIBUTING.md) — Development setup and guidelines

## Support & Troubleshooting

See [AZD_AUTOMATION_GUIDE.md](docs/AZD_AUTOMATION_GUIDE.md#troubleshooting) for common issues and solutions.

## License

The automation harness is licensed under [MIT](LICENSE). The `fabric-main/` submodule is a separate upstream project; review that repository for its current licensing terms before redistribution.

---

**Built for**: Demonstrating healthcare data intelligence with Microsoft Fabric and Azure AI Services
