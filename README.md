# HLS IQ Automation Harness

Automation orchestration layer for the [Fabric Payer-Provider Healthcare Demo](https://github.com/rasgiza/Fabric-Payer-Provider-HealthCare-Demo), built to streamline deployment, configuration, and operational excellence for the healthcare intelligence solution.

## Overview

This repository provides:
- **Automated deployment pipelines** via Azure Developer CLI (AzD)
- **Orchestrator agent scaffolding** for Healthcare IQ
- **Notebook launcher automation** and dev container setup
- **Post-provisioning configuration** and verification scripts

The **Fabric main demo** is pulled in as a submodule (`fabric-main/`), allowing this automation harness to orchestrate and extend its capabilities without duplicating content.


## Quick Start

### Prerequisites
- **Docker** (for dev container)
- **VS Code** with Dev Containers extension

#### Enable following Fabric Tenant settings: 
- Service Principals can use Fabric APIs
- User can create Graph
- User can create Ontology
- Enable Operations Agent


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
   az login --use-device-code
   azd auth login --use-device-code
   ```

4. **Deploy everything:**
   ```bash
   bash scripts/azd/run_all.sh
   ```

> [!NOTE]
> *Due to model availability and capacity constraints, for lab and demo environments as of 5/15/26 Sweeden Central is the reccomended region for deployment.*
   

That's it! The deployment is fully automated. See [AZD_AUTOMATION_GUIDE.md](docs/AZD_AUTOMATION_GUIDE.md) for troubleshooting and detailed information.

4. **Cleanup:**
   ```bash
   bash scripts/azd/cleanup.sh
   ```

When you're all done, you can pause the Fabric capacity, or if you want you can use the cleanup script to fully cleanup the Azure and Fabric environments. 

### Documentation (`docs/`)
- **AZD_AUTOMATION_GUIDE.md** — Step-by-step deployment and automation documentation

## Workflow

### Standard Deployment

1. Open in dev container: `Dev Containers: Reopen in Container`
2. Login: `az login && azd auth login`
3. Run: `./scripts/azd/run_all.sh`
4. Monitor the deployment logs


## Documentation

- [AZD Automation Guide](docs/AZD_AUTOMATION_GUIDE.md) — Comprehensive deployment details
- [Contributing](CONTRIBUTING.md) — Development setup and guidelines

## Support & Troubleshooting

See [AZD_AUTOMATION_GUIDE.md](docs/AZD_AUTOMATION_GUIDE.md#troubleshooting) for common issues and solutions.

## License

See [LICENSE](LICENSE) file.

---

**Built for**: Demonstrating healthcare data intelligence with Microsoft Fabric and Azure AI Services
