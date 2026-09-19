# Sandbox Policy Classes and Host Runtimes

This directory holds worker profile definitions and operator notes for the
hardened execution path. AIAT authorizes a policy class; the worker host
selects the concrete runtime.

| AIAT class | Host runtime | Use |
| --- | --- | --- |
| `trusted` | normal Docker/runc | AIAT-owned services and reviewed trusted workers |
| `sandboxed` | gVisor `runsc` | Default external-worker boundary |
| `vm_isolated` | Kata Containers runtime | High-risk or gVisor-incompatible workers; not currently activated |

Compatibility values are still accepted: `standard` and `restricted` resolve
to `trusted`, `gvisor` resolves to `sandboxed`, and `firecracker` resolves to
`vm_isolated`. The direct Firecracker launcher remains a compatibility and
benchmark path, not a fourth AIAT policy tier.

Implemented profile files:
- `tier0-standard.yaml`
- `tier1-restricted.yaml`

Default external-worker execution:
- `command.run_safe`, `security.scan`, and `test.run` delegate worker-controlled
  commands through `TOOL_SANDBOX_COMMAND`.
- The shipped sandbox adapter requires `profile: sandboxed` (or the legacy
  `gvisor` alias) and
  `network_mode: egress-deny-all`.
- The adapter requires Docker to have the `runsc` runtime registered.
- The adapter never falls back to Docker's default `runc` runtime.

VM-isolated execution is selected explicitly with `profile: vm_isolated` and a
host-certified `sandbox_runtime: kata`. It never falls back to `runsc` or
`runc`. The host may choose Kata's VMM/profile (for example Dragonball or QEMU)
without exposing that choice to the worker.

When Docker does not expose `runsc`, these tools should remain unavailable.
That is the expected fail-closed state, not a code defect:

```text
Tool availability: 70/73
command.run_safe: unavailable
security.scan: unavailable
test.run: unavailable
reason: gvisor_runsc_runtime_not_available
no runc fallback
```

## WSL2 operator bootstrap: Docker Engine and gVisor `runsc`

For the supported AIAT development WSL2 profile, run the idempotent host
bootstrap from the repository root. It installs/configures the WSL-local Docker
Engine and pinned gVisor package when needed, registers `runsc` with that
daemon, executes the digest-pinned smoke, starts/migrates local Compose, and
writes [`dev_host_readiness.json`](../../docs/provenance/dev_host_readiness.json):

```bash
bash mas/scripts/bootstrap-dev-host.sh
```

This is an operator/host action. It does not run inside an AIAT application
container and does not expose the Docker socket to workers. It protects an
existing Docker Desktop daemon by using the dedicated `aiat-wsl` context and
daemon socket. On another Linux host, configure the daemon that actually runs
AIAT containers; installing `runsc` somewhere on Windows is not sufficient.

The explicit lower-level installation sequence below is retained as historical
diagnostic reference for a separately managed Linux host; it is not required
for the supported WSL2 development workflow.

```bash
(
  set -e
  ARCH=$(uname -m)
  URL=https://storage.googleapis.com/gvisor/releases/release/latest/${ARCH}
  wget ${URL}/runsc ${URL}/runsc.sha512 \
    ${URL}/containerd-shim-runsc-v1 ${URL}/containerd-shim-runsc-v1.sha512
  sha512sum -c runsc.sha512 \
    -c containerd-shim-runsc-v1.sha512
  rm -f *.sha512
  chmod a+rx runsc containerd-shim-runsc-v1
  sudo mv runsc containerd-shim-runsc-v1 /usr/local/bin
)

sudo /usr/local/bin/runsc install
sudo systemctl restart docker
docker run --rm --runtime=runsc hello-world
```

Optional smoke probes:

```bash
docker info | grep -i runsc || true
docker run --rm --runtime=runsc hello-world
docker run --rm --runtime=runsc -it ubuntu dmesg
```

After registration, rerun the AIAT probes for `command.run_safe`,
`security.scan`, and `test.run`. Expected result:

```text
Tool availability: 73/73
command.run_safe: available
security.scan: available
test.run: available
sandbox_profile: sandboxed
no runc fallback
```

## Kata VM-isolated worker boundary

Kata is a future host capability, not a current release requirement. A host
must advertise the policy class and runtime in its registration metadata:

```yaml
sandbox_profile: vm_isolated
metadata:
  sandbox_runtime: kata
  kata_profile: dragonball
```

The worker adapter emits an OCI command using `--runtime kata`; it does not
install Kata, select a VMM, or claim that a VM boundary is certified. Run the
explicit read-only probe from `mas/` on the certified Linux host when Kata is
being evaluated:

```bash
uv run --isolated python scripts/check_sandbox_runtime_readiness.py \
  --live --require-kata --json
```

Kata requires a supported virtualization environment, commonly hardware
virtualization or approved nested virtualization. The current WSL2/Docker
profile is not a Kata certification environment.

## Direct Firecracker compatibility boundary

Direct Firecracker is retained only as a legacy/experimental host-VMM path,
not as an AIAT policy class. The AIAT-owned launch contract is
implemented by `5ed0a0b` in
`mas_core.worker_registry.firecracker.FirecrackerLaunchSpec` and
`FirecrackerAdapter`. It requires immutable kernel/rootfs digests, bounded
vCPU/memory/PID/disk/output/time limits, read-only rootfs, deny-by-default
egress, opaque secret references, artifact output, and cleanup. The adapter
constructs argv only for an explicitly named certified launcher; Docker, runc,
and gVisor fallback are prohibited.

Run the read-only readiness check from `mas/`:

```bash
uv run --isolated python scripts/check_firecracker_worker_pool.py --live --json
```

The current host result is static-pass/live-blocked because neither
`aiat-firecracker-launcher` nor `firecracker` is installed. No launch, network
probe, mutation, or weaker-runtime fallback is attempted. Retained evidence:
[`firecracker_worker_pool_readiness.json`](../../docs/provenance/firecracker_worker_pool_readiness.json).
If Firecracker is used in the future, prefer it as a Kata host VMM and certify
the `vm_isolated` class; direct launcher activation requires a separate
operator-approved benchmark and evidence package.
