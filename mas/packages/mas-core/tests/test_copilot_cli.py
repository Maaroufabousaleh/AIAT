"""Tests for the Copilot CLI model scanner and flag-based CLI execution.

Test classes
------------
TestCopilotModelScanner     — discover, filter, register, background scan
TestCopilotCLIExecution     — flag-mode _call_cli (prompt via -p, model via --model)
TestCopilotCostMap          — COPILOT_COST_MAP integrity
TestCopilotExports          — public API exports from llm_gateway
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from mas_core.llm_gateway.client import LLMGatewayClient, LLMGatewayError
from mas_core.llm_gateway.models import LLMConfig
from mas_core.llm_gateway.providers import (
    ApiStyle,
    ModelEntry,
    ModelRegistry,
)
from mas_core.llm_gateway.providers.cli.copilot import (
    COPILOT_COST_MAP,
    COPILOT_PROVIDER,
    CopilotModelScanner,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

# Realistic `copilot --help` output (model choices section)
FAKE_HELP_OUTPUT = """\
Usage: copilot [options] [command]

GitHub Copilot CLI - An AI-powered coding assistant.

Options:
  --model <model>                     Set the AI model to use (choices:
                                      "claude-sonnet-4.6", "claude-sonnet-4.5",
                                      "claude-haiku-4.5", "claude-opus-4.6",
                                      "claude-opus-4.6-fast", "claude-opus-4.5",
                                      "claude-sonnet-4", "gemini-3-pro-preview",
                                      "gpt-5.3-codex", "gpt-5.2-codex",
                                      "gpt-5.2", "gpt-5.1-codex-max",
                                      "gpt-5.1-codex", "gpt-5.1",
                                      "gpt-5.1-codex-mini", "gpt-5-mini",
                                      "gpt-4.1")
  -p, --prompt <text>                 Execute a prompt in non-interactive mode
  -s, --silent                        Output only the agent response
  -h, --help                          display help for command
"""


def _fresh_registry() -> ModelRegistry:
    """Build a clean registry with no models."""
    return ModelRegistry()


def _make_config(**overrides: Any) -> LLMConfig:
    defaults = dict(
        gateway_url="http://fake-llm:8080",
        default_model="gpt-4o",
        api_key="test-key",
        max_retries=1,
        retry_min_wait_s=0.001,
        retry_max_wait_s=0.005,
        timeout_s=5.0,
    )
    defaults.update(overrides)
    return LLMConfig.model_construct(**defaults)


# ---------------------------------------------------------------------------
# Helper to mock subprocess for discover_models
# ---------------------------------------------------------------------------


def _mock_help_subprocess(help_text: str = FAKE_HELP_OUTPUT, returncode: int = 0):
    """Return a patcher that mocks create_subprocess_exec for copilot --help."""
    mock_proc = AsyncMock()
    mock_proc.communicate = AsyncMock(
        return_value=(help_text.encode("utf-8"), b"")
    )
    mock_proc.returncode = returncode
    return patch(
        "asyncio.create_subprocess_exec",
        return_value=mock_proc,
    )


# ===========================================================================
# TestCopilotCostMap
# ===========================================================================


class TestCopilotCostMap:
    def test_free_models_present(self):
        assert COPILOT_COST_MAP["gpt-5-mini"] == 0.0
        assert COPILOT_COST_MAP["gpt-4.1"] == 0.0

    def test_paid_models_positive(self):
        assert COPILOT_COST_MAP["claude-sonnet-4.6"] > 0
        assert COPILOT_COST_MAP["claude-opus-4.6"] > 0

    def test_all_values_are_floats(self):
        for mid, cost in COPILOT_COST_MAP.items():
            assert isinstance(cost, (int, float)), f"{mid}: {cost}"
            assert cost >= 0, f"{mid}: negative cost {cost}"


# ===========================================================================
# TestCopilotModelScanner
# ===========================================================================


class TestCopilotModelScanner:
    @pytest.mark.asyncio
    async def test_discover_models_parses_help(self):
        reg = _fresh_registry()
        scanner = CopilotModelScanner(registry=reg, binary="copilot")

        with (
            patch.object(scanner, "find_binary", return_value="/usr/bin/copilot"),
            _mock_help_subprocess(),
        ):
            models = await scanner.discover_models()

        assert "gpt-5-mini" in models
        assert "gpt-4.1" in models
        assert "claude-sonnet-4.6" in models
        assert len(models) == 17  # all models from the help text

    @pytest.mark.asyncio
    async def test_discover_models_binary_not_found(self):
        reg = _fresh_registry()
        scanner = CopilotModelScanner(registry=reg, binary="nonexistent")

        with patch.object(scanner, "find_binary", return_value=None):
            models = await scanner.discover_models()

        assert models == []

    @pytest.mark.asyncio
    async def test_discover_models_help_parse_failure(self):
        reg = _fresh_registry()
        scanner = CopilotModelScanner(registry=reg, binary="copilot")

        with (
            patch.object(scanner, "find_binary", return_value="/usr/bin/copilot"),
            _mock_help_subprocess("Usage: copilot [options]\n  No model info here.\n"),
        ):
            models = await scanner.discover_models()

        assert models == []

    def test_filter_free_models(self):
        scanner = CopilotModelScanner()
        all_ids = list(COPILOT_COST_MAP.keys())
        free = scanner.filter_free_models(all_ids)
        assert "gpt-5-mini" in free
        assert "gpt-4.1" in free
        assert "claude-sonnet-4.6" not in free
        assert "claude-opus-4.6" not in free

    def test_filter_free_models_unknown_skipped(self):
        scanner = CopilotModelScanner()
        free = scanner.filter_free_models(["gpt-5-mini", "unknown-model-xyz"])
        assert free == ["gpt-5-mini"]

    def test_filter_excludes_all_premium_models(self):
        """Premium models (cost > 0) must never be registered, regardless of value."""
        scanner = CopilotModelScanner()
        free = scanner.filter_free_models(list(COPILOT_COST_MAP.keys()))
        assert "gpt-5-mini" in free
        assert "gpt-4.1" in free
        # All non-zero cost models must be excluded
        assert "claude-haiku-4.5" not in free   # 0.33×
        assert "gpt-5.1-codex-mini" not in free  # 0.33×
        assert "claude-sonnet-4.6" not in free   # 1.0×
        assert "claude-opus-4.6-fast" not in free  # 30×

    @pytest.mark.asyncio
    async def test_scan_and_register(self):
        reg = _fresh_registry()
        scanner = CopilotModelScanner(registry=reg, binary="copilot")

        with (
            patch.object(scanner, "find_binary", return_value="/usr/bin/copilot"),
            _mock_help_subprocess(),
        ):
            entries = await scanner.scan_and_register()

        assert len(entries) == 2
        ids = {e.model_id for e in entries}
        assert "copilot/gpt-5-mini" in ids
        assert "copilot/gpt-4.1" in ids

        # Verify they're in the registry
        assert reg.get("copilot/gpt-5-mini") is not None
        assert reg.get("copilot/gpt-4.1") is not None

        # Verify provider was registered
        assert reg.get_provider("copilot") is not None

    @pytest.mark.asyncio
    async def test_scan_and_register_no_free_models(self):
        reg = _fresh_registry()
        scanner = CopilotModelScanner(registry=reg, binary="copilot")

        # Only return paid models
        help_text = (
            'Options:\n  --model <model>  Set the AI model (choices: "claude-opus-4.6")\n'
        )
        with (
            patch.object(scanner, "find_binary", return_value="/usr/bin/copilot"),
            _mock_help_subprocess(help_text),
        ):
            entries = await scanner.scan_and_register()

        assert entries == []

    @pytest.mark.asyncio
    async def test_scan_idempotent(self):
        """Rescanning overwrites existing entries without duplicates."""
        reg = _fresh_registry()
        scanner = CopilotModelScanner(registry=reg, binary="copilot")

        with (
            patch.object(scanner, "find_binary", return_value="/usr/bin/copilot"),
            _mock_help_subprocess(),
        ):
            await scanner.scan_and_register()
            await scanner.scan_and_register()  # second scan

        copilot_models = reg.list_models("copilot")
        assert len(copilot_models) == 2

    def test_register_known_free_models_sync(self):
        reg = _fresh_registry()
        scanner = CopilotModelScanner(registry=reg)

        with patch.object(scanner, "find_binary", return_value="/usr/bin/copilot"):
            entries = scanner.register_known_free_models()

        assert len(entries) == 2
        ids = {e.model_id for e in entries}
        assert "copilot/gpt-5-mini" in ids
        assert "copilot/gpt-4.1" in ids

    @pytest.mark.asyncio
    async def test_scan_entry_structure(self):
        """Verify the structure of a registered copilot model entry."""
        reg = _fresh_registry()
        scanner = CopilotModelScanner(registry=reg, binary="copilot")

        with (
            patch.object(scanner, "find_binary", return_value="/usr/bin/copilot"),
            _mock_help_subprocess(),
        ):
            await scanner.scan_and_register()

        entry = reg.get("copilot/gpt-5-mini")
        assert entry is not None
        assert entry.api_style == ApiStyle.CLI
        assert entry.provider == "copilot"
        assert entry.endpoint == "/usr/bin/copilot"
        assert entry.cli_prompt_flag == "-p"
        assert entry.cli_model_flag == "--model"
        assert entry.extra["cli_model_name"] == "gpt-5-mini"
        assert entry.cost_per_1m_input == 0.0
        assert entry.cost_per_1m_output == 0.0
        assert entry.supports_tools is False
        assert "-s" in entry.cli_args
        assert "--no-ask-user" in entry.cli_args
        assert "--no-auto-update" in entry.cli_args


# ===========================================================================
# TestCopilotBackgroundScan
# ===========================================================================


class TestCopilotBackgroundScan:
    @pytest.mark.asyncio
    async def test_start_and_stop_background_scan(self):
        reg = _fresh_registry()
        scanner = CopilotModelScanner(registry=reg, binary="copilot", scan_interval=0.05)

        scan_count = 0
        original_scan = scanner.scan_and_register

        async def counting_scan():
            nonlocal scan_count
            scan_count += 1
            return []

        scanner.scan_and_register = counting_scan  # type: ignore[method-assign]

        await scanner.start_background_scan(interval=0.05)
        await asyncio.sleep(0.15)
        await scanner.stop_background_scan()

        assert scan_count >= 2  # Should have run at least twice in 0.15s

    @pytest.mark.asyncio
    async def test_double_start_ignored(self):
        scanner = CopilotModelScanner(scan_interval=10)
        scanner.scan_and_register = AsyncMock(return_value=[])  # type: ignore[method-assign]

        await scanner.start_background_scan()
        first_task = scanner._scan_task
        await scanner.start_background_scan()  # should be ignored
        assert scanner._scan_task is first_task

        await scanner.stop_background_scan()


# ===========================================================================
# TestCopilotCLIExecution — flag-mode _call_cli
# ===========================================================================


class TestCopilotCLIExecution:
    def _make_copilot_entry(self, model_id: str = "copilot/gpt-5-mini") -> ModelEntry:
        return ModelEntry(
            model_id=model_id,
            provider="copilot",
            api_style=ApiStyle.CLI,
            endpoint="copilot",
            cli_args=["-s", "--no-ask-user", "--no-auto-update"],
            cli_prompt_flag="-p",
            cli_model_flag="--model",
            supports_tools=False,
            supports_streaming=False,
            cost_per_1m_input=0.0,
            cost_per_1m_output=0.0,
            extra={"cli_model_name": "gpt-5-mini"},
        )

    def _make_registry_with_copilot(self) -> ModelRegistry:
        reg = ModelRegistry()
        reg.register_provider(COPILOT_PROVIDER)
        reg.register(self._make_copilot_entry())
        return reg

    @pytest.mark.asyncio
    async def test_copilot_cli_uses_prompt_flag_not_stdin(self):
        """copilot-style models pass prompt via -p flag, not stdin."""
        reg = self._make_registry_with_copilot()
        config = _make_config()
        client = LLMGatewayClient(config, registry=reg)

        captured_cmd: list[str] = []
        captured_stdin: bytes | None = b"SENTINEL"

        mock_proc = AsyncMock()
        mock_proc.communicate = AsyncMock(return_value=(b"hello", b""))
        mock_proc.returncode = 0

        async def mock_exec(*args, stdin=None, stdout=None, stderr=None):
            nonlocal captured_cmd, captured_stdin
            captured_cmd = list(args)
            captured_stdin = b"stdin_used" if stdin is not None else None
            return mock_proc

        with patch("asyncio.create_subprocess_exec", side_effect=mock_exec):
            async with client:
                resp = await client.chat_completion(
                    [{"role": "user", "content": "hello world"}],
                    model="copilot/gpt-5-mini",
                )

        assert resp.text == "hello"
        assert resp.model == "copilot/gpt-5-mini"

        # Verify stdin was NOT used (flag mode)
        assert captured_stdin is None

        # Verify -p flag with prompt was in the command
        assert "-p" in captured_cmd
        p_idx = captured_cmd.index("-p")
        assert "[User] hello world" in captured_cmd[p_idx + 1]

        # Verify --model flag with native model name
        assert "--model" in captured_cmd
        m_idx = captured_cmd.index("--model")
        assert captured_cmd[m_idx + 1] == "gpt-5-mini"

    @pytest.mark.asyncio
    async def test_copilot_cli_includes_base_args(self):
        reg = self._make_registry_with_copilot()
        config = _make_config()
        client = LLMGatewayClient(config, registry=reg)

        captured_cmd: list[str] = []

        mock_proc = AsyncMock()
        mock_proc.communicate = AsyncMock(return_value=(b"ok", b""))
        mock_proc.returncode = 0

        async def mock_exec(*args, **kwargs):
            nonlocal captured_cmd
            captured_cmd = list(args)
            return mock_proc

        with patch("asyncio.create_subprocess_exec", side_effect=mock_exec):
            async with client:
                await client.chat_completion(
                    [{"role": "user", "content": "test"}],
                    model="copilot/gpt-5-mini",
                )

        assert "-s" in captured_cmd
        assert "--no-ask-user" in captured_cmd
        assert "--no-auto-update" in captured_cmd

    @pytest.mark.asyncio
    async def test_copilot_cli_multi_message_prompt(self):
        """System + user messages are formatted correctly in the prompt."""
        reg = self._make_registry_with_copilot()
        config = _make_config()
        client = LLMGatewayClient(config, registry=reg)

        captured_cmd: list[str] = []

        mock_proc = AsyncMock()
        mock_proc.communicate = AsyncMock(return_value=(b"response", b""))
        mock_proc.returncode = 0

        async def mock_exec(*args, **kwargs):
            nonlocal captured_cmd
            captured_cmd = list(args)
            return mock_proc

        with patch("asyncio.create_subprocess_exec", side_effect=mock_exec):
            async with client:
                await client.chat_completion(
                    [
                        {"role": "system", "content": "You are a reviewer."},
                        {"role": "user", "content": "Review this code."},
                    ],
                    model="copilot/gpt-5-mini",
                )

        p_idx = captured_cmd.index("-p")
        prompt = captured_cmd[p_idx + 1]
        assert "[System] You are a reviewer." in prompt
        assert "[User] Review this code." in prompt

    @pytest.mark.asyncio
    async def test_copilot_cli_failure(self):
        reg = self._make_registry_with_copilot()
        config = _make_config()
        client = LLMGatewayClient(config, registry=reg)

        mock_proc = AsyncMock()
        mock_proc.communicate = AsyncMock(return_value=(b"", b"auth failed"))
        mock_proc.returncode = 1

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            async with client:
                with pytest.raises(LLMGatewayError, match="CLI model.*failed"):
                    await client.chat_completion(
                        [{"role": "user", "content": "test"}],
                        model="copilot/gpt-5-mini",
                    )

    @pytest.mark.asyncio
    async def test_stdin_mode_still_works(self):
        """Models without cli_prompt_flag still use stdin piping."""
        reg = ModelRegistry()
        reg.register(
            ModelEntry(
                model_id="test-stdin-cli",
                provider="cli",
                api_style=ApiStyle.CLI,
                endpoint="echo",
                cli_args=["hello"],
                # No cli_prompt_flag → stdin mode
            )
        )
        config = _make_config()
        client = LLMGatewayClient(config, registry=reg)

        captured_stdin_data: bytes | None = None

        mock_proc = AsyncMock()
        mock_proc.returncode = 0

        async def mock_communicate(input_data=None):
            nonlocal captured_stdin_data
            captured_stdin_data = input_data
            return (b"stdin response", b"")

        mock_proc.communicate = mock_communicate

        async def mock_exec(*args, stdin=None, **kwargs):
            return mock_proc

        with patch("asyncio.create_subprocess_exec", side_effect=mock_exec):
            async with client:
                resp = await client.chat_completion(
                    [{"role": "user", "content": "test"}],
                    model="test-stdin-cli",
                )

        assert resp.text == "stdin response"
        assert captured_stdin_data is not None  # stdin was used
        assert b"[User] test" in captured_stdin_data


# ===========================================================================
# TestCopilotExports
# ===========================================================================


class TestCopilotExports:
    def test_copilot_scanner_importable(self):
        from mas_core.llm_gateway import CopilotModelScanner

        assert CopilotModelScanner is not None

    def test_copilot_cost_map_importable(self):
        from mas_core.llm_gateway import COPILOT_COST_MAP

        assert isinstance(COPILOT_COST_MAP, dict)
        assert "gpt-5-mini" in COPILOT_COST_MAP

    def test_model_entry_has_cli_flags(self):
        entry = ModelEntry(
            model_id="test",
            provider="p",
            endpoint="/bin/test",
            cli_prompt_flag="-p",
            cli_model_flag="--model",
        )
        assert entry.cli_prompt_flag == "-p"
        assert entry.cli_model_flag == "--model"

    def test_model_entry_cli_flags_default_none(self):
        entry = ModelEntry(model_id="test", provider="p", endpoint="/bin/test")
        assert entry.cli_prompt_flag is None
        assert entry.cli_model_flag is None
