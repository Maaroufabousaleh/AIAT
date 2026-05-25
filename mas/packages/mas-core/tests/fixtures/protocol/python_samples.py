"""Python protocol fixture samples for cross-runtime validation tests."""

MESSAGE_ENVELOPE_SAMPLE = {
    "protocol_version": "aiat.v1",
    "msg_type": "TASK",
    "sender_id": "ceo_agent",
    "sender_role": "orchestrator",
    "sender_team": "exec_ceo",
    "recipient_id": "worker_alpha",
    "project_id": "proj-alpha",
    "payload": {"task": "validate_contract"},
}

TOOL_REQUEST_SAMPLE = {
    "protocol_version": "aiat.v1",
    "agent_id": "ceo_agent",
    "sender_role": "orchestrator",
    "sender_team": "exec_ceo",
    "project_id": "proj-alpha",
    "tool_name": "project.transition",
    "kwargs": {"event": "project_created"},
}

TOOL_RESPONSE_SAMPLE = {
    "protocol_version": "aiat.v1",
    "tool_name": "project.transition",
    "success": True,
    "result": {"state": "FEASIBILITY_CHECK"},
}

WORKER_MANIFEST_SAMPLE = {
    "protocol_version": "aiat.v1",
    "metadata": {
        "id": "worker_alpha",
        "name": "Worker Alpha",
        "version": "1.0.0",
        "source_repo": "https://github.com/example/worker-alpha",
        "version_pin": "v1.0.0",
    },
    "runtime": {
        "transport": "process",
        "adapter_config": {"command": "python -m worker_alpha"},
    },
    "capabilities": [{"name": "validate_contract", "risk_level": "low"}],
    "sandbox": {
        "profile": "restricted",
        "network_mode": "egress-allowlist",
        "egress_allowlist": ["api.github.com"],
    },
}
