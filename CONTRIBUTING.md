# Contributing to HLS IQ Automation Harness

## Development Setup

1. **Clone with submodules:**
   ```bash
   git clone --recursive https://github.com/matthansen0/hls-iq-auto-harness.git
   cd hls-iq-auto-harness
   ```

2. **Use dev container** (recommended):
   - Open in VS Code
   - Click "Reopen in Container" when prompted
   - The canonical manifest is `.devcontainer/devcontainer.json`
   - Its post-create step initializes the public submodule and runs the idempotent prerequisite installer
   - Runs in a consistent Ubuntu environment with Git, curl, Python, Azure CLI, azd, jq, and `requests`

3. **Manual setup** (if not using dev container):
   - Python 3.9+
   - Azure CLI (`az`)
   - Azure Developer CLI (`azd`)
   - `jq`
   - Python `requests`
   - Bash

## Project Structure

- `fabric-main/` — Main demo content (separate Git repository and commit history)
- `scripts/automation/` — Orchestration scripts
- `scripts/azd/` — Azure Developer CLI hooks
- `docs/` — Documentation

## Before Committing

1. **Test your changes:**
   ```bash
   python3 -m unittest -v tests/test_recovery_automation.py
   python3 -m py_compile scripts/automation/*.py scripts/azd/postprovision.py
   bash -n scripts/azd/run_all.sh scripts/azd/cleanup.sh
   az bicep build --file infra/main.bicep --stdout >/dev/null
   git diff --check
   git -C fabric-main diff --check
   ```

2. **Update documentation** if you change behavior

3. **Keep commits focused** — one change per commit with clear messages

## Updating The Main Demo Submodule

The submodule points directly to the public `rasgiza` upstream. Do not commit harness-specific changes inside `fabric-main`; contribute general demo changes to that upstream project first, then pin the accepted upstream commit here.

```bash
git -C fabric-main fetch origin main
git -C fabric-main checkout --detach origin/main
git add fabric-main
git commit -m "chore: update fabric-main submodule"
```

A root commit must never reference a submodule commit that is unavailable from `https://github.com/rasgiza/Fabric-Payer-Provider-HealthCare-Demo.git`.

## Reporting Issues

- Describe what you expected vs. what happened
- Include the region and sanitized environment name when relevant
- Attach only redacted logs; never include tokens, keys, UPNs, tenant IDs, subscription IDs, workspace IDs, or resource-specific portal URLs

## Code Style

- **Python:** PEP 8 compliant
- **Shell:** Follow existing scripts in `scripts/automation/`
- **Documentation:** Markdown with clear sections

---

Questions? See [AZD_AUTOMATION_GUIDE.md](docs/AZD_AUTOMATION_GUIDE.md) or open an issue.
