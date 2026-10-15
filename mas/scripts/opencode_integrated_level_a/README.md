# Isolated OpenCode integrated Level A profile

This profile composes an isolated, disposable AIAT control plane with the real
Orchestrator API, a dedicated local host consumer, migrated Postgres, Redis
Streams/router, Tool Service, MinIO artifact storage, and the pinned OpenCode
runtime. The only model route is the deterministic local fixture carried over
from the existing OpenCode Level A harness.

It is separate from production/default Compose. It does not import `.env`, use
the running AIAT database or Tool Service, publish service ports, or make a
public provider request. Control and sandbox networks are both Docker
`internal` networks. OpenCode is configured with `runsc` and can reach only
the sandbox peers; Postgres, Redis, and MinIO stay on the control network.

The dedicated `worker-host` service owns the existing local host queue runtime.
It is a trusted, elevated Level A boundary: access to the Docker socket is
effectively host-authoritative even though the process runs as a non-root UID.
Only `worker-host` receives the socket. It joins the internal control network
and sandbox gateway so the canonical OpenCode adapter can reach the local
OpenCode runtime; both networks have external egress disabled. The
orchestrator API, Tool Service, router, and spawned worker containers remain
socket-free; OCI workers use `--network none` and do not inherit the host
process environment. Do not reuse this host service design as a production
deployment without a separate host security review. The profile keeps the
checked-in OpenCode worker approval gate unchanged. A queued run still needs
normal AIAT worker, model, project, and budget governance.

The gateway master key is generated per profile and shared only with the
local gateway clients. The fixture provider key is separate and remains
confined to the deterministic provider fixture route.

## Commands

Render the Compose file and enforce the profile's isolation invariants without
creating containers:

```bash
python3 mas/scripts/opencode_integrated_level_a/run.py --check-only
```

Build and start the isolated stack, run migrations, verify health, then tear
down this generated Compose project and its disposable volumes:

```bash
python3 mas/scripts/opencode_integrated_level_a/run.py --stack-only
```

Run the API acceptance driver after the stack is healthy. The default driver
is `mas/scripts/opencode_vertical_e2e.py`; the profile copies it into the
isolated Orchestrator container, runs it with the local API URL and ephemeral
operator key, then removes the copied script before project-scoped teardown.

```bash
python3 mas/scripts/opencode_integrated_level_a/run.py
```

The runner generates one unique Compose project name, private non-overlapping
sandbox subnet, temporary mode-0600 env file, and ephemeral test-only service
credentials per invocation. It never accepts a user-supplied Compose project
name. It also assigns a matching unique local image namespace, checks for tag
collisions before building, and verifies that project containers, networks,
volumes, and local images are gone after cleanup. It does not invoke or modify
the default `mas` project.

For builds, it stages only the source paths consumed by the existing pinned
Dockerfiles into a temporary context. This keeps unrelated or permission-
restricted developer temp data out of BuildKit's context walk. Worker identity
is resolved from the isolated API's authenticated worker list; the profile
does not assign IDs or alter worker approval state.

## Interpretation

`--stack-only` proves that the isolated services, migrations, local host
registration/consumer startup, and runtime health checks succeed. It does not
prove a worker run. The API driver is responsible for classifying admission
and lifecycle outcomes. In particular, the default `coding_worker` and
`tester` manifests currently retain their pending OpenCode security-review
gate; this profile does not mark them approved, alter their records, or weaken
production activation policy. If that gate prevents a real WorkerRun, report
the run as blocked at worker admission rather than treating service startup as
the integrated worker certificate.

The profile is a development integration check, not native-Linux release,
production image, KMS, or provider certification. It requires Docker Compose
v2, a registered `runsc` runtime, locally available digest-pinned base
services, and the local LiteLLM image. The image build may fetch pinned source
packages from their build registries, but no running service has external
network access and no provider credential is supplied.
