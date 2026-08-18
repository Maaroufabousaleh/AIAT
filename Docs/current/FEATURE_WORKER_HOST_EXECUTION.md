# Worker-Plane Host Execution Feature

**Baseline:** 2026-08-17

**Status:** local Compose Postgres single-host, concurrent two-host native,
bounded duplicate-effect/replay protection, complete local governed run-version
pinning, fenced host-loss queue-recovery, a repeated same-host recovery soak,
selected model-resolution host-execution, and the fail-closed Firecracker launch
contract are implemented; deployed runtime, host-certified sandbox, provider,
and independent-host recovery evidence remain open

**Implementation:** `73c0bda`, `f9c717b`, `d45e4dd`, `7c1ef74`, `893293a`, `424805c`, `6cef1b8`, `9a7db70`, `5ed0a0b`

**Authority:** [AIAT Target Programme](../../AIAT_TARGET_PROGRAMME.md)
**Related plan:** [P2 Scale, Storage, and Guarded Autonomy Plan](plans/P2_SCALE_STORAGE_AND_AUTONOMY_PLAN.md)

## Purpose

This feature is the AIAT-owned execution edge between durable worker placement
and the existing `WorkerRunController`. It lets a host process execute a run
only after the control plane has committed a run-to-reservation binding. The
host process is still an untrusted execution boundary; it receives no control-
plane authority from this wrapper.

The feature is deliberately narrower than a provider or sandbox integration. It
certifies local host admission, deterministic model-profile resolution and
snapshot propagation, native adapter lifecycle, concurrent execution against
two distinct durable worker-host records, duplicate claim rejection and
terminal/alias replay without redispatch, and explicit queue recovery after a
fenced host lease is lost. It keeps gVisor, Firecracker, external providers,
remote runtimes, and independent-host outage recovery as separate evidence
boundaries.

The high-risk launch contract in
[`firecracker.py`](../../mas/packages/mas-core/mas_core/worker_registry/firecracker.py)
and [`FirecrackerAdapter`](../../mas/packages/mas-core/mas_core/worker_registry/runtime_adapters.py)
(`5ed0a0b`) validates immutable kernel/rootfs digests, bounded vCPU/memory/PID/
disk/output/time limits, read-only rootfs, deny-by-default egress, opaque
secret references, artifact output, and cleanup. The adapter emits argv only
through an explicitly named certified launcher; it never falls back to Docker,
runc, or gVisor. Static readiness passes, while the current live probe is
blocked because the launcher and Firecracker binary are unavailable. Evidence
is [`firecracker_worker_pool_readiness.json`](../../mas/docs/provenance/firecracker_worker_pool_readiness.json).

## Contract

The implementation is in
[`host_executor.py`](../../mas/packages/mas-core/mas_core/worker_registry/host_executor.py)
and exposes `aiat.worker-host-execution.v1`.

`HostExecutionRequest` carries only the run UUID, authenticated host identity,
execution owner, and bounded Worker Run lease duration. `WorkerHostExecutor`
requires all of the following before claiming work:

1. A binding exists for the requested run.
2. Binding and reservation state are both `COMMITTED`.
3. Binding host identity matches the requesting host.
4. Binding worker identity matches the selected worker registry row.
5. The binding host plane is exactly `worker` and the host is `READY`.
6. The binding lease generation equals the current host lease generation.
7. The current host lease is valid at the admission read.

Admission rejects a missing, stale, released, cross-plane, cross-host,
cross-worker, or generation-mismatched binding with a stable reason code. No
runtime is started before the checks and the atomic Worker Run claim succeed.

## Execution sequence

```text
committed binding
      │
      ├─ read host plane/status/generation/lease
      ├─ claim queued Worker Run as host owner
      ├─ WorkerRunController.execute(request, adapter)
      │     └─ readiness → negotiation → dispatch → evidence → terminal state
      └─ release committed reservation and binding
```

The claim owner is the binding owner, making host admission, queue lease, and
release auditable under one bounded identity. Controller terminal handling
remains authoritative for artifact/usage persistence and terminal state. The
binding service now permits the required `COMMITTED → RELEASED` transition and
keeps replay idempotent.

## Durable evidence

The reserved live checker is
[`check_worker_host_execution_postgres.py`](../../mas/scripts/check_worker_host_execution_postgres.py).
It registers one deterministic worker and worker-plane host, creates a queued
run, commits the binding, executes the real native fixture adapter through the
host executor, reads the run/binding/usage/artifact/trace records through a new
Postgres connection, and removes only its fixture namespace.

Evidence is retained at
[`worker_host_execution_postgres_evidence.json`](../../mas/docs/provenance/worker_host_execution_postgres_evidence.json).
The latest pass records:

| Signal | Observed |
| --- | --- |
| Migration | `0042_worker_run_host_binding` |
| Worker Run claim / terminal state | `CLAIMED` / `SUCCEEDED` |
| Admission | worker plane, generation `1 == 1`, current host lease valid |
| Binding / reservation settlement | `COMMITTED` before execution, `RELEASED` after execution |
| Durable evidence | one usage row, one artifact row, three native spans, payload-free projection |
| Reopen and cleanup | healthy read-back; all fixture counts return to zero |
| External effects | no external network or provider mutation; native fixture dispatch only |

The report intentionally says sandbox runtime, external provider/remote runtime,
and provider-backed recovery are `not_checked`. A native fixture is not a claim
that gVisor or Firecracker is active.

### Concurrent multi-host native certificate

[`check_worker_multi_host_execution_postgres.py`](../../mas/scripts/check_worker_multi_host_execution_postgres.py)
extends the same boundary with two queued runs, two separately reserved
worker-plane host records, and two concurrent `WorkerHostExecutor` calls. The
live certificate is retained at
[`worker_multi_host_execution_postgres_evidence.json`](../../mas/docs/provenance/worker_multi_host_execution_postgres_evidence.json).
It records both host-specific lease generations as current, two `CLAIMED` and
`SUCCEEDED` runs, released bindings and reservations, two usage rows, two
artifacts, three native spans per trace, payload-free coverage, Postgres
reopen/read-back, and zero remaining fixture rows. The upgraded certificate
(`d45e4dd`) concurrently races a second host-executor claim for one run and
records one `worker_run_claim_failed` rejection, exactly two adapter dispatches,
and terminal plus alternate-run-ID idempotency replays that return the canonical
`SUCCEEDED` run without redispatch. This is a deterministic native fixture
running through two AIAT host identities; it is not evidence that two
independently deployed machines, gVisor, Firecracker, an external provider, or
host-loss recovery is active.

### Independent process execution certificate

[`check_worker_independent_process_execution_postgres.py`](../../mas/scripts/check_worker_independent_process_execution_postgres.py)
launches two separate Python child processes on the local Compose host. Each
child reconnects to Postgres and executes one committed worker-plane binding
through the production `WorkerHostExecutor` and `WorkerRunController`; the
parent reopens the store, verifies two `SUCCEEDED` runs, two usage rows, two
artifacts, three native spans per trace, payload-free coverage, distinct child
process IDs, and zero remaining fixture rows. Evidence is retained at
[`worker_independent_process_execution_postgres_evidence.json`](../../mas/docs/provenance/worker_independent_process_execution_postgres_evidence.json).
This closes the local process-isolation prerequisite only. It does not claim
independent deployed machines, host-loss/split-brain recovery, external
provider execution, or gVisor/Firecracker operation.

### Fenced host-loss queue recovery certificate

The recovery extension is implemented by
`WorkerRunHostBindingService.reassign_after_host_loss()` and the scoped
`host_ids` filter on `HostLeaseRecovery.reconcile_expired_hosts()`. The live
checker is
[`check_worker_host_loss_queue_recovery_postgres.py`](../../mas/scripts/check_worker_host_loss_queue_recovery_postgres.py),
with evidence at
[`worker_host_loss_queue_recovery_postgres_evidence.json`](../../mas/docs/provenance/worker_host_loss_queue_recovery_postgres_evidence.json).
It expires one reserved host lease and one claimed Worker Run lease, fences the
host and reservation, requeues the run through canonical storage recovery,
rejects the stale host executor before dispatch, reassigns the queued binding
to host B, completes the native retry at attempt two, reopens Postgres, and
cleans the fixture namespace. The recovery report is host-filtered so unrelated
expired hosts are not mutated. This is AIAT-owned local recovery evidence, not
independent-machine, sandbox, provider, or provider-backed recovery evidence.

### Same-host recovery soak certificate

Commit `424805c` adds
[`check_worker_host_loss_queue_recovery_soak_postgres.py`](../../mas/scripts/check_worker_host_loss_queue_recovery_soak_postgres.py),
which invokes the production host-loss checker three times in separate child
processes against the local Compose Postgres boundary. The retained
[`worker_host_loss_queue_recovery_soak_postgres_evidence.json`](../../mas/docs/provenance/worker_host_loss_queue_recovery_soak_postgres_evidence.json)
records three successful `SUCCEEDED` retries at attempt two, stale-executor
rejection before dispatch, durable reopen, zero residual fixture rows, and a
scalar-only parent projection with no fixture payload. This is a bounded
same-host consistency soak: it does not certify independent deployed hosts,
split-brain across machines, gVisor/Firecracker, provider or provider-outage
recovery, clean-host recovery, deployment-wide load/chaos, or disaster
recovery.

### Selected model-resolution host certificate

[`check_worker_host_model_resolution_postgres.py`](../../mas/scripts/check_worker_host_model_resolution_postgres.py)
adds the local model-backed contract edge. It creates an approved AIAT Model
Profile and version, resolves it through `ModelProfileResolver`, persists the
immutable resolution snapshot, and carries requested/resolved references and
the snapshot ID through a committed worker-host execution. The live fixture
registers the worker as `aiat_gateway`, dispatches through the production
`GatewayWorkerAdapter` over one bounded local gateway double, and reads the
exact gateway call, provider/model usage attribution, terminal evidence,
released binding, and snapshot back after a Postgres connection reopen; the
executor rejects missing or request-mismatched snapshots before claiming work;
payload-free trace coverage and scoped cleanup pass. Evidence is retained at
[`worker_host_model_resolution_postgres_evidence.json`](../../mas/docs/provenance/worker_host_model_resolution_postgres_evidence.json).
The provider and model are local deterministic fixture identifiers: this closes
AIAT control-plane resolution and local gateway-adapter propagation only, not a
network provider call, provider outage recovery, independent hosts, gVisor, or
Firecracker.

### Durable local gateway provider-shaped recovery

The durable gateway/mail-edge checker can now run `--provider-recovery` against
the default local fixture profile. Commits `1679341` and `9c7e76d` inject one
synthetic transient `429` into the fixture gateway, exercise the production
`GatewayWorkerAdapter` retry budget, and verify two attempts with one retry
before `SUCCEEDED` settlement. The certificate also uses the raw-provider
ingress route, reopens worker and identity Postgres independently, keeps the
worker/mail-edge projection payload-free, and removes all reserved rows:
[`gateway_worker_mail_edge_provider_recovery_postgres_evidence.json`](../../mas/docs/provenance/gateway_worker_mail_edge_provider_recovery_postgres_evidence.json).
This is local provider-shaped retry evidence; it does not claim an external
provider outage/restore, callback or delivery confirmation, independent
host/process loss, or gVisor/Firecracker execution.

### Complete governed run-version pinning certificate

[`check_worker_version_pinning_postgres.py`](../../mas/scripts/check_worker_version_pinning_postgres.py)
(`7c1ef74`) extends the earlier shell/adapter/skill-bundle certificate to the
complete local governed run set. It creates two shell, adapter, skill-bundle,
and model-profile versions plus the AIAT steward record, advances the worker's
source/version metadata and active pointers, and persists distinct immutable
model-resolution snapshots on a `RUNNING` version-one run and a queued
version-two run. After a Postgres connection reopen, the version-one run still
points to its original IDs/snapshot while the replacement run points to the
new IDs/snapshot; the single steward identity remains stable for both governed
runs. Evidence is retained at
[`worker_version_pinning_postgres_evidence.json`](../../mas/docs/provenance/worker_version_pinning_postgres_evidence.json),
with payload-free output and zero remaining fixture rows. This is local
control-plane storage evidence; it does not certify independent deployed hosts,
provider execution/recovery, gVisor/Firecracker, or live dispatch.

### Gateway worker HTTP-boundary certificate

[`check_gateway_worker_http_fixture.py`](../../mas/scripts/check_gateway_worker_http_fixture.py)
(`cbbfe56`) drives the real `LLMGatewayClient`, `GatewayWorkerAdapter`, and
`WorkerRunController` through an in-process OpenAI-compatible HTTP transport.
The certificate receives one deterministic `429`, retries once, checks the
AIAT-owned `/v1/chat/completions` path and bearer-secret header, verifies the
bounded model/prompt/generation payload, and reads back a successful terminal
result with exact provider/model usage. Evidence is retained at
[`gateway_worker_http_fixture.json`](../../mas/docs/provenance/gateway_worker_http_fixture.json).
This closes the local HTTP client boundary only; it is not external-provider,
provider-outage, independent-host, gVisor, or Firecracker evidence.

## Tests and operation

Focused unit and checker tests are in
[`test_host_executor.py`](../../mas/packages/mas-core/tests/test_host_executor.py)
and
[`test_check_worker_host_execution_postgres.py`](../../mas/scripts/tests/test_check_worker_host_execution_postgres.py).
Run the bounded checks from `mas/`:

```bash
uv run --isolated ruff check \
  packages/mas-core/mas_core/worker_registry/host_executor.py \
  packages/mas-core/mas_core/worker_registry/run_host_binding.py \
  packages/mas-core/tests/test_host_executor.py \
  scripts/check_worker_host_execution_postgres.py \
  scripts/tests/test_check_worker_host_execution_postgres.py \
  scripts/check_worker_multi_host_execution_postgres.py \
  scripts/tests/test_check_worker_multi_host_execution_postgres.py \
  scripts/check_worker_host_loss_queue_recovery_postgres.py \
  scripts/tests/test_check_worker_host_loss_queue_recovery_postgres.py \
  scripts/check_worker_host_loss_queue_recovery_soak_postgres.py \
  scripts/tests/test_check_worker_host_loss_queue_recovery_soak_postgres.py \
  scripts/check_worker_host_model_resolution_postgres.py \
  scripts/tests/test_check_worker_host_model_resolution_postgres.py
uv run --isolated pytest -q \
  packages/mas-core/tests/test_host_executor.py \
  packages/mas-core/tests/test_run_host_binding.py \
  scripts/tests/test_check_worker_host_execution_postgres.py \
  scripts/tests/test_check_worker_multi_host_execution_postgres.py \
  scripts/tests/test_check_worker_host_loss_queue_recovery_postgres.py \
  scripts/tests/test_check_worker_host_model_resolution_postgres.py
docker exec mas-orchestrator-api-1 python /tmp/check_worker_host_execution_postgres.py --json
docker exec mas-orchestrator-api-1 python /tmp/check_worker_multi_host_execution_postgres.py --json
docker exec mas-orchestrator-api-1 python /tmp/check_worker_host_loss_queue_recovery_postgres.py --json
docker exec mas-orchestrator-api-1 python /tmp/check_worker_host_loss_queue_recovery_soak_postgres.py --json --iterations 3
docker exec mas-orchestrator-api-1 python /tmp/check_worker_host_model_resolution_postgres.py --json
```

The deployed command requires migration `0042_worker_run_host_binding` and a
local Postgres DSN. It is a certification probe, not an automatic production
dispatcher. A production host integration must supply an authenticated host
identity, adapter/runtime selection, sandbox profile, bounded mounts/network,
artifact policy, and recovery policy before it can claim a real run.

## Open boundaries

- Connect the selected model-resolution snapshot to a real provider-backed
  worker without bypassing worker shell, adapter, skill-bundle, steward, model,
  budget, or human-approval controls; the local fixture path is already
  certified.
- Replace the deterministic two-host fixture with two independently deployed
  worker hosts and prove concurrent admission, host loss, split-brain fencing,
  requeue, and duplicate-effect protection under real host/process boundaries;
  the local duplicate-claim and replay boundary is already certified.
- Certify gVisor on supported hosts and independently certify Firecracker for
  high-risk profiles.
- Add provider-backed execution, callback/bounce evidence, outage recovery, and
  restore/rollback exercises.

The canonical resource metadata catalogue remains the only place for third-party
source and licence metadata; this execution contract does not use that metadata
as an admission predicate. See [Third-party notices](../../THIRD_PARTY_NOTICES.md).
