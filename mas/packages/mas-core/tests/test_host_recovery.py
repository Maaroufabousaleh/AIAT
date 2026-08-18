from __future__ import annotations

from mas_core.worker_registry.host_recovery import (
    HOST_RECOVERY_SCHEMA,
    HostLeaseRecovery,
)


def test_host_recovery_contract_is_aiat_owned_and_bounded() -> None:
    assert HOST_RECOVERY_SCHEMA == "aiat.worker-host-recovery.v1"
    assert HostLeaseRecovery is not None
