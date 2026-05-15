# AGENTS.md — HLS IQ Automation Harness Context

> **For Copilot, GitHub Copilot, and future collaborators:** This file contains the complete context, goals, and architecture of the HLS IQ Automation Harness.

---

## WHAT Is This Project?

**HLS IQ Automation Harness** is the orchestration layer that automates one-click deployment of a complete **Healthcare Payer/Provider Analytics Solution** into Microsoft Fabric.

The harness handles:
- ✅ **Azure infrastructure provisioning** (via Azure Developer CLI)
- ✅ **Fabric workspace + capacity setup**
- ✅ **Post-provisioning automation** (data generation, ETL orchestration, AI agent setup)
- ✅ **Healthcare data agents** (Fabric Data Agent + Foundry Orchestrator Agent)
- ✅ **Ontology graph deployment** (GraphQL entity model)
- ✅ **Real-Time Intelligence (RTI)** (optional: streaming, KQL, live scoring)
- ✅ **Power BI dashboards** (auto-deployed)

**Main repository** (included as git submodule `fabric-main/`):
- https://github.com/rasgiza/Fabric-Payer-Provider-HealthCare-Demo
- Contains all demo content, notebooks, configs, and healthcare knowledge graphs

---

## WHY Does This Project Exist?

### The Problem It Solves

**Healthcare payers and providers lose $billions annually** to six compounding operational failures:

1. **Claim denials** (10-15% denial rate = $4.2M/year for mid-size system)
   - Root causes: missing docs (23%), invalid codes (18%), eligibility gaps (14%)
   - No real-time visibility into at-risk claims before submission

2. **Hospital readmissions** (CMS penalties: up to 3% of reimbursement = $13.5M at stake)
   - 30-day readmissions for CHF, COPD, pneumonia, AMI, TKA/THA tracked & penalized
   - No integrated risk scoring combining clinical + social determinant data

3. **Medication non-adherence** (triple-weighted in CMS Star Ratings)
   - Drives plan quality ratings and multi-million-dollar bonus payments
   - Adherence gaps invisible without pharmacy claims integration

4. **Social determinants hidden** (80% of health outcomes driven by non-clinical factors)
   - SDOH data (poverty, food deserts, housing, transportation) rarely integrated
   - Can't stratify population health or inform readmission prevention

5. **Provider-payer contract complexity** (health systems manage 12+ contracts)
   - Can't identify which payers underpay, deny most, or have network gaps
   - Contract-level analytics missing entirely

6. **Analytics teams can't stand up environments** (weeks to provision)
   - Python installs, credential management, infrastructure debugging
   - Business users & SQL analysts locked out of analytics

### The Solution

**One notebook. One click. Fifteen minutes.**

This harness eliminates setup burden entirely. It demonstrates:
- **Real-time denial risk dashboards** with root cause analysis + appeal tracking
- **Predictive readmission scoring** with SDOH-informed discharge planning
- **HEDIS-aligned medication adherence** monitoring with care gap closure
- **Natural language analytics** via Fabric Data Agent + Azure AI Foundry
- **Ontology-driven knowledge graphs** connecting patients → encounters → claims → providers → payers

All from a single Fabric workspace deployed in minutes with `azd up` + `run_all.sh`.

---

## WHO Works Here?

| Role | Focus | Key Files |
|------|-------|-----------|
| **Data Engineers** | ETL, Fabric notebooks, medallion architecture, data quality | `scripts/azd/postprovision.py`, `fabric-main/workspace/` notebooks |
| **Data Scientists** | Scoring models, RTI algorithms, care pathways, adherence logic | `fabric-main/scripts/` (notebooks for RTI fraud/gaps/cost) |
| **DevOps / Cloud Architects** | Infrastructure provisioning, AzD hooks, capacity scaling | `scripts/azd/`, `config/azure.yaml`, `.devcontainer/devcontainer.json` |
| **AI/ML Engineers** | Orchestrator agent instructions, knowledge base integration, Foundry setup | `fabric-main/foundry_agent/orchestrator_instructions.md`, `scripts/automation/create_orchestrator_agent.py` |
| **Healthcare Analysts** | Data quality, clinical validation, domain expertise | `fabric-main/DATA_AGENT_INSTRUCTIONS.md`, healthcare knowledge graphs |
| **Business/Demo Leads** | Storytelling, executive demos, runbook execution | `fabric-main/EXECUTIVE_DEMO_RUNBOOK.md`, `fabric-main/SAMPLE_QUESTIONS.md` |

---

## HOW Does It Work?

### Deployment Flow

```
┌─────────────────────────────────────────────────────────────────────────┐
│                     DEPLOYMENT ORCHESTRATION                            │
└─────────────────────────────────────────────────────────────────────────┘

User runs: git clone --recursive && open-in-container && az login && azd auth login

                                    │
                                    ▼
                    ┌─────────────────────────┐
                    │  Dev Container Starts   │
                    │                         │
                    │ - Ubuntu 24.04 LTS      │
                    │ - Azure CLI + AzD       │
                    │ - Python 3.9+           │
                    │ - Git with submodules   │
                    │ - fabric-main cloned    │
                    └────────────┬────────────┘
                                 │
                    User: ./scripts/azd/run_all.sh
                                 │
                                 ▼
                    ┌─────────────────────────┐
                    │   AzD Up (Bicep/ARM)    │
                    │                         │
                    │ Provisions:             │
                    │ - Resource Group        │
                    │ - Fabric Capacity (F64) │
                    │ - Fabric Workspace      │
                    │ - Storage Account       │
                    │ - Data Factory (if RTI) │
                    │ - Azure AI Services     │
                    └────────────┬────────────┘
                                 │
                    azure.yaml postprovision hook
                                 │
                                 ▼
                    ┌─────────────────────────────────┐
                    │   postprovision.py (Phase 1)    │
                    │                                 │
                    │ - Authenticate to Fabric        │
                    │ - Poll workspace creation       │
                    │ - Create deploy lakehouse       │
                    │ - Generate sample data (10K pat)│
                    │ - Run ETL pipeline              │
                    └────────────┬────────────────────┘
                                 │
                                 ▼
                    ┌─────────────────────────────────┐
                    │   postprovision.py (Phase 2)    │
                    │                                 │
                    │ - Create semantic model         │
                    │ - Deploy Power BI dashboard     │
                    │ - Create dimension SCD2 tables  │
                    │ - Generate fact aggregates      │
                    └────────────┬────────────────────┘
                                 │
                                 ▼
                    ┌─────────────────────────────────┐
                    │   create_orchestrator_agent.py  │
                    │                                 │
                    │ - Authenticate to Foundry       │
                    │ - Create Orchestrator Agent     │
                    │ - Load 21-doc Knowledge Base    │
                    │ - Configure data agent calls    │
                    │ - Test via test_orchestrator    │
                    └────────────┬────────────────────┘
                                 │
                                 ▼
                    ┌─────────────────────────────────┐
                    │   Optional RTI (streaming)      │
                    │                                 │
                    │ - Deploy Eventhouse             │
                    │ - Deploy KQL Database           │
                    │ - Run scoring notebooks         │
                    │ - Start event simulator         │
                    │ - Wire Eventstream routing      │
                    └────────────┬────────────────────┘
                                 │
                    ✅ READY FOR DEMO
                                 │
                                 ▼
        ┌──────────────────────────────────────────────────────┐
        │  User Interacts With:                                │
        │                                                      │
        │  📊 Power BI Dashboard (6 pages, 60+ visuals)        │
        │  🔍 Fabric Data Agent (natural language SQL queries) │
        │  🧠 Orchestrator Agent (policy + data fusion)        │
        │  📈 RTI Dashboard (30s refresh, live scoring)        │
        │  🕸️  Ontology Graph (entity model exploration)       │
        └──────────────────────────────────────────────────────┘
```

### Directory Structure

```
hls-iq-auto-harness/
│
├── fabric-main/                              # Git submodule
│   ├── workspace/                            # Fabric workspace structure
│   │   ├── Notebooks/                        # ETL + RTI scoring
│   │   ├── Reports/                          # Power BI dashboards
│   │   ├── SemanticModel/                    # Star schema definition
│   │   ├── DataAgent/                        # Healthcare HLS Agent config
│   │   ├── Eventhouse/                       # RTI compute (streaming)
│   │   └── KQLDatabase/                      # Real-time intelligence DB
│   │
│   ├── healthcare_knowledge/                 # Domain knowledge
│   │   ├── clinical_guidelines/              # HEDIS, readmission, adherence
│   │   ├── denial_management/                # Appeal rules, denial codes
│   │   ├── compliance/                       # HIPAA, audit, privacy
│   │   ├── provider_network/                 # Credentialing, contracting
│   │   └── quality_measures/                 # CMS Star, quality metrics
│   │
│   ├── ontology/                             # GraphQL entity model
│   │   └── Healthcare_Demo_Ontology_HLS/     # Patient→Encounter→Claim→Provider
│   │
│   ├── scripts/                              # Data processing & utilities
│   │   ├── automation/                       # (orchestrator agent setup)
│   │   ├── azd/                              # (AzD hooks & postprovision)
│   │   └── clients/                          # SDKs & API clients
│   │
│   ├── README.md                             # Main demo documentation
│   ├── AZD_AUTOMATION_GUIDE.md               # Deployment walkthrough
│   ├── DATA_AGENT_INSTRUCTIONS.md            # Data agent AI instructions
│   ├── SAMPLE_QUESTIONS.md                   # Demo conversation examples
│   ├── EXECUTIVE_DEMO_RUNBOOK.md             # Nancy & Sarah stories
│   └── foundry_agent/orchestrator_instructions.md  # Orchestrator v26+ rules
│
├── scripts/
│   ├── automation/
│   │   ├── create_orchestrator_agent.py      # Provisions Foundry orchestrator
│   │   ├── test_orchestrator_agent.py        # Validates orchestrator setup
│   │   └── verify_and_reset_indexer.sh       # Manages search indexes
│   │
│   └── azd/
│       ├── postprovision.py                  # Phase 1-2: fabric setup + agents
│       ├── run_all.sh                        # Main orchestration entry point
│       ├── install_prereqs.sh                # Container-time prereq setup
│       └── cleanup.sh                        # Teardown (optional)
│
├── config/
│   ├── azure.yaml                            # AzD environment definition
│   └── devcontainer.json                     # Dev container spec
│
├── docs/
│   └── AZD_AUTOMATION_GUIDE.md               # Detailed automation guide
│
├── README.md                                 # Quick start (clone → container → login → run)
├── CONTRIBUTING.md                           # Development guidelines
├── .gitmodules                               # Submodule configuration
└── .gitignore & .gitattributes               # Git housekeeping
```

---

## KEY ARCHITECTURAL CONCEPTS

### Data Model (Star Schema)

**Dimensions:**
- `dim_patient` (10K patients, SCD2 tracked)
- `dim_provider` (500 providers, specialty-mapped)
- `dim_payer` (12 payers with contract rates)
- `dim_diagnosis` (ICD-10 codes with HEDIS categories)
- `dim_medication` (drug classes, formulary coverage)
- `dim_sdoh` (zip-code-level social determinants)
- `dim_facility` (hospital, clinic, urgent care)

**Facts:**
- `fact_encounter` (100K encounters with readmission_risk_score, risk_category)
- `fact_claim` (100K claims with denial_risk_score, primary_denial_reason)
- `fact_prescription` (~250K prescriptions with cost split: payer/copay)
- `fact_diagnosis` (~200K diagnosis-encounter links)

**Aggregates** (pre-computed for speed):
- `agg_readmission_by_date` (daily trends)
- `agg_medication_adherence` (PDC by drug class per patient)

### Data Agent AI Instructions

**Location:** `fabric-main/DATA_AGENT_INSTRUCTIONS.md`

The Fabric Data Agent uses **concept-to-table routing**:
- **"Readmission risk"** → Routes to `fact_encounter.readmission_risk_score`
- **"Denial rate"** → Routes to `fact_claim.denial_flag`
- **"Medication adherence"** → Routes to `agg_medication_adherence.pdc_score`
- **Critical rule:** Patient disambiguation for adherence (multiple patients share names)

### Orchestrator Agent Instructions (v26)

**Location:** `fabric-main/foundry_agent/orchestrator_instructions.md`

The Azure AI Foundry Orchestrator Agent:
1. **Decomposes user questions** into DATA / KNOWLEDGE / EXTERNAL sub-queries
2. **Calls fabric_dataagent_preview** for each data need (never mixes data + knowledge)
3. **Mandatory citation protocol** — every number must come from tool call (no fabrication)
4. **Provider lookup validation** — for patient-specific questions, MUST fetch provider list before naming providers (patient safety rule)
5. **Knowledge base integration** — 21-doc KB with HEDIS, diabetes, CHF, readmission, adherence, compliance, denial appeal, credentialing, HIPAA guidelines
6. **Result combination** — data blockquote → analysis → recommendations with KB citations

### Healthcare Concepts Embedded in Demo

| Concept | Where | Purpose |
|---------|-------|---------|
| **Readmission Risk** | `fact_encounter.readmission_risk_score` (0.0-1.0) | Predict 30-day readmission likelihood for CHF, COPD, pneumonia, AMI, TKA/THA — enables discharge planning |
| **Denial Risk** | `fact_claim.denial_risk_score` + `primary_denial_reason` | Flag high-risk claims before submission — root cause by payer, appeals tracking |
| **Medication Adherence (PDC)** | `agg_medication_adherence.pdc_score` (0.0-1.0) | Proportion of Days Covered for diabetes, RAS antagonists, statins — HEDIS metrics + Star Ratings |
| **SDOH Integration** | `dim_sdoh` (zip-code) joined to every patient | Poverty rates, food deserts, transportation, housing — enables equity analysis + intervention targeting |
| **Payer Contract Rates** | `dim_payer` contract columns | Different reimbursement per payer — reveals collection rate variance, negotiation priorities |
| **HEDIS Measures** | `care_gaps` lookup table | Preventive care gaps (colonoscopy, A1c screening, flu vax, lipid panels) — care managers close gaps |
| **Care Pathways** | Ontology relationships | Patient → Encounters → Diagnoses → Medications → Providers → Payers — graph traversal for complex questions |

---

## INTEGRATION POINTS

### With Microsoft Fabric

- **OneLake:** All medallion architecture (bronze/silver/gold) stored in One Lake
- **Lakehouses:** 4 lakehouses (raw, stage, ODS, curated) with managed table formats
- **Spark Notebooks:** 5 ETL + 2 utilities + 5 optional RTI scoring notebooks
- **Pipelines:** Orchestration with full/incremental modes + scheduling
- **Semantic Model:** Star schema (100+ measures) for Power BI + data agent
- **Data Agent:** LLM-powered natural language queries + built-in disambiguation
- **Eventhouse:** KQL compute engine for sub-second RTI queries (optional streaming)

### With Azure AI Foundry

- **Orchestrator Agent:** Deployed via `create_orchestrator_agent.py`
- **Knowledge Base:** 21 healthcare policy/guideline documents (HEDIS, HIPAA, clinical protocols)
- **Data Agent Integration:** Orchestrator calls Fabric Data Agent via `fabric_dataagent_preview` tool
- **Web Search:** Optional for current CMS regulations + external benchmarks
- **Evaluation Workflows:** Test orchestrator responses against gold-standard answers

### With Power BI

- **Semantic Model:** Direct Lake connection (near-real-time)
- **Dashboards:** 6 pages (Claims, Denials, Readmission, Adherence, SDOH, Provider Network)
- **60+ visuals:** KPIs, trends, distributions, payer/specialty breakdowns

### With Azure DevOps / GitHub

- **Fabric Workspace Structure:** Git-tracked via `fabric-cicd` integration
- **Power BI Reports:** Deployed via `deploy_report_v2.py`
- **Ontology:** API-deployed GraphQL via `deploy_graph_model.py`

---

## RUNNING THE HARNESS

### Prerequisites
- **Docker** (for dev container)
- **VS Code** with Dev Containers extension

### 4-Step Deployment

```bash
# 1. Clone (with submodules auto-initialized by container)
git clone https://github.com/matthansen0/hls-iq-auto-harness.git
cd hls-iq-auto-harness

# 2. Reopen in container (VS Code auto-detects .devcontainer/devcontainer.json)
# Command Palette: Dev Containers: Reopen in Container

# 3. Authenticate
az login
azd auth login

# 4. Deploy
./scripts/azd/run_all.sh
```

**That's it.** No configuration. No manual steps. Everything is preconfigured.

### Advanced: Custom Configuration

Edit `config/azure.yaml` or `fabric-main/Healthcare_Launcher.ipynb` CONFIG cell:
- `DEPLOY_STREAMING = True` — Enable RTI (Eventhouse + KQL + scoring)
- `GITHUB_OWNER = "your-fork"` — Point to your own fork
- `FABRIC_CAPACITY_SKU = "F256"` — Change capacity size

---

## EXTENDING THE HARNESS

### Add a New Post-Provisioning Step

1. Create script in `scripts/automation/`
2. Call it from `scripts/azd/postprovision.py` with phase label
3. Print status + errors clearly
4. Add heartbeat for long-running steps

### Add a New AzD Hook

1. Edit `config/azure.yaml` under `hooks` section
2. Define new hook (e.g., `postdeploy`, `predeploy`)
3. Reference your script in `scripts/azd/`

### Extend Orchestrator Agent Knowledge Base

1. Add document to Foundry KB (via Azure Portal or SDK)
2. Update `fabric-main/foundry_agent/orchestrator_instructions.md` KB citation section
3. Test via `test_orchestrator_agent.py`

### Add RTI Scoring Logic

1. Create notebook in `fabric-main/scripts/` (or workspace)
2. Read from KQL Database real-time events
3. Join with Gold dimension tables for enrichment
4. Write scores back to KQL for dashboards

---

## KEY REPOS & LINKS

| Link | Purpose |
|------|---------|
| https://github.com/matthansen0/hls-iq-auto-harness | **This repo** (automation harness) |
| https://github.com/rasgiza/Fabric-Payer-Provider-HealthCare-Demo | **Main demo** (included as submodule `fabric-main/`) |
| https://github.com/matthansen0/Fabric-Payer-Provider-HealthCare-Demo | Fork (reference for original split) |
| https://rasgiza.github.io/Fabric-Payer-Provider-HealthCare-Demo/ | Interactive 3D ontology + patient stories |

---

## KEY FILES TO READ FIRST

1. **README.md** (this repo) — Quick start (4 steps)
2. **AZD_AUTOMATION_GUIDE.md** — Detailed deployment walkthrough + troubleshooting
3. **fabric-main/README.md** — Main demo architecture + features
4. **fabric-main/DATA_AGENT_INSTRUCTIONS.md** — How Fabric Data Agent works
5. **fabric-main/foundry_agent/orchestrator_instructions.md** — Orchestrator AI rules (v26)
6. **fabric-main/EXECUTIVE_DEMO_RUNBOOK.md** — Nancy & Sarah patient stories (high-level)
7. **CONTRIBUTING.md** — Development setup + guidelines

---

## KNOWN BEHAVIORS & QUIRKS

### AzD Timing
- **Portal completion precedes terminal completion** — Infrastructure shows ready in Azure Portal before `azd up` command finishes in terminal
- **Heartbeat every 30 seconds** — Long operations (model deployment, capacity scale) are printed to show progress
- **Wait output for data operations** — Fabric capacity scale + model creation have visible output during provisioning

### Data Agent Disambiguation
- **Patient name disambiguation required** — Multiple patients may share first+last name (by design, for realistic healthcare scenarios)
- **Two-step patient lookup** — Always disambiguate by age/gender first, then fetch specific patient's adherence data
- **Never query by name alone** — Returns multiple rows, causing duplicate drug-class confusion

### Orchestrator Agent Rules
- **MANDATORY tool calls** — Every number in response must come from `fabric_dataagent_preview` tool call (no hallucinations)
- **Provider lookup validation** — For patient-specific questions, MUST call for provider list before naming any provider (patient safety)
- **Knowledge Base priority** — Always cite KB docs; never say "no guideline found" (21 docs cover most healthcare topics)

---

## SUPPORT & CONTACT

See **CONTRIBUTING.md** for development guidelines.
See **AZD_AUTOMATION_GUIDE.md** troubleshooting section for common issues.

---

**Last Updated:** May 15, 2026  
**Maintainer:** Matthew Hansen (matthansen0)  
**License:** MIT (see LICENSE file)
