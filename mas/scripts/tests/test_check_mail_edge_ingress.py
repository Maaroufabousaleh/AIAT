from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

MAS_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = MAS_ROOT / "check_mail_edge_ingress.py"


def test_mail_edge_ingress_certification_drives_real_local_asgi_boundary() -> None:
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--json"],
        cwd=MAS_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert report["schema_version"] == "aiat.mail-edge-ingress-certification.v1"
    assert report["status"] == "pass"
    assert report["http_statuses"] == {
        "delivered": 200,
        "bounced": 200,
        "duplicate": 200,
        "conflict": 409,
        "tampered": 401,
    }
    assert report["stored_observation_count"] == 2
    assert report["dashboard_row_count"] == 2
    assert report["payload_free"] is True
    assert report["external_network_access_performed"] is False
    assert report["external_provider_mutation_performed"] is False
    assert report["licence_metadata_is_gate"] is False
