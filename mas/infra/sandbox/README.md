# Sandbox Profiles

This directory holds worker sandbox profile definitions and operator notes for
the hardened worker execution path.

Implemented profile files:
- `tier0-standard.yaml`
- `tier1-restricted.yaml`

Default hardened worker execution:
- `command.run_safe`, `security.scan`, and `test.run` delegate worker-controlled
  commands through `TOOL_SANDBOX_COMMAND`.
- The shipped sandbox adapter requires `profile: gvisor` and
  `network_mode: egress-deny-all`.
- The adapter requires Docker to have the `runsc` runtime registered.
- The adapter never falls back to Docker's default `runc` runtime.

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

## Operator Task: Register gVisor `runsc`

Run this on the Linux Docker host that actually runs AIAT containers. For
Docker Desktop on Windows, installing `runsc` somewhere on Windows is not
enough; the runtime has to be registered inside the Linux Docker daemon
environment.

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
sandbox_profile: gvisor
no runc fallback
```

Optional higher-isolation mode:
- Firecracker remains a future/high-risk-worker option. It should not replace
  the default gVisor gate for these default tools.
