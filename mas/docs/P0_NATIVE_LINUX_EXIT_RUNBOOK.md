# P0 Native-Linux Exit Runbook

**Purpose:** produce the live evidence that cannot be generated in the current
WSL checkout and close the remaining P0 release-integrity gates.

**Authority:** [root roadmap](../../ROADMAP.md), [P0 plan](../../Docs/current/plans/P0_RELEASE_INTEGRITY_PLAN.md), and [current ledger](AIAT_CURRENT_RELEASE_LEDGER.md).

This runbook is for a clean native-Linux host with Docker Engine/Compose v2,
the repository at a frozen commit, and a private environment manifest. Do not
replace a missing live result with a static claim. Record every command,
timestamp, image digest, and artifact path in the current release ledger.
Run the commands below from the `mas/` workspace unless a path says otherwise.

## 1. Freeze inputs and validate source contracts

1. Check out the exact release candidate commit into a clean working tree.
2. Copy `infra/compose/production-image-lock.example.env` to a private lock
   file and replace every placeholder with a real digest-bearing image ref.
3. Validate the source contracts before touching the stack:

   ```bash
   uv run python scripts/check_provenance.py
   uv run python scripts/check_worker_reconciliation.py --json
   uv run python scripts/check_image_provenance.py --env-file /secure/aiat-image-lock.env
   uv run python scripts/check_image_provenance.py --live --json \
     --env-file /secure/aiat-image-lock.env \
     > /secure/aiat-image-provenance-live.json
   uv run python scripts/check_image_budgets.py
   uv run python scripts/check_release_ledger.py --json \
     > /secure/aiat-release-ledger-static.json
   docker compose --env-file /secure/aiat.env \
     --env-file /secure/aiat-image-lock.env \
     -f infra/compose/docker-compose.yml config --quiet
   ```

   Preserve the resulting Compose config as a release artifact. The
   development `mas.sh` wrapper is not evidence for this run.

   The live command must return `0` only when every deployment-supplied image
   digest matches the local Docker `RepoDigests`. Exit `2` means Docker or the
   immutable deployment refs are unavailable and is an externally blocked
   result; exit `1` means an identity mismatch. Its scope is deliberately local
   identity only, so it never substitutes for SBOM, vulnerability scan,
   clean-build, or source/lock reconciliation evidence.

   The release-ledger command is the aggregation boundary for this run. It
   records static/contract/recovery results, pending evidence, worktree state,
   and the conservative `NO-RELEASE` decision without printing credentials.

## 2. Build/pull and reconcile images

Build the AIAT images from the frozen source and pull the selected LiteLLM and
OmniRoute images. For each service in
`docs/provenance/production_images.yaml`, record:

- resolved `RepoDigests` from `docker image inspect`;
- source commit, Dockerfile/profile, build arguments, and dependency lock hash;
- SBOM output (Docker Scout, Syft, or the approved equivalent);
- vulnerability/secret scan output and disposition;
- compressed and uncompressed image sizes.

The recorded digest must exactly match the `*_IMAGE_REF` value used by the
production Compose invocation. Update the provenance ledger only after the
clean pull/build and scans complete.

Build both tool-service profiles from the same source:

```bash
docker build --build-arg TOOL_SERVICE_PROFILE=core \
  -f infra/docker/Dockerfile.tool-service .
docker build --build-arg TOOL_SERVICE_PROFILE=extensions \
  -f infra/docker/Dockerfile.tool-service .
```

Run the budget helper against each resulting image and record startup and
steady-state memory in addition to the helper's size check:

```bash
uv run python scripts/check_image_budgets.py --budget tool-service-core --image-ref <core-ref>
uv run python scripts/check_image_budgets.py --budget tool-service-extensions --image-ref <extensions-ref>
```

## 3. Start the clean stack and run the identity/ACL matrix

Start only the production Compose file with the frozen environment. Provision
unique operator, CEO, worker, service, PM-gateway, and gateway keys. Verify:

| Caller | Allowed examples | Denied examples |
| --- | --- | --- |
| operator | every dashboard section; ACL update | none within the operator surface |
| CEO | `ceo`, `projects`, `governance`, `workers` | `credentials`, `identity`, `operations` |
| service | `analytics`, `projects`, `workers` | `credentials`, `identity` |
| worker | `projects`, `workers` | `credentials`, `ceo`, `operations` |
| PM gateway | `integrations`, `projects` | `credentials`, `ceo` |

For each row call `GET /dashboard/access` and
`GET /dashboard/sections/{section}` with the caller's API key and a matching
`X-AIAT-Dashboard-Section` header. Record HTTP 200/403 outcomes and verify
that a non-operator `PUT /dashboard/sections/{section}/acl` returns 403 while
an operator update persists after an orchestrator restart.

## 4. Run the network denial/allow matrix

From each team-runner container, first record its environment and network
memberships. It must contain no `PGBOUNCER_DSN`, `MINIO_*`, or shared
`MAS_API_KEY`; only the distinct CEO/worker control-plane key may be used for
the narrow storage API. Run the non-secret verifier from the `mas/` directory:

```bash
uv run python scripts/check_network_boundary.py --live --json \
  > /secure/aiat-network-boundary.json
uv run python scripts/check_release_ledger.py --live --json \
  > /secure/aiat-release-ledger-live.json
```

Exit `0` means every live probe passed, `1` means a running boundary was
violated, and `2` means Docker/Engine is unavailable and the evidence is
externally blocked. The command records container IDs only by a short prefix
and never emits environment values or credentials. It then tests DNS/TCP/HTTP
access to Redis, Postgres/PgBouncer, MinIO, the identity database, Docker
sockets, and an unapproved provider endpoint. Every direct data-plane probe
must fail. From the same containers, prove positive access only to the router,
tool service, orchestrator, and explicitly approved model gateway endpoints,
including one checkpoint/review/usage call through
`POST /internal/team-runners/{team_id}/storage`.

Capture the Compose network membership and the exact probe output. Re-run the
historical `DEF-2026-07-14-036` negative case and append its post-fix closure
record to `AIAT_CURRENT_RELEASE_LEDGER.md`.

## 5. Exercise bounded metrics and recovery

Create enough disposable projects/runs to exercise the configured metric
series budget. Scrape every AIAT Prometheus family and verify that no label
contains a raw project ID and that the total/family budgets remain below their
ceilings. Preserve the scrape and budget output.

Finally run shutdown, worker lease expiry, router reclaim/DLQ replay, restart,
and project resume tests. Include at least one browser/API dashboard golden
path on the native host; no human-only confirmation may be simulated.

## 6. Publish the ledger decision

Use the machine-readable static/live reports as the source for the evidence
rows, then update `AIAT_CURRENT_RELEASE_LEDGER.md` with static, contract, integration,
live API/UI, recovery, security, and externally blocked evidence labels. A P0
release decision may change from **NO-RELEASE / P0 INCOMPLETE** only when every
required row has current evidence, the working tree/commit is frozen, and no
Critical defect remains open.
