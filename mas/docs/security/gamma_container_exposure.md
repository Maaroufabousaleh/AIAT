# Gamma Container Exposure Audit

Gamma keeps AIAT as the control plane, so container access is limited to
operator observability rather than worker execution.

## Current Docker Socket Use

`infra/compose/docker-compose.yml` mounts `/var/run/docker.sock` only into the
`dashboard` service, and the mount is read-only:

```yaml
volumes:
  - /var/run/docker.sock:/var/run/docker.sock:ro
```

The dashboard uses that socket for the operator-facing container log viewer. No
runner, worker, API, router, or tool-service container should receive a Docker
socket mount in Gamma.

## Risk

Even read-only Docker socket access can expose host/container metadata. It must
not be propagated into worker runtimes or nested-container execution paths.
Firecracker, gVisor, OCI runtime launchers, and broader runtime isolation remain
deferred beyond Gamma.

## Enforced Policy

The orchestrator test suite includes a static compose policy check:

- exactly one service may mount `/var/run/docker.sock`
- that service must be `dashboard`
- the mount must be read-only

If Gamma later adds a safer log proxy, this exception should be removed and the
test changed to reject Docker socket mounts entirely.
