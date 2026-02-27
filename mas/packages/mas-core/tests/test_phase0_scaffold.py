"""Phase 0 scaffold checks against plan-masArchitectureUpgrade.prompt.md."""

from __future__ import annotations

from pathlib import Path
import tomllib


def _repo_root() -> Path:
    # .../mas/packages/mas-core/tests/test_phase0_scaffold.py -> .../mas
    return Path(__file__).resolve().parents[3]


def test_required_phase0_directories_exist() -> None:
    root = _repo_root()
    required = [
        "apps/orchestrator-api",
        "apps/team-runner",
        "apps/message-router",
        "apps/tool-service",
        "packages/mas-core",
        "packages/mas-tools-sdk",
        "packages/mas-core/workflow",
        "teams",
        "infra/docker",
        "infra/compose",
        "migrations",
    ]
    missing = [rel for rel in required if not (root / rel).exists()]
    assert not missing, f"Missing Phase 0 scaffold paths: {missing}"


def test_phase0_team_registry_has_exact_11_teams() -> None:
    root = _repo_root()
    expected = {
        "exec_ceo",
        "exec_coo",
        "office_cfo",
        "office_cio",
        "office_chrm",
        "office_cso",
        "office_cto",
        "dept_production",
        "dept_system",
        "dept_qa",
        "dept_devops",
    }
    actual = {p.stem for p in (root / "teams").glob("*.yaml")}
    assert actual == expected, f"Team YAML mismatch: missing={expected - actual}, extra={actual - expected}"


def test_phase0_workflow_scaffold_files_exist() -> None:
    root = _repo_root()
    required = [
        "packages/mas-core/workflow/README.md",
        "packages/mas-core/mas_core/workflow/__init__.py",
        "packages/mas-core/mas_core/workflow/controller.py",
        "packages/mas-core/mas_core/workflow/events.py",
        "packages/mas-core/mas_core/workflow/states.py",
        "packages/mas-core/mas_core/workflow/transitions.py",
        "packages/mas-core/mas_core/workflow/watchdog.py",
    ]
    missing = [rel for rel in required if not (root / rel).exists()]
    assert not missing, f"Missing workflow scaffold files: {missing}"


def test_phase0_workspace_includes_local_core_packages() -> None:
    root = _repo_root()
    pyproject = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    members = set(pyproject["tool"]["uv"]["workspace"]["members"])
    assert "packages/mas-core" in members
    assert "packages/mas-tools-sdk" in members


def test_phase0_expected_docker_and_compose_files_exist() -> None:
    root = _repo_root()
    expected = [
        "infra/docker/Dockerfile.orchestrator-api",
        "infra/docker/Dockerfile.team-runner",
        "infra/docker/Dockerfile.message-router",
        "infra/docker/Dockerfile.tool-service",
        "infra/compose/docker-compose.yml",
        "infra/compose/docker-compose.dev.yml",
    ]
    missing = [rel for rel in expected if not (root / rel).is_file()]
    assert not missing, f"Missing Phase 0 infra files: {missing}"


def test_phase0_migration_scaffold_files_exist() -> None:
    root = _repo_root()
    expected = [
        "alembic.ini",
        "migrations/env.py",
        "migrations/script.py.mako",
    ]
    missing = [rel for rel in expected if not (root / rel).is_file()]
    assert not missing, f"Missing migration scaffold files: {missing}"
