# tech-radar

CLI that gathers the latest backend/frontend/devops/cloud/security info,
ranks and filters it by importance, optionally enriches it with an LLM,
and renders permanent date-based HTML digests behind a central hub.

## Quick start
```bash
python3.13 -m venv .venv && source .venv/bin/activate   # Python 3.11+ required
pip install -r requirements-dev.txt
cp config/radar.example.toml config/radar.toml   # then edit
export RADAR_LLM_API_KEY=...                      # optional; omit for rule-based digest
python radar.py run
open output/index.html
```

## Commands
- `python radar.py fetch`  — gather sources → `output/data/<date>.json` (resumable per source)
- `python radar.py enrich` — optional LLM pass (resumable per category)
- `python radar.py render` — build `output/digests/<date>.html` + `output/index.html`
- `python radar.py run`    — all three
- Flags: `--config`, `--output`, `--force` (all stages), `--fresh` (fetch only)

## Publishing
The included GitHub Actions workflow runs daily, commits `output/` back to `main`
(versioned site + audit trail), and deploys `output/` to GitHub Pages. Enable Pages
in the repo settings with **Source = GitHub Actions**. Store `RADAR_LLM_API_KEY` as a
repository secret (optional; without it the digest is rule-based).

## Testing
```bash
python -m pytest -q
```
