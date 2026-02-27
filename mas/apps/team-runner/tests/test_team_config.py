"""Smoke tests for team-runner."""
import pytest

yaml = pytest.importorskip("yaml")


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
