from __future__ import annotations

import json
import logging

from mas_core.observability.logging import configure_logging


def test_stdlib_extra_context_is_rendered_for_trace_search(capsys):
    configure_logging("test-service")

    logging.getLogger("trace-test").info(
        "trace_probe",
        extra={
            "trace_id": "corr-1",
            "span_id": "msg-1",
            "agent_id": "agent-1",
            "team_id": "team-1",
            "project_id": "project-1",
        },
    )

    event = json.loads(capsys.readouterr().out)
    assert event["trace_id"] == "corr-1"
    assert event["span_id"] == "msg-1"
    assert event["agent_id"] == "agent-1"
    assert event["team_id"] == "team-1"
    assert event["project_id"] == "project-1"
