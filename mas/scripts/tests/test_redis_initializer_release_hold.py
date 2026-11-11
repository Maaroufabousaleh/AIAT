"""The temporary release hold must stop lifecycle commands before Docker runs."""
import os
from pathlib import Path
import shutil
import subprocess

MAS_ROOT = Path(__file__).resolve().parents[2]


def test_compose_entrypoint_is_a_fail_closed_hold():
    import yaml
    data = yaml.safe_load((MAS_ROOT / "infra/compose/docker-compose.yml").read_text())
    entrypoint = data["services"]["redis-acl-init"]["entrypoint"]
    assert entrypoint[:2] == ["/bin/sh", "-c"]
    result = subprocess.run(entrypoint, capture_output=True, text=True, timeout=5)
    assert result.returncode == 78


def test_lifecycle_wrappers_refuse_activation_without_calling_docker(tmp_path):
    fakebin = tmp_path / "bin"
    fakebin.mkdir()
    marker = tmp_path / "docker-was-called"
    fake = fakebin / "docker"
    fake.write_text('#!/bin/sh\ntouch "$DOCKER_MARKER"\n')
    fake.chmod(0o755)
    env = dict(os.environ, PATH=f"{fakebin}:{os.environ['PATH']}", DOCKER_MARKER=str(marker))
    root = tmp_path / "workspace"
    compose = root / "mas/infra/compose"
    compose.mkdir(parents=True)
    wrapper = compose / "mas.sh"
    shutil.copyfile(MAS_ROOT / "infra/compose/mas.sh", wrapper)
    for args in (["up"], ["start"], ["restart", "redis-acl-init"], ["migrate"]):
        result = subprocess.run(["bash", str(wrapper), *args], env=env, capture_output=True, text=True, timeout=5)
        assert result.returncode == 78
    for script, args in (("masctl", ["redis-acl-init", "start"]), ("masctl-all", ["restart"])):
        result = subprocess.run(["bash", str(MAS_ROOT / "infra/systemd" / script), *args], env=env, capture_output=True, text=True, timeout=5)
        assert result.returncode == 78
    assert not marker.exists()
