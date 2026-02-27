"""
Conftest for team-runner tests.
"""
import os
import textwrap
import pytest


@pytest.fixture
def sample_team_yaml(tmp_path):
    """Write a minimal team YAML to a temp file and set TEAM_CONFIG env var."""
    yaml_content = textwrap.dedent("""\
        team_id: test_team
        admin:
          agent_id: test_admin
          role: admin
          class: AdminAgent
          display_name: Test Admin
          system_prompt_file: prompts/ceo.md
          budget_defaults:
            max_tokens_per_turn: 1000
            max_turns: 5
            timeout_seconds: 30
          tools: []
        workers: []
    """)
    config_file = tmp_path / "test_team.yaml"
    config_file.write_text(yaml_content)
    os.environ["TEAM_CONFIG"] = str(config_file)
    yield str(config_file)
    del os.environ["TEAM_CONFIG"]
