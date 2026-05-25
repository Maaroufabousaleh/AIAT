# AIAT Agent Instructions

This repository contains the AIAT MAS codebase. The active implementation workspace is `mas/`; use the checkout root mainly for shared docs, prompts, memory, and top-level coordination.

## Working Rules

- Inspect the current code and tests before answering implementation or runbook questions.
- Keep root-level docs and `mas/` implementation docs aligned when behavior changes.
- Treat `.github/prompts/PLAN_gamma.md` as the current next-phase roadmap in this checkout, with `.github/prompts/PLAN_alpha_beta.md` as prior-phase context. Do not create scattered new plan files unless explicitly asked.
- Do not revert user changes. This repo often has an intentionally dirty worktree.
- Prefer targeted changes tied to an operator-facing workflow, API contract, or plan item.
- When reporting completion, include exact validation commands and results.

## MAS Commands

Run service, migration, Python, and dashboard commands from `mas/` unless a task specifically targets the repository root.

```bash
cd mas
docker compose -f infra/compose/docker-compose.yml --env-file infra/compose/.env up -d --build
uv run alembic upgrade head
uv run pytest
```

Dashboard commands run from `mas/apps/mas-dashboard`:

```bash
npm install
npm run build
npm run lint
npm run test:protocol-fixtures
npm run test:e2e
```

On this Windows checkout, WSL validation has historically been more reliable for backend pytest:

```powershell
wsl.exe bash -lc 'cd /mnt/c/projects/AIAT/mas && .venv-wsl/bin/python -m pytest <tests> -q'
```

Avoid using `uv` against `mas/.venv` if it tries to mutate `lib64` and fails with `Access is denied`.

## Local Skills

Codex skills live in `.agents/skills/`.
Claude Code skills live in `.claude/skills/`.

Use these project skills when their trigger matches the task:

- `mas-architecture-review`: architecture, roadmap, boundary, or design review.
- `mas-api-contract-verifier`: backend/API/dashboard proxy contract changes.
- `mas-dashboard-e2e-tester`: dashboard operator workflows and Playwright coverage.
- `mas-flow-auditor`: flow runtime, approval, retry, escalation, and persistence behavior.
