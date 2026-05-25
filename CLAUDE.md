# AIAT Claude Instructions

This repository contains AIAT MAS. The active implementation workspace is `mas/`; use the repository root for shared docs, prompts, and coordination.

## Project Rules

- Read the relevant code and tests before answering run, implementation, or validation questions.
- Keep root docs and `mas/` docs aligned when behavior changes.
- Treat `.github/prompts/PLAN_gamma.md` as the current next-phase roadmap in this checkout, with `.github/prompts/PLAN_alpha_beta.md` as prior-phase context. Avoid creating new scattered prompt-plan files unless asked.
- Do not revert unrelated user changes. This repo may already have a dirty worktree.
- For "does it work now?" questions, verify through the operator-facing dashboard when the task is dashboard behavior.
- Report exact validation commands and results.

## Common Commands

From `mas/`:

```bash
docker compose -f infra/compose/docker-compose.yml --env-file infra/compose/.env up -d --build
uv run alembic upgrade head
uv run pytest
```

From `mas/apps/mas-dashboard`:

```bash
npm run build
npm run lint
npm run test:protocol-fixtures
npm run test:e2e
```

WSL backend fallback:

```powershell
wsl.exe bash -lc 'cd /mnt/c/projects/AIAT/mas && .venv-wsl/bin/python -m pytest <tests> -q'
```

## Local Skills

Project skills are installed under `.claude/skills/`.

- `mas-architecture-review`: design, roadmap, docs, and boundary review.
- `mas-api-contract-verifier`: backend, dashboard proxy, protocol, and TypeScript contract checks.
- `mas-dashboard-e2e-tester`: dashboard operator workflows and Playwright validation.
- `mas-flow-auditor`: flow runtime, approvals, retry, escalation, and persistence behavior.
