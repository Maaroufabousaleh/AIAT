"""Tests for Mistral model discovery and fallback registration."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from mas_core.llm_gateway import MistralModelScanner
from mas_core.llm_gateway.providers import ModelRegistry
from mas_core.llm_gateway.providers.api.mistral import (
    VERIFIED_MISTRAL_CHAT_MODEL_IDS,
    build_mistral_entry,
    is_chat_capable_model,
)


def _client_cm(mock_client: MagicMock) -> MagicMock:
    cm = MagicMock()
    cm.__enter__.return_value = mock_client
    cm.__exit__.return_value = False
    return cm


class TestMistralHelpers:
    def test_chat_filter_excludes_non_chat_routes(self):
        assert is_chat_capable_model("mistral-small-latest") is True
        assert is_chat_capable_model("codestral-latest") is True
        assert is_chat_capable_model("mistral-embed") is False
        assert is_chat_capable_model("mistral-moderation-latest") is False
        assert is_chat_capable_model("mistral-ocr-latest") is False
        assert is_chat_capable_model("voxtral-mini-transcribe-2507") is False

    def test_build_entry_adds_description_for_new_family(self):
        entry = build_mistral_entry("devstral-latest")
        assert entry.model_id == "mistral/devstral-latest"
        assert entry.provider == "mistral"
        assert "Devstral 2" in entry.description
        assert "software-engineering" in entry.best_for
        assert "text-only" in entry.limits

    def test_build_entry_marks_reasoning_models(self):
        entry = build_mistral_entry("magistral-medium-latest")
        assert entry.capabilities.supports_reasoning is True
        assert entry.capabilities.supports_images is True
        assert "reasoning" in entry.best_for


class TestMistralModelScanner:
    def test_discover_models_parses_models_payload(self, monkeypatch):
        monkeypatch.setenv("MISTRAL_API_KEY", "test-key")
        scanner = MistralModelScanner(registry=ModelRegistry())

        mock_response = MagicMock()
        mock_response.json.return_value = {
            "data": [
                {"id": "mistral-small-latest"},
                {"id": "mistral-small-latest"},
                {"id": "mistral-embed"},
            ]
        }
        mock_response.raise_for_status.return_value = None
        mock_client = MagicMock()
        mock_client.get.return_value = mock_response

        with patch(
            "mas_core.llm_gateway.providers.api.mistral.httpx.Client",
            return_value=_client_cm(mock_client),
        ):
            models = scanner.discover_models()

        assert models == ["mistral-small-latest", "mistral-embed"]

    def test_filter_chat_models_keeps_only_chat_candidates(self):
        scanner = MistralModelScanner(registry=ModelRegistry())
        filtered = scanner.filter_chat_models(
            [
                "mistral-small-latest",
                "mistral-embed",
                "mistral-moderation-latest",
                "voxtral-mini-transcribe-2507",
                "codestral-latest",
            ]
        )
        assert filtered == ["mistral-small-latest", "codestral-latest"]

    def test_verify_chat_models_excludes_invalid_entries(self, monkeypatch):
        monkeypatch.setenv("MISTRAL_API_KEY", "test-key")
        scanner = MistralModelScanner(registry=ModelRegistry())

        ok_response = MagicMock(status_code=200, text="{}")
        bad_response = MagicMock(status_code=400, text='{"message":"Invalid model"}')

        mock_client = MagicMock()
        mock_client.post.side_effect = [ok_response, bad_response]

        with patch(
            "mas_core.llm_gateway.providers.api.mistral.httpx.Client",
            return_value=_client_cm(mock_client),
        ):
            verified = scanner.verify_chat_models(
                ["mistral-small-latest", "voxtral-mini-2602"]
            )

        assert verified == ["mistral-small-latest"]

    def test_scan_and_register_uses_verified_set_when_requested(self):
        reg = ModelRegistry()
        scanner = MistralModelScanner(registry=reg)

        with (
            patch.object(
                scanner,
                "discover_models",
                return_value=["mistral-small-latest", "voxtral-mini-2602"],
            ),
            patch.object(
                scanner,
                "verify_chat_models",
                return_value=["mistral-small-latest"],
            ),
        ):
            entries = scanner.scan_and_register(verify_chat=True)

        assert [entry.model_id for entry in entries] == ["mistral/mistral-small-latest"]
        assert reg.get("mistral/mistral-small-latest") is not None
        assert reg.get("mistral/voxtral-mini-2602") is None

    def test_register_known_chat_models_loads_verified_snapshot(self):
        reg = ModelRegistry()
        scanner = MistralModelScanner(registry=reg)
        entries = scanner.register_known_chat_models()

        assert len(entries) == len(VERIFIED_MISTRAL_CHAT_MODEL_IDS)
        assert reg.get("mistral/mistral-small-latest") is not None
        assert reg.get("mistral/codestral-latest") is not None
        assert reg.get("mistral/magistral-medium-latest") is not None


class TestMistralExports:
    def test_mistral_scanner_importable(self):
        assert MistralModelScanner is not None
