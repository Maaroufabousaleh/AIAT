---
name: mas-architecture-review
description: Review AIAT MAS architecture, roadmap scope, module boundaries, and implementation plans. Use when working in C:\projects\AIAT on MAS design changes, `.github/prompts/PLAN_gamma.md`, `.github/prompts/PLAN_alpha_beta.md`, `next.txt`, README drift, cross-service changes under `mas/`, worker adoption policy, security boundaries, dashboard/operator UX scope, or questions about whether a proposed change belongs in the active `mas/` workspace.
---

# MAS Architecture Review

## Overview

Use this skill to keep AIAT MAS work aligned with the current repo shape: the root coordinates docs and plans, while `mas/` is the active implementation workspace.

## Review Workflow

1. Start from the active target.
   - Root-level docs/plans: inspect `README.md`, `next.txt`, `.github/prompts/PLAN_gamma.md`, and `.github/prompts/PLAN_alpha_beta.md`.
   - Implementation: inspect `mas/README.md`, the relevant `mas/apps/*`, `mas/packages/*`, `mas/workers`, and tests.

2. Preserve the AIAT control-plane model.
   - AIAT supervises orchestration, worker adoption, credentials, policy, and operator approval.
   - Do not turn new open-source integrations into unmanaged direct execution paths.
   - Prefer governed worker manifests, evaluation metadata, sandbox profiles, and dashboard visibility.

3. Keep user-facing and operator-facing surfaces in sync.
   - Backend worker or workflow changes usually need dashboard/API proxy updates.
   - Dashboard pages should expose real current state, pending approvals, errors, and unavailable states.
   - Documentation should match implemented commands, ports, and workflow status.

4. Attach proof to architecture conclusions.
   - Cite exact files and current code paths.
   - Separate implemented, covered by tests, manual/live verified, and deferred.
   - Include exact commands and pass/fail results when claiming completeness.

## Key Boundaries

- The canonical app source is `mas/`, not older generated root-level app output.
- Compose files are under `mas/infra/compose`.
- The dashboard is an authenticated Next.js proxy. Browser code should call `mas/apps/mas-dashboard/app/api/*`; those routes call backend services with server-side credentials.
- Worker adoption should flow through `GET/POST /capabilities/workers`, evaluation routes, sandbox policy, and the Hiring Board UI.
- Protocol contracts use `aiat.v1` schemas and checked fixtures under `mas/packages/mas-core`.

## Verification Anchors

Prefer the smallest command set that proves the changed boundary:

- Backend behavior: `cd mas && uv run pytest <targeted tests> -q`
- WSL backend fallback: `wsl.exe bash -lc 'cd /mnt/c/projects/AIAT/mas && .venv-wsl/bin/python -m pytest <targeted tests> -q'`
- Dashboard compile and contracts: `cd mas/apps/mas-dashboard && npm run build && npm run test:protocol-fixtures`
- Operator UI: `cd mas/apps/mas-dashboard && npx playwright test --workers=1 <spec>`
- Compose config: `cd mas && docker compose -f infra/compose/docker-compose.yml --env-file infra/compose/.env config --quiet`
