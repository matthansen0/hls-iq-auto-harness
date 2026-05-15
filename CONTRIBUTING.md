# Contributing to HLS IQ Automation Harness

## Development Setup

1. **Clone with submodules:**
   ```bash
   git clone https://github.com/matthansen0/hls-iq-auto-harness.git
   cd hls-iq-auto-harness
   git submodule update --init --recursive
   ```

2. **Use dev container** (recommended):
   - Open in VS Code
   - Click "Reopen in Container" when prompted
   - Runs in consistent Ubuntu environment with prerequisites pre-installed

3. **Manual setup** (if not using dev container):
   - Python 3.9+
   - Azure CLI (`az`)
   - Azure Developer CLI (`azd`)
   - Bash

## Project Structure

- `fabric-main/` — Main demo (git submodule) — **do not edit files here**
- `scripts/automation/` — Orchestration scripts
- `scripts/azd/` — Azure Developer CLI hooks
- `config/` — Environment & container configuration
- `docs/` — Documentation

## Before Committing

1. **Test your changes:**
   ```bash
   python -m py_compile scripts/automation/*.py
   bash scripts/automation/verify_and_reset_indexer.sh --dry-run  # if applicable
   ```

2. **Update documentation** if you change behavior

3. **Keep commits focused** — one change per commit with clear messages

## Updating Main Demo Submodule

To pull latest changes from the main demo:

```bash
git submodule update --remote
git add fabric-main
git commit -m "chore: update fabric-main submodule"
git push
```

## Reporting Issues

- Describe what you expected vs. what happened
- Include deployment environment (subscription, region, resource group)
- Attach relevant logs from `azd` or application

## Code Style

- **Python:** PEP 8 compliant
- **Shell:** Follow existing scripts in `scripts/automation/`
- **Documentation:** Markdown with clear sections

---

Questions? See [AZD_AUTOMATION_GUIDE.md](docs/AZD_AUTOMATION_GUIDE.md) or open an issue.
