# HLS IQ Automation Harness Guidelines

## Scope

This repository automates a healthcare payer/provider analytics demo across Azure, Microsoft Fabric, Microsoft Foundry, Azure AI Search, and Power BI. The root repository owns orchestration and infrastructure. `fabric-main/` is a Git submodule that owns the Fabric workspace content, healthcare knowledge documents, Data Agent definitions, and orchestrator instructions.

Use this file as project-wide guidance. Link to the source-of-truth files below instead of duplicating their implementation details in new documentation.

## Deployment Contract

From the dev container, deployment remains three operator commands:

```bash
az login
azd auth login --use-device-code
./scripts/azd/run_all.sh
```

`run_all.sh` creates or selects the azd environment, provisions Azure resources, runs the Fabric launcher, configures Foundry and Search, repairs and validates Direct Lake, certifies the IQ/non-IQ paths, and writes a local demo handoff.

Useful options:

```bash
./scripts/azd/run_all.sh --env-name <name>
./scripts/azd/run_all.sh --env-name <name> --turbodeploy
```

- Standard deployment uses Fabric F64. Turbo deployment uses F256 during setup and scales down to F64.
- A full standard run is approximately 40 minutes; do not advertise the older 15-minute estimate.
- The generated handoff is `handoffs/<environment>-live-iq-demo.md`.
- `handoffs/`, `logs/`, `.azure/`, and `.azd/` are local/generated and must never be committed.
- The CLI must finish with `Your handoff file is here: <absolute-path>`.

## Certified Architecture

The default and certified architecture is:

1. Azure resources are provisioned by `infra/main.bicep`: Microsoft Fabric capacity, Microsoft Foundry account/project, and Basic Azure AI Search.
2. `scripts/azd/postprovision.py` creates the Fabric workspace, imports upstream `fabric-main/Healthcare_Launcher.ipynb` with a root-owned retry transformation for the launcher package install, runs it as `Healthcare_Launcher`, deploys models, and applies required RBAC.
3. The Fabric Data Agent `HealthcareHLSAgent` is data-only and lakehouse-only:
   - `FABRIC_DATA_AGENT_ROUTING=lakehouse_primary`
   - `FABRIC_DATA_AGENT_SEMANTIC_SOURCE=remove`
   - 13 selected analytical tables from `lh_gold_curated`
4. The `HealthcareDemoHLS` Direct Lake semantic model remains the Power BI and DAX baseline. It is healthy after ownership takeover and framing refresh, but is intentionally not attached to the Data Agent.
5. Fabric IQ connects Foundry to the published Data Agent MCP endpoint with a `RemoteTool`, delegated `UserEntraToken`, and the `fabric_iq_preview` tool.
6. The launcher uploads `fabric-main/healthcare_knowledge/**/*.md` to `lh_gold_curated/Files/healthcare_knowledge/`. Azure AI Search uses an `indexedOneLake` knowledge source to generate the data source, skillset, vector index, and indexer; the KB uses extractive output, the current chat model, `medium` reasoning, and source data for citations.
7. `HealthcareOrchestratorAgent2` combines governed Fabric data and cited policy retrieval.
8. `HealthcareOrchestratorNonIQ` is the policy-only comparison agent and must clearly decline live-data questions.
9. The default reasoning deployment is GPT-5.4 Global Standard with a 100K TPM rate ceiling. This is pay-per-token capacity, not reserved throughput.

The legacy `fabric_dataagent_preview` / `CustomKeys` project connection is supported only as a diagnostic comparison. It returns a Microsoft credential/account-RP HTTP 500 in tested regions. Do not make it the default or gate Fabric IQ deployment on it.

## Behavioral Invariants

- Every operational number in an orchestrator answer must come from a completed Fabric tool call.
- Policy recommendations must come from the Search knowledge base and include citations.
- Hybrid questions must use separate data and policy calls before synthesis.
- Patient-specific recommendations must perform a separate provider lookup before naming a provider.
- Patient lookups require disambiguation; do not query by name alone.
- Denial-rate baseline prompts mean all available data with no implicit date filter.
- Preserve lakehouse-only Data Agent routing unless the user explicitly requests an experiment. Do not restore the semantic-model source by default.
- Keep `require_approval=never` for the certified Fabric IQ tool so demos do not pause for approval.

## Source Of Truth

| Concern | Source |
|---|---|
| One-command orchestration and defaults | `scripts/azd/run_all.sh` |
| Azure/Foundry/Fabric bootstrap | `scripts/azd/postprovision.py` |
| Foundry IQ, Search, Data Agent synchronization | `scripts/automation/automate_foundry_remaining.py` |
| Search policy corpus | `fabric-main/healthcare_knowledge/**/*.md`, uploaded by the launcher to `lh_gold_curated/Files/healthcare_knowledge/` (synthetic runtime fixture; 26 documents) |
| Generated local handoff | `scripts/automation/generate_live_iq_handoff.py` |
| Live certification | `scripts/automation/functional_test_suite.py` |
| Direct Lake ownership/framing canary | `scripts/automation/semantic_model_health_canary.py` |
| Recovery unit tests | `tests/test_recovery_automation.py` |
| Fabric Data Agent behavior | Upstream Data Agent definition JSON normalized by `scripts/automation/automate_foundry_remaining.py` |
| Foundry orchestration and safety rules | `config/orchestrator_instructions.md` |
| Deployment guide | `docs/AZD_AUTOMATION_GUIDE.md` |
| Dev container | `.devcontainer/devcontainer.json` plus `scripts/azd/install_prereqs.sh` |

## Engineering Conventions

- Follow existing Python and Bash patterns; keep changes scoped to the owning automation layer.
- Use structured JSON parsing and REST/SDK clients. Do not parse API payloads with ad hoc string manipulation.
- Keep root and submodule changes separate. Never revert unrelated user changes in either repository.
- Never hardcode tokens, keys, passwords, connection strings, user UPNs, live tenant IDs, subscription IDs, workspace IDs, or environment-specific portal URLs in tracked files.
- Allow-list metadata written to generated files and support bundles. Never serialize the full environment.
- Keep generated validation results, support bundles, handoffs, transcripts, terminal output, and exported live definitions out of Git.
- Keep one canonical devcontainer manifest at `.devcontainer/devcontainer.json`; do not recreate the removed `config/devcontainer.json` duplicate.
- Treat `fabric-main/healthcare_knowledge/` as environment-neutral runtime test/demo data. Do not add PHI, confidential organizational policy, or tenant-specific content.
- Retain diagnostics only when normal automation or a documented recovery path uses them. Delete one-off probes after their evidence is captured in an issue.
- Do not commit directly or update the submodule pointer unless the user explicitly requests a commit.

## Validation

Run the focused check immediately after an edit, then finish with:

```bash
python3 -m unittest -v tests/test_recovery_automation.py
python3 -m py_compile \
  scripts/automation/automate_foundry_remaining.py \
  scripts/automation/foundry_self_heal.py \
  scripts/automation/functional_test_suite.py \
  scripts/automation/generate_live_iq_handoff.py \
  scripts/automation/semantic_model_health_canary.py \
  scripts/azd/postprovision.py
bash -n scripts/azd/run_all.sh scripts/azd/cleanup.sh
az bicep build --file infra/main.bicep --stdout >/dev/null
git diff --check
git -C fabric-main diff --check
```

For a deployed environment, run `scripts/automation/functional_test_suite.py` with `--enforce-success`. Certification requires all direct MCP, Search KB, IQ, non-IQ, citation, approval/consent, and DAX consistency checks to pass.

## Operations And Cost

- Keep a working demo environment unless the user requests teardown.
- Fabric F64 is the primary idle-cost resource. Pause it between demo sessions and resume it before certification; use the environment-specific commands in the generated handoff.
- Do not pause, scale, delete, or recreate live resources during a documentation-only task.
- Portal completion can precede terminal completion. Long operations emit heartbeat output; wait for terminal success and certification.

## Known Platform Limitation

Track the legacy Foundry `CustomKeys` HTTP 500 in GitHub rather than accumulating local research transcripts or live-output documents. The Fabric IQ `RemoteTool` path is the implemented workaround and the production demo path.
