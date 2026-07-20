# HLS IQ Automation Harness

One-command deployment and certification for the [Fabric Payer-Provider Healthcare Demo](https://github.com/rasgiza/Fabric-Payer-Provider-HealthCare-Demo). The upstream Fabric solution is pinned as the `fabric-main/` submodule; this root repository contains only infrastructure, orchestration, validation, and operator tooling.

## Overview

- Provisions Azure and Microsoft Fabric resources with Azure Developer CLI (`azd`).
- Imports and runs the upstream Fabric launcher.
- Configures Microsoft Foundry, Fabric IQ, Azure AI Search, and the orchestrator agents.
- Certifies the deployed environment and generates a local demo handoff.

## Quick Start

Use Docker and VS Code with the Dev Containers extension. Complete the [deployment prerequisites](docs/AZD_AUTOMATION_GUIDE.md#prerequisites-before-run) and [required Fabric tenant settings](docs/AZD_AUTOMATION_GUIDE.md#required-fabric-tenant-settings-manual), then clone and open the repository in its dev container:

```bash
git clone --recursive https://github.com/matthansen0/hls-iq-auto-harness.git
cd hls-iq-auto-harness
```

From the dev container, deployment is three commands:

```bash
az login
azd auth login --use-device-code
./scripts/azd/run_all.sh
```

A standard F64 deployment takes approximately 40 minutes. The final CLI message gives the absolute path to the ignored, environment-specific handoff under `handoffs/`.

To remove an environment later:

```bash
./scripts/azd/cleanup.sh --env-name <environment> --remove-env
```

## Automation Flow

### Fabric

The harness provisions Fabric capacity, creates the workspace, imports and runs upstream `Healthcare_Launcher.ipynb`, and verifies launcher completion. Fabric workspace content remains owned by the pinned upstream submodule.

### Foundry

After the upstream Fabric launcher completes, `scripts/azd/postprovision.py` runs the root-owned Foundry automation:

1. Creates the Foundry project, deploys the chat and embedding models, and applies required RBAC.
2. Resolves and publishes the upstream Fabric Data Agent with the certified lakehouse-only table selection, instructions, and few-shot examples.
3. Connects Foundry to the published Data Agent MCP endpoint through a Fabric IQ `RemoteTool` using delegated `UserEntraToken` authentication.
4. Creates the Azure AI Search `indexedOneLake` source, generated ingestion pipeline, and extractive knowledge base, then waits for ingestion readiness.
5. Creates the knowledge-base MCP project connection and deploys the IQ and non-IQ agent versions with source-controlled instructions.
6. Runs Direct Lake, MCP, citation, hybrid, approval, and cross-path consistency checks, then writes the environment-specific demo handoff.

### Certification

Deployment fails closed unless Direct Lake, Fabric Data Agent MCP, Search knowledge-base MCP, IQ/non-IQ behavior, citations, approvals, and cross-path consistency checks pass. The complete contract and recovery guidance live in the [AZD Automation Guide](docs/AZD_AUTOMATION_GUIDE.md#certification-contract).

## Documentation

- [AZD Automation Guide](docs/AZD_AUTOMATION_GUIDE.md): prerequisites, deployment options, certification, cleanup, and troubleshooting.
- [Automation Scripts](scripts/automation/README.md): configuration switches, recovery commands, diagnostics, and manual fallbacks.
- [Upstream Fabric Demo](fabric-main/README.md): Fabric solution content, architecture, and component usage.
- [Contributing](CONTRIBUTING.md): development setup, validation, and submodule workflow.
- [Known legacy connection issue](https://github.com/matthansen0/hls-iq-auto-harness/issues/1): status of the diagnostic `CustomKeys` path.

Each successful deployment also produces `handoffs/<environment>-live-iq-demo.md` with environment-specific URLs, prompts, validation results, cost controls, and rollback commands. Generated handoffs are local and ignored by Git.

## License

The automation harness is licensed under [MIT](LICENSE). The `fabric-main/` submodule is a separate upstream project; review its current licensing terms before redistribution.
