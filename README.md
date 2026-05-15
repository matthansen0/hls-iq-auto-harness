# HLS IQ Automation Harness

Automation orchestration layer for the **Fabric Payer-Provider Healthcare Demo**, built to streamline deployment, configuration, and operational excellence for the healthcare intelligence solution.

## Overview

This repository provides:
- **Automated deployment pipelines** via Azure Developer CLI (AzD)
- **Orchestrator agent scaffolding** for Healthcare IQ
- **Notebook launcher automation** and dev container setup
- **Post-provisioning configuration** and verification scripts

The **Fabric main demo** is pulled in as a submodule (`fabric-main/`), allowing this automation harness to orchestrate and extend its capabilities without duplicating content.

## Repository Structure

```
.
├── fabric-main/                    # Main demo repo (git submodule)
│   └── [All content from rasgiza/Fabric-Payer-Provider-HealthCare-Demo]
│
├── scripts/
│   └── automation/                 # Orchestration & automation scripts
│       ├── create_orchestrator_agent.py
│       ├── test_orchestrator_agent.py
│       └── verify_and_reset_indexer.sh
│
├── config/
│   ├── azure.yaml                  # AzD environment configuration
│   └── devcontainer.json           # Dev container setup
│
├── docs/
│   └── AZD_AUTOMATION_GUIDE.md     # Complete automation walkthrough
│
└── README.md                        # This file
```

## Quick Start

### Prerequisites
- **Docker** (for dev container)
- **VS Code** with Dev Containers extension

### Deploy in 4 Steps

1. **Clone the repository:**
   ```bash
   git clone https://github.com/matthansen0/hls-iq-auto-harness.git
   cd hls-iq-auto-harness
   ```

2. **Open in dev container** (VS Code):
   - Open the folder in VS Code
   - Click "Reopen in Container" when prompted
   - (Or use Command Palette: `Dev Containers: Reopen in Container`)
   - Wait for container to build and start (~2-3 min)

3. **Authenticate to Azure:**
   ```bash
   az login
   azd auth login
   ```

4. **Deploy everything:**
   ```bash
   ./scripts/azd/run_all.sh
   ```

That's it! The deployment is fully automated. See [AZD_AUTOMATION_GUIDE.md](docs/AZD_AUTOMATION_GUIDE.md) for troubleshooting and detailed information.

## Key Components

### Automation Scripts (`scripts/automation/`)
- **create_orchestrator_agent.py** — Provisions the Healthcare IQ orchestrator agent
- **test_orchestrator_agent.py** — Validates orchestrator agent functionality
- **verify_and_reset_indexer.sh** — Manages search index verification

### Configuration (`config/`)
- **azure.yaml** — AzD environment, hooks, and deployment parameters
- **devcontainer.json** — Dev container definition for standardized environment

### Documentation (`docs/`)
- **AZD_AUTOMATION_GUIDE.md** — Step-by-step deployment and automation documentation

## Workflow

### Standard Deployment

1. Open in dev container: `Dev Containers: Reopen in Container`
2. Login: `az login && azd auth login`
3. Run: `./scripts/azd/run_all.sh`
4. Monitor the deployment logs

Everything is preconfigured—no manual setup needed.

### Extending the Harness

- Add new automation scripts to `scripts/automation/`
- Add new provisioning hooks to `scripts/azd/`
- Update `docs/AZD_AUTOMATION_GUIDE.md` with any changes

## Main Demo Integration

The Fabric demo (`fabric-main/`) is included as a git submodule and automatically cloned when you initialize the container. All deployment scripts use the content from this submodule.

To update to the latest main demo version:
```bash
git submodule update --remote
```

## Documentation

- [AZD Automation Guide](docs/AZD_AUTOMATION_GUIDE.md) — Comprehensive deployment details
- [Contributing](CONTRIBUTING.md) — Development setup and guidelines

## Support & Troubleshooting

See [AZD_AUTOMATION_GUIDE.md](docs/AZD_AUTOMATION_GUIDE.md#troubleshooting) for common issues and solutions.

## License

See [LICENSE](LICENSE) file.

---

**Built for**: Demonstrating healthcare data intelligence with Microsoft Fabric and Azure AI Services
