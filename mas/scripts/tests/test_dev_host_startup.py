"""Regression coverage for the supported WSL Compose startup path."""

from __future__ import annotations

import importlib.util
import os
import re
import subprocess
import sys
from pathlib import Path

import yaml

MAS_ROOT = Path(__file__).resolve().parents[2]
COMPOSE_ROOT = MAS_ROOT / "infra" / "compose"
BOOTSTRAP = MAS_ROOT / "scripts" / "bootstrap-dev-host.sh"
WRAPPER = COMPOSE_ROOT / "mas.sh"
OMNIROUTE_BOOTSTRAP = COMPOSE_ROOT / "configure_omniroute.py"


def _host_port_mappings() -> set[tuple[str, int, str]]:
    mappings: set[tuple[str, int, str]] = set()
    for filename in ("docker-compose.yml", "docker-compose.dev.yml"):
        compose = yaml.safe_load((COMPOSE_ROOT / filename).read_text(encoding="utf-8"))
        for service, config in compose.get("services", {}).items():
            for port in config.get("ports", []) or []:
                if isinstance(port, dict):
                    target = int(str(port["target"]).split("/", maxsplit=1)[0])
                    published = str(port.get("published", ""))
                else:
                    host, target_text = str(port).rsplit(":", maxsplit=1)
                    target = int(target_text.split("/", maxsplit=1)[0])
                    published = host
                variable = re.search(r"\$\{([A-Z][A-Z0-9_]*)", published)
                assert variable, f"{service}:{target} must use a named published-port variable"
                mappings.add((service, target, variable.group(1)))
    return mappings


def test_bootstrap_selects_and_reconciles_every_compose_host_port():
    source = BOOTSTRAP.read_text(encoding="utf-8")
    selected = {
        (service, int(target), variable)
        for variable, service, target in re.findall(
            r"^\s*select_host_port\s+([A-Z][A-Z0-9_]*)\s+\S+\s+([a-z][a-z0-9-]*)\s+(\d+)\s*$",
            source,
            flags=re.MULTILINE,
        )
    }
    reconciled = {
        (service, int(target), variable)
        for service, target, variable in re.findall(
            r'^\s*"([a-z][a-z0-9-]*)\s+(\d+)\s+([A-Z][A-Z0-9_]*)"\s*$',
            source,
            flags=re.MULTILINE,
        )
    }
    mappings = _host_port_mappings()

    assert mappings <= selected
    assert mappings <= reconciled


def test_compose_wrapper_preserves_bootstrap_environment_over_dotenv(tmp_path):
    source = WRAPPER.read_text(encoding="utf-8")
    function = re.search(r"(?ms)^load_env_file\(\) \{.*?^\}", source)
    assert function is not None

    env_file = tmp_path / ".env"
    env_file.write_text(
        "AIAT_OMNIROUTE_BOOTSTRAP=true\n"
        "AIAT_DEV_PROMETHEUS_PORT=9090\n"
        "MAS_LOCAL_TEST_VALUE=from-file\n",
        encoding="utf-8",
    )
    command = (
        function.group(0)
        + '\nload_env_file "$1"\n'
        + 'printf "%s\\n" "$AIAT_OMNIROUTE_BOOTSTRAP" '
        + '"$AIAT_DEV_PROMETHEUS_PORT" "$MAS_LOCAL_TEST_VALUE"\n'
    )
    environment = os.environ.copy()
    environment.update(
        AIAT_OMNIROUTE_BOOTSTRAP="false",
        AIAT_DEV_PROMETHEUS_PORT="18036",
    )
    result = subprocess.run(
        ["bash", "-c", command, "test-wrapper", str(env_file)],
        check=True,
        capture_output=True,
        text=True,
        env=environment,
    )

    assert result.stdout.splitlines() == ["false", "18036", "from-file"]


def test_omniroute_bootstrap_follows_selected_host_port():
    spec = importlib.util.spec_from_file_location("configure_omniroute", OMNIROUTE_BOOTSTRAP)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)

    assert module._bootstrap_base_url({"AIAT_DEV_OMNIROUTE_API_PORT": "18038"}) == "http://127.0.0.1:18038"
    assert module._bootstrap_base_url({}) == "http://127.0.0.1:20128"
    assert module._bootstrap_base_url(
        {
            "AIAT_DEV_OMNIROUTE_API_PORT": "18038",
            "OMNIROUTE_BOOTSTRAP_URL": "http://127.0.0.1:18400/",
        }
    ) == "http://127.0.0.1:18400"
