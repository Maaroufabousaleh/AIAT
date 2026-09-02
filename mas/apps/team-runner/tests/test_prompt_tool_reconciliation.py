import importlib.util
from pathlib import Path


def test_authority_prompts_match_runtime_tool_manifest():
    mas_root = Path(__file__).resolve().parents[3]
    script_path = mas_root / "scripts" / "check_prompt_tool_reconciliation.py"
    spec = importlib.util.spec_from_file_location("prompt_reconciliation", script_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    report = module.reconcile(mas_root / "prompts")
    assert report["status"] == "pass", report["errors"]
    assert report["prompt_count"] == 11
    assert report["manifest_tool_count"] == report["registered_tool_count"]
