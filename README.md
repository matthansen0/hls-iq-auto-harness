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
- **Azure CLI** (az)
- **Azure Developer CLI** (azd)
- **Python 3.9+**
- **Git** (with submodule support)

### Clone & Initialize

```bash
git clone https://github.com/matthansen0/hls-iq-auto-harness.git
cd hls-iq-auto-harness
git submodule update --init --recursive
```

### Deploy with AzD

```bash
# Set your environment
azd config set defaults.subscription <SUBSCRIPTION_ID>
azd config set defaults.location <LOCATION>

# Provision infrastructure and deploy
azd up
```

See [AZD_AUTOMATION_GUIDE.md](docs/AZD_AUTOMATION_GUIDE.md) for detailed walkthrough.

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

### Development

1. Clone this repo and initialize submodules
2. Use dev container for consistent environment: `Dev Containers: Reopen in Container`
3. Test scripts locally before deployment
4. Run `azd up` to provision and deploy

### Extending

- Add new automation scripts to `scripts/automation/`
- Update `azure.yaml` to hook new provisioning steps
- Document changes in `docs/`

## Integration with Main Demo

The main Fabric demo (`fabric-main/`) is included as a git submodule. To pull latest changes:

```bash
git submodule update --remote
```

This harness orchestrates deployments but does not duplicate demo content—allowing clean separation of automation concerns from core application logic.

## Documentation

- [AZD Automation Guide](docs/AZD_AUTOMATION_GUIDE.md) — Comprehensive deployment walkthrough
- [Main Demo README](fabric-main/README.md) — Healthcare demo architecture & features

## Support & Troubleshooting

See [AZD_AUTOMATION_GUIDE.md](docs/AZD_AUTOMATION_GUIDE.md#troubleshooting) for common issues and solutions.

## License

See [LICENSE](LICENSE) file.

---

**Built for**: Demonstrating healthcare data intelligence with Microsoft Fabric and Azure AI Services
