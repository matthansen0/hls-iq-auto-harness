# AZD End-to-End Automation Guide

This repository includes an `azd` scaffold so one command can provision the Azure and Fabric foundation and run post-provision bootstrap steps.

## What `azd up` Automates

1. Creates Azure AI Foundry hub (AI Services account)
2. Creates Azure AI Search (Basic, MSI enabled)
3. Creates Fabric capacity (`Microsoft.Fabric/capacities`)
4. Runs post-provision bootstrap script to:
   - enable AI Hub project-management capability (`allowProjectManagement`)
   - create Foundry Project under the Hub
   - create or find Fabric workspace
   - attempt workspace-to-capacity assignment
   - attempt to run `Healthcare_Launcher` notebook if it exists in the workspace

> Note: Foundry Project ARM creation is region-dependent. In regions where
> `Microsoft.CognitiveServices/accounts/projects` is not available (for example,
> some Sweden Central tenants), bootstrap now detects this and skips ARM project
> creation with clear manual fallback instructions instead of failing the run.

## Certification Contract

A successful deployment satisfies all of these checks:

1. `azd up --no-prompt` completed non-interactively
2. Fabric workspace was created and assigned to capacity
3. `Healthcare_Launcher` was auto-imported and executed successfully
4. The launcher reaches a verified successful terminal state
5. Foundry project creation succeeded after the hub custom subdomain prerequisite was met
6. Direct Lake ownership/framing and DAX counts passed
7. The Data Agent was published with 13 lakehouse tables, synchronized instructions/few-shots, and lakehouse-only routing
8. The indexed OneLake source targeted `lh_gold_curated/Files/healthcare_knowledge/` and generated its data source, skillset, vector index, and daily indexer
9. A clean indexer run processes the 26 source documents with no failures and produces a nonempty vector index using `text-embedding-ada-002` at 120K TPM
10. The Search KB persisted GPT-5.4 with `medium` retrieval reasoning; Fabric IQ, KB MCP, IQ/non-IQ agents, and all project connections were created successfully
11. The final functional suite passed all 12 checks, including cited KB retrieval and DAX/direct/IQ aggregate consistency

Legacy comparison behavior:

- The legacy `fabric_dataagent_preview` connection can return backend HTTP `500` from the Foundry credential/account-RP service even when workspace and artifact IDs are valid
- Keep this route diagnostic-only; it must not gate deployment
- Fabric IQ bypasses this path through a `RemoteTool` connection to the published Data Agent MCP endpoint

The legacy backend failure and Fabric IQ workaround are tracked in [GitHub issue #1](https://github.com/matthansen0/hls-iq-auto-harness/issues/1).

## One-Command Run (Recommended)

After login, run:

```bash
bash scripts/azd/run_all.sh
```

On every completed run, the CLI prints:

```text
[done] Your handoff file is here: <absolute-path>/handoffs/<environment>-live-iq-demo.md
```

The handoff contains environment-specific links, demo prompts, validation results, cost controls, and rollback guidance. `handoffs/` is ignored by Git.

What `--turbodeploy` does (optional):

1. Provisions Fabric capacity at `F256` for faster deployment/setup
2. Starts `Healthcare_Launcher` notebook job (if notebook exists in workspace)
3. Polls status with live `[STATUS]` output every 30 seconds
4. Auto-scales capacity down to `F64` after notebook reaches terminal state

Turbo mode waits for the launcher to reach a terminal state before scaling the capacity back to F64.

Standard mode (lower cost):

```bash
bash scripts/azd/run_all.sh
```

Recommended for budget control:

- Keep `TURBO_DEPLOY=false`
- Keep `FABRIC_CAPACITY_SKU=F64`

## Prerequisites (Before run)

1. Install and sign in
   - `az login`
   - `azd auth login --use-device-code`
2. Access prerequisites
   - PIM elevation completed (if your tenant requires it)
   - Fabric Admin / Capacity Admin rights to create capacity and workspaces
   - Subscription `Owner` or equivalent RBAC for Azure resources + role assignments
3. Register Azure providers
   - `az provider register --namespace Microsoft.CognitiveServices`
   - `az provider register --namespace Microsoft.Search`
   - `az provider register --namespace Microsoft.Fabric`
4. Configure env values
   - `scripts/azd/run_all.sh` now auto-seeds the required names and region when missing.
   - It also syncs key infra parameters into `azd env config` so `azd up --no-prompt` can run cleanly.
   - You can still override values directly with `azd env set ...` before running.

Additional prerequisites:

5. AI Hub custom subdomain
   - Foundry project ARM create fails unless the AI hub has `properties.customSubDomainName` set
   - The template now sets this to `hubName`
6. Search auth fallback
   - AAD calls to Search knowledge-source APIs may return `403`
   - Automation now falls back to Search admin key for knowledge source/base creation
7. OneLake external application access
   - The Fabric tenant must allow OneLake data access from applications outside Fabric.
   - The launcher uploads policy Markdown to `lh_gold_curated/Files/healthcare_knowledge/` before Search ingestion begins.
   - The Search managed identity receives Fabric workspace Contributor access automatically.

## If You Prefer Raw `azd up`

## Run

```bash
azd env new healthcare-demo
azd env set HUB_NAME "hls-healthcaredemo-<suffix>"
azd env set SEARCH_SERVICE_NAME "healthcarefoundryais<suffix>"
azd env set FABRIC_CAPACITY_NAME "healthcarefabriccap<suffix>"
azd env set LOCATION "swedencentral"
azd env set FABRIC_WORKSPACE_NAME "HealthcareDemo-WS"
azd up
```

If you use `bash scripts/azd/run_all.sh`, these values are created automatically when absent and then reused from `azd env`.

## Troubleshooting: Fabric Capacity Admin Error

If you see an error like:

```
"At least one capacity administrator is required"
```

Or if deployment fails with a message about missing `FABRIC_CAPACITY_ADMINS`, manually set both the environment and config values:

```bash
azd env set FABRIC_CAPACITY_ADMINS '["<REPLACE_WITH_YOUR_EMAIL>"]'
azd env config set infra.parameters.fabricCapacityAdmins '["<REPLACE_WITH_YOUR_EMAIL>"]'
```

Replace `<REPLACE_WITH_YOUR_EMAIL>` with your Azure account UPN if different. Then rerun:

```bash
bash scripts/azd/run_all.sh --turbodeploy
```

This ensures the Fabric capacity deployment has a valid admin and will succeed.

## Post-Run Steps

No separate Foundry notebook is required. A successful `run_all.sh` invocation already:

1. Publishes the 13 selected analytical lakehouse tables and lakehouse-only Data Agent routing.
2. Repairs Direct Lake ownership/framing and validates the DAX baseline.
3. Creates the Fabric IQ connection and the indexed OneLake Search source, generated indexer pipeline, knowledge base, and KB connection.
4. Creates the IQ and non-IQ comparison agents.
5. Runs data-only, policy-only, hybrid, citation, approval/consent, and cross-path consistency checks.
6. Writes `handoffs/<environment>-live-iq-demo.md` and prints its absolute path.

Open the generated handoff for environment-specific links, demo prompts, certification results, cost controls, and rollback guidance. The file is local and excluded from Git.

If the Fabric launcher cannot be imported automatically, `run_all.sh` prints the one manual import checkpoint and resumes post-provisioning afterward. For a failed Foundry retry sequence, use the redacted support bundle under `logs/support-bundles/`.

The legacy `fabric_dataagent_preview` / `CustomKeys` connection can still return a credential/account-RP HTTP 500. This does not block the default Fabric IQ `RemoteTool` path; track backend status in [GitHub issue #1](https://github.com/matthansen0/hls-iq-auto-harness/issues/1) rather than rerunning regional probes.

## Workspace Cleanup

To delete an old Fabric workspace, use the explicit-name cleanup helper:

```bash
python3 scripts/azd/delete_fabric_workspace.py --workspace-name "HealthcareDemo-WS"
```

The script now requires an explicit workspace name to avoid deleting the active workspace by mistake.

For full cleanup of Azure resources, Fabric workspace, and the local `azd` env:

```bash
bash scripts/azd/cleanup.sh --env-name <your-env-name> --remove-env
```

Cleanup verifies that:

- Azure resource group deleted by `azd down`
- AI hub purge completed
- Fabric workspace deleted via API
- Local `azd` environment removed

## Status Output During Long Runs

`scripts/azd/postprovision.py` prints live progress lines like:

- `[STATUS] +300s | notebook job status: running`
- `[STATUS] +7200s | notebook job status: completed`

Polling interval and timeout are configurable via azd env vars:

- `NOTEBOOK_RUN_POLL_SECONDS` (default `30`)
- `NOTEBOOK_RUN_MAX_MINUTES` (default `240`)
- `NOTEBOOK_RUN_MAX_ATTEMPTS` (default `2`)
- `NOTEBOOK_RETRY_DELAY_SECONDS` (default `20`)

Notebook job tracking is resilient to tenant differences:

- If run-start response omits job id in JSON, bootstrap now extracts it from
   the `Location` header.
- If both are missing, bootstrap queries job instances and tracks the most
   recent `RunNotebook` job automatically.
- Polling now honors `Retry-After` headers from Job Scheduler responses when
   provided, and falls back to `NOTEBOOK_RUN_POLL_SECONDS` otherwise.

Deterministic notebook run controls (recommended defaults):

- `NOTEBOOK_RUN_REQUIRE_JOB_ID` (default `true`): fail the run when Fabric
   accepts notebook submission but no job id can be resolved. This avoids
   guessing whether execution completed.
- `NOTEBOOK_RUN_ENFORCE_SUCCESS` (default `true`): return non-zero when the
   launcher run doesn't reach a verified success state after retries.
- `NOTEBOOK_RUN_EXPECTED_EXIT_VALUE` (default empty): optional exact match check
   against notebook `exitValue` for semantic success validation.

Failure tracking:

- Post-provision logic now appends structured failure events to
   `logs/postprovision_failure_events.jsonl`.
- Events include stage, stable error code, message, timestamp, and bounded
   context such as request, activity, or job identifiers.

## Region Alignment for Foundry Models

For Foundry orchestrator work, keep all of these in the same Azure region:

1. AI Services Hub account
2. Foundry Project
3. Chat model deployment
4. Embedding model deployment

After `azd up`, use this to confirm the active region:

```bash
azd env get-value LOCATION
```

Then deploy both chat and embedding models into that same region/hub to avoid
cross-region availability and connection issues.

## Automation Scope

- In most tenants, provisioning + workspace setup can be fully automated.
- Notebook execution is automated (import + run + status polling).
- Foundry model deployments are automated (`gpt-5.4` Global Standard and `text-embedding-ada-002` by default).
- Azure RBAC is automated for Search + OpenAI roles (user + Search MSI where resolvable).
- Fabric workspace Contributor grants are automated for Search MSI + Foundry Hub MSI.
- The OneLake policy source, generated data source/skillset/vector index/indexer, ingestion polling, and nonempty-index check are automated.
- Fabric IQ connection and orchestrator upsert are automated through the published Data Agent MCP endpoint.
- The legacy `fabric_dataagent_preview` / `CustomKeys` connection may still fail with backend `500`, but it is diagnostic-only and does not block deployment.

Fabric Data Agent discovery, Search knowledge source/base creation, and KB MCP connection creation are now automated successfully in the validated path.

## Required Fabric Tenant Settings (Manual)

These tenant-level settings must be enabled by a Fabric admin for the full demo:

1. Service principals can use Fabric APIs
2. Users can create Graph
3. Users can create Ontology
4. Enable Operations Agent
5. Users can access OneLake data with apps external to Fabric

Also ensure Fabric admin/capacity permissions are active for the deployment identity before running automation.

---

## Automated Steps
The following steps are now fully automated by the deployment scripts:

- Azure resource deployment (workspaces, capacity, AI Search, etc.)
- Foundry project creation
- Model deployments
- RBAC assignments
- Fabric workspace creation and assignment
- Notebook import and execution
- Data Agent table selection, metadata, few-shot, routing, and publication synchronization
- Indexed OneLake policy source, generated Search ingestion resources, knowledge base, and KB MCP connection
- Fabric IQ and non-IQ agent creation/update
- Direct Lake, direct MCP, Search MCP, IQ/non-IQ, citation, and consistency certification
- Environment-specific ignored demo handoff generation

## Manual Steps Remaining
No Foundry portal steps remain on the certified path. The operator authenticates, runs `run_all.sh`, waits for terminal completion, and uses the generated handoff.

The only conditional checkpoint is a one-time manual Fabric notebook import if the tenant does not permit automated import. `run_all.sh` detects that specific condition, prints the exact import instructions, and resumes automation afterward.

---
