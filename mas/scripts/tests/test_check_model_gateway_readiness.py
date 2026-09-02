from __future__ import annotations

import importlib.util
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "check_model_gateway_readiness.py"


def _module():
    spec = importlib.util.spec_from_file_location("check_model_gateway_readiness", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _write_config(path: Path, aliases: list[str]) -> None:
    rows = [
        {"model_name": alias, "litellm_params": {"model": "openai/auto"}}
        for alias in aliases
    ]
    path.write_text(f"model_list: {rows!r}\n", encoding="utf-8")


def test_static_alias_contract_passes(tmp_path: Path) -> None:
    config = tmp_path / "litellm.yaml"
    _write_config(config, ["auto", "omniroute-auto", "omniroute-free", "omniroute-coding", "omniroute-smart"])

    report = _module().inspect_static(config_path=config)

    assert report["status"] == "pass"
    assert report["missing_aliases"] == []
    assert report["registry_alias_present"] is True
    assert report["dispatch_performed"] is False


def test_static_alias_contract_fails_closed_on_missing_alias(tmp_path: Path) -> None:
    config = tmp_path / "litellm.yaml"
    _write_config(config, ["auto", "omniroute-coding"])

    report = _module().inspect_static(config_path=config)

    assert report["status"] == "fail"
    assert "omniroute-auto" in report["missing_aliases"]


class _Response:
    def __init__(self, payload: object) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> object:
        return self._payload


def test_live_listing_checks_aliases_without_completion(monkeypatch) -> None:
    seen: dict[str, object] = {}

    def fake_get(url: str, *, headers: dict[str, str], timeout: float) -> _Response:
        seen.update(url=url, headers=headers, timeout=timeout)
        return _Response(
            {"data": [{"id": alias} for alias in ("auto", "omniroute-auto", "omniroute-free", "omniroute-coding", "omniroute-smart")]}
        )

    module = _module()
    monkeypatch.setattr(module.httpx, "get", fake_get)
    report = module.inspect_live(url="http://gateway", api_key="secret", timeout=3.0)

    assert report["status"] == "pass"
    assert report["model_count"] == 5
    assert report["provider_call_performed"] is False
    assert seen == {
        "url": "http://gateway/v1/models",
        "headers": {"Authorization": "Bearer secret"},
        "timeout": 3.0,
    }
