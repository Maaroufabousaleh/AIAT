"""
Conftest for team-runner tests.
"""
import os
import sys
import textwrap
from pathlib import Path
from uuid import uuid4

import pytest

# Ensure the team-runner package is importable.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "packages" / "mas-core"))
sys.path.insert(0, str(ROOT / "packages" / "mas-tools-sdk"))


@pytest.fixture(params=["asyncio"])
def anyio_backend(request):
    """TeamRunner is asyncio-native and uses asyncio task primitives directly."""
    return request.param


@pytest.fixture
def sample_team_yaml():
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
            max_llm_calls: 10
            max_tool_calls: 5
            max_subtasks: 3
            max_cost_usd: 0.25
          tools: []
        workers: []
    """)
    temp_dir = Path(__file__).resolve().parent / "_tmp"
    temp_dir.mkdir(parents=True, exist_ok=True)
    config_file = temp_dir / f"test_team_{uuid4().hex}.yaml"
    config_file.write_text(yaml_content, encoding="utf-8")
    os.environ["TEAM_CONFIG"] = str(config_file)
    try:
        yield str(config_file)
    finally:
        os.environ.pop("TEAM_CONFIG", None)
        if config_file.exists():
            config_file.unlink()
