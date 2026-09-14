# Git Repository Initialization

## Repository

- Default branch: `main`
- Remote: `https://github.com/ourhome-macro/agent-investigator.git`
- Baseline: DeerFlow 2.0 source snapshot plus the local Windows source-mode fixes covered by tests.

## Commit hygiene

The repository ignore rules exclude local credentials, generated configuration,
runtime state, dependencies, build output, logs, and caches. In particular:

- `.env`
- `config.yaml`
- `extensions_config.json`
- `.extensions_config.json.lock`
- `.deer-flow/`
- `logs/`
- Python virtual environments and caches
- `frontend/node_modules/`
- `frontend/.next/`

The following local planning/bootstrap documents are intentionally retained only
in the working directory through `.git/info/exclude` and are not part of commits:

- `docs/COMPETITIVE_INTELLIGENCE_MULTI_AGENT_FEASIBILITY_ZH.md`
- `docs/SOURCE_RUN_AND_ARCHITECTURE_ZH.md`

Before each push, inspect the staged file list and run a staged-content secret
scan. Do not use broad force-add commands to bypass ignore rules.
