"""Smoke tests for team-runner."""
from pathlib import Path
from uuid import uuid4

import pytest

yaml = pytest.importorskip("yaml")
from team_runner.main import load_team_config  # noqa: E402  (yaml import guard above)


def _write_team_yaml(content: str) -> Path:
    temp_dir = Path(__file__).resolve().parent / "_tmp"
    temp_dir.mkdir(parents=True, exist_ok=True)
    path = temp_dir / f"team_{uuid4().hex}.yaml"
    path.write_text(content, encoding="utf-8")
    return path


def test_sample_team_yaml_loads(sample_team_yaml):
    """Fixture YAML must parse without errors."""
    with open(sample_team_yaml) as f:
        config = yaml.safe_load(f)
    assert config["team_id"] == "test_team"
    assert "admin" in config
    assert "workers" in config


def test_team_yaml_has_required_keys(sample_team_yaml):
    with open(sample_team_yaml) as f:
        config = yaml.safe_load(f)
    admin = config["admin"]
    for key in ("agent_id", "role", "class", "display_name",
                "system_prompt_file", "budget_defaults", "tools"):
        assert key in admin, f"Missing key: {key}"


def test_team_tools_are_normalized_to_canonical_names():
    content = """\
team_id: test_team
admin:
  agent_id: admin_1
  role: admin
  class: AdminAgent
  display_name: Admin
  system_prompt_file: prompts/ceo.md
  budget_defaults: {}
  tools:
    - document_create
    - review_aggregate
    - project.status
workers: []
"""
    path = _write_team_yaml(content)
    try:
        cfg = load_team_config(path)
        assert cfg.admin.tools == [
            "document.create_draft",
            "review.aggregate",
            "project.status",
        ]
    finally:
        if path.exists():
            path.unlink()


def test_team_tools_fail_fast_on_unknown_name():
    content = """\
team_id: test_team
admin:
  agent_id: admin_1
  role: admin
  class: AdminAgent
  display_name: Admin
  system_prompt_file: prompts/ceo.md
  budget_defaults: {}
  tools:
    - definitely.not_a_real_tool
workers: []
"""
    path = _write_team_yaml(content)
    try:
        with pytest.raises(ValueError, match="Unknown tool"):
            load_team_config(path)
    finally:
        if path.exists():
            path.unlink()


def test_exec_ceo_declares_full_operator_workflow_tool_surface():
    repo_mas_root = Path(__file__).resolve().parents[3]
    cfg = load_team_config(repo_mas_root / "teams" / "exec_ceo.yaml")

    expected = {
        "project.create",
        "project.status",
        "project.transition",
        "project.list",
        "flow.list",
        "flow.recommend",
        "flow.assign",
        "flow.invoke",
        "flow.status",
        "flow.advance",
        "human.notify",
        "human.await_decision",
        "review.aggregate",
        "approval.override_cso",
    }
    assert expected.issubset(set(cfg.admin.tools))


def test_production_team_config_reconciles_mounted_worker_manifests():
    mas_root = Path(__file__).resolve().parents[3]
    cfg = load_team_config(
        mas_root / "teams" / "exec_ceo.yaml",
        workers_dir=mas_root / "workers",
    )

    assert cfg.admin.worker_manifest_ref == "ceo"
    assert all(worker.worker_manifest_ref == worker.agent_id for worker in cfg.workers)


def test_startup_manifest_reconciliation_fails_closed(tmp_path: Path):
    teams_dir = tmp_path / "teams"
    workers_dir = tmp_path / "workers"
    teams_dir.mkdir()
    workers_dir.mkdir()
    (workers_dir / "known.yaml").write_text("metadata:\n  id: known\n", encoding="utf-8")
    config_path = teams_dir / "team.yaml"
    config_path.write_text(
        """\
team_id: fixture
admin:
  agent_id: admin
  role: admin
  class: AdminAgent
  display_name: Admin
  budget_defaults: {}
  tools: []
workers: []
""",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="worker-manifest reconciliation failed"):
        load_team_config(config_path, workers_dir=workers_dir)


def test_agent_config_retains_explicit_worker_manifest_reference():
    from team_runner.main import RunnerSettings, TeamRuntime

    config_path = _write_team_yaml(
        """\
team_id: fixture
admin:
  agent_id: known
  worker_manifest_ref: known
  role: worker
  class: WorkerAgent
  display_name: Known Worker
  budget_defaults: {}
  tools: []
workers: []
"""
    )
    try:
        config = load_team_config(config_path)
        runtime = TeamRuntime(
            RunnerSettings(team_config_path=config_path, router_secret="secret"),
            config,
        )
        agent = runtime._build_agent(config.admin)
        assert agent.config.worker_manifest_ref == "known"
        assert runtime.health_payload()["agent_worker_manifest_refs"] == {"known": "known"}
    finally:
        if config_path.exists():
            config_path.unlink()


def test_prompt_time_block_uses_configured_company_timezone():
    from team_runner.main import TeamRuntime

    rendered = TeamRuntime._prepend_time_block("prompt body", "America/Toronto")

    assert rendered.startswith("## Current Time (America/Toronto)\n")
    assert "company-timezone reference" in rendered
    assert "prompt body" in rendered
    assert "America/New_York" not in rendered


def test_prompt_time_block_falls_back_to_utc_for_invalid_timezone():
    from team_runner.main import TeamRuntime

    rendered = TeamRuntime._prepend_time_block("prompt body", "Not/AZone")

    assert rendered.startswith("## Current Time (UTC)\n")
