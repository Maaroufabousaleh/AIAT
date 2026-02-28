"""GitHub Copilot CLI provider — configuration, constants, and model scanner.

The provider config is registered at import time.  Individual **model**
entries are registered dynamically by :class:`CopilotModelScanner`, which
discovers free (0×) models at runtime via ``copilot --help``.

Usage::

    scanner = CopilotModelScanner()
    await scanner.scan_and_register()           # initial scan
    await scanner.start_background_scan(3600)   # re-scan every hour

    # ... later ...
    await scanner.stop_background_scan()
"""

from __future__ import annotations

import asyncio
import logging
import re
import shutil

from ..base import ApiStyle, ModelEntry, ModelRegistry, ProviderConfig

# Deferred import — MODEL_REGISTRY is created in the parent __init__.py
# before sub-packages are imported.
from .. import MODEL_REGISTRY

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_COPILOT_BINARY = "copilot"

#: Static CLI flags for non-interactive copilot usage.
COPILOT_BASE_ARGS: list[str] = [
    "-s",                # silent — output only agent response
    "--no-ask-user",     # no interactive prompts
    "--no-auto-update",  # don't check for CLI updates
]

#: Known cost multipliers (from ``copilot /models`` output).
#: 0.0 = free / included in every Copilot plan.
COPILOT_COST_MAP: dict[str, float] = {
    "claude-sonnet-4.6": 1.0,
    "claude-sonnet-4.5": 1.0,
    "claude-haiku-4.5": 0.33,
    "claude-opus-4.6": 3.0,
    "claude-opus-4.6-fast": 30.0,
    "claude-opus-4.5": 3.0,
    "claude-sonnet-4": 1.0,
    "gemini-3-pro-preview": 1.0,
    "gpt-5.3-codex": 1.0,
    "gpt-5.2-codex": 1.0,
    "gpt-5.2": 1.0,
    "gpt-5.1-codex-max": 1.0,
    "gpt-5.1-codex": 1.0,
    "gpt-5.1": 1.0,
    "gpt-5.1-codex-mini": 0.33,
    "gpt-5-mini": 0.0,
    "gpt-4.1": 0.0,
}

#: Per-model metadata for known Copilot CLI models.
#: Keys: supports_reasoning, max_context_tokens, supports_images.
COPILOT_MODEL_META: dict[str, dict] = {
    "gpt-4.1": {
        "supports_reasoning": False,
        "supports_images": False,
        "max_context_tokens": 64_000,
    },
    "gpt-5-mini": {
        "supports_reasoning": True,
        "supports_images": False,
        "max_context_tokens": 128_000,
    },
}

# ---------------------------------------------------------------------------
# Provider
# ---------------------------------------------------------------------------

#: Copilot provider configuration (registered on import).
COPILOT_PROVIDER = ProviderConfig(
    provider_id="copilot",
    base_url="",  # CLI-based, no HTTP base URL
    api_key_env_vars=[],
    default_api_key="",
    description=(
        "GitHub Copilot CLI — local subprocess.  "
        "Free-tier models (0× cost multiplier) are registered automatically."
    ),
)
MODEL_REGISTRY.register_provider(COPILOT_PROVIDER)

# ---------------------------------------------------------------------------
# Regex helpers
# ---------------------------------------------------------------------------

#: Regex to extract model choices from ``copilot --help`` output.
MODEL_CHOICES_RE = re.compile(
    r"--model\s+<model>\s+.*?\(choices:\s*(.*?)\)",
    re.DOTALL,
)
MODEL_ID_RE = re.compile(r'"([^"]+)"')


# ---------------------------------------------------------------------------
# Scanner
# ---------------------------------------------------------------------------


class CopilotModelScanner:
    """Discovers and registers free GitHub Copilot CLI models.

    Parameters
    ----------
    registry:
        Target ``ModelRegistry``.  Defaults to the global ``MODEL_REGISTRY``.
    binary:
        Name or path of the copilot binary.  Resolved via ``shutil.which``.
    scan_interval:
        Default seconds between background re-scans (default 3600 = 1 h).

    Notes
    -----
    Only **free (0× cost)** models are ever registered.  Premium Copilot
    models are unconditionally excluded and cannot be used by agents.
    """

    def __init__(
        self,
        *,
        registry: ModelRegistry | None = None,
        binary: str | None = None,
        scan_interval: float = 3600.0,
    ) -> None:
        self._registry = registry if registry is not None else MODEL_REGISTRY
        self._binary = binary or DEFAULT_COPILOT_BINARY
        self._scan_interval = scan_interval
        self._scan_task: asyncio.Task[None] | None = None
        self._discovered_models: list[str] = []

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def discovered_models(self) -> list[str]:
        """Model IDs discovered in the last scan (before cost filtering)."""
        return list(self._discovered_models)

    # ------------------------------------------------------------------
    # Binary lookup
    # ------------------------------------------------------------------

    def find_binary(self) -> str | None:
        """Return the resolved copilot binary path, or ``None``."""
        return shutil.which(self._binary)

    # ------------------------------------------------------------------
    # Discovery
    # ------------------------------------------------------------------

    async def discover_models(self) -> list[str]:
        """Run ``copilot --help`` and extract available model IDs.

        Returns a list of **all** advertised model IDs (unfiltered).
        """
        binary = self.find_binary()
        if binary is None:
            logger.warning("Copilot CLI binary not found on PATH (%s)", self._binary)
            return []

        try:
            proc = await asyncio.create_subprocess_exec(
                binary,
                "--help",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=15.0)
            help_text = stdout.decode("utf-8", errors="replace")
        except (asyncio.TimeoutError, OSError) as exc:
            logger.warning("Failed to run copilot --help: %s", exc)
            return []

        match = MODEL_CHOICES_RE.search(help_text)
        if not match:
            logger.warning("Could not parse model choices from copilot --help")
            return []

        choices_text = match.group(1)
        model_ids = MODEL_ID_RE.findall(choices_text)
        logger.info(
            "Copilot CLI discovered %d model(s): %s",
            len(model_ids),
            ", ".join(model_ids),
        )
        self._discovered_models = model_ids
        return model_ids

    # ------------------------------------------------------------------
    # Filtering
    # ------------------------------------------------------------------

    def filter_free_models(self, model_ids: list[str]) -> list[str]:
        """Return only models whose cost multiplier is exactly 0.0 (free tier).

        Premium Copilot models (any cost > 0) are unconditionally excluded.
        """
        free: list[str] = []
        for mid in model_ids:
            cost = COPILOT_COST_MAP.get(mid)
            if cost == 0.0:
                free.append(mid)
            elif cost is None:
                logger.debug(
                    "Unknown cost for copilot model '%s' — skipping (add to COPILOT_COST_MAP)",
                    mid,
                )
            else:
                logger.debug(
                    "Copilot model '%s' is premium (cost=%.2f×) — excluded",
                    mid,
                    cost,
                )
        return free

    # ------------------------------------------------------------------
    # Registration
    # ------------------------------------------------------------------

    def _make_entry(self, copilot_model_id: str, binary: str) -> ModelEntry:
        """Create a ``ModelEntry`` for a single copilot model."""
        from ..base import ModelCapabilities

        meta = COPILOT_MODEL_META.get(copilot_model_id, {})
        has_reasoning = meta.get("supports_reasoning", False)
        has_images = meta.get("supports_images", False)
        ctx_tokens = meta.get("max_context_tokens", None)

        modalities = "text"
        if has_images:
            modalities = "text, image"

        best = ["code-generation", "text-analysis", "agent-advisory"]
        if has_reasoning:
            best.insert(0, "reasoning")
        else:
            best.insert(0, "quick-generation")

        lims = [
            "no-tool-calling",
            "no-streaming",
            "no-file-attachments",
            "cli-subprocess",
        ]
        if not has_images:
            lims.insert(0, "text-only")
            lims.append("no-vision")

        return ModelEntry(
            model_id=f"copilot/{copilot_model_id}",
            provider="copilot",
            api_style=ApiStyle.CLI,
            endpoint=binary,
            description=(
                f"GitHub Copilot CLI — {copilot_model_id} (free tier, 0× cost). "
                f"Modalities: {modalities}. "
                f"{'Reasoning capable.' if has_reasoning else 'No reasoning.'}"
            ),
            max_context_tokens=ctx_tokens,
            cli_args=[*COPILOT_BASE_ARGS],
            cli_prompt_flag="-p",
            cli_model_flag="--model",
            supports_tools=False,
            supports_streaming=False,
            cost_per_1m_input=0.0,
            cost_per_1m_output=0.0,
            extra={"cli_model_name": copilot_model_id},
            capabilities=ModelCapabilities(
                supports_images=has_images,
                supports_pdf=False,
                supports_video=False,
                supports_reasoning=has_reasoning,
                image_how=(
                    "image input supported via Copilot CLI"
                    if has_images
                    else "not supported — use Copilot Chat UI for images"
                ),
                pdf_how="extract text and paste relevant excerpts into prompt",
            ),
            best_for=best,
            limits=lims,
            compliance=[
                "free-tier (0× cost)",
                "github-copilot-tos",
                "local-subprocess",
                "no-data-retention",
            ],
        )

    async def scan_and_register(self) -> list[ModelEntry]:
        """Full scan → filter → register cycle.

        Returns the list of newly registered ``ModelEntry`` objects.
        """
        all_ids = await self.discover_models()
        free_ids = self.filter_free_models(all_ids)

        if not free_ids:
            logger.info("No free copilot models discovered")
            return []

        # Ensure the copilot provider is registered (handles custom registries)
        if self._registry.get_provider("copilot") is None:
            self._registry.register_provider(COPILOT_PROVIDER)

        binary = self.find_binary() or self._binary
        entries: list[ModelEntry] = []

        for mid in free_ids:
            entry = self._make_entry(mid, binary)
            self._registry.register(entry)
            entries.append(entry)
            logger.info("Registered copilot model: %s", entry.model_id)

        return entries

    # ------------------------------------------------------------------
    # Synchronous helpers (for startup or non-async contexts)
    # ------------------------------------------------------------------

    def register_known_free_models(self) -> list[ModelEntry]:
        """Register free models from the static ``COPILOT_COST_MAP``.

        This is a **synchronous** fallback for environments where
        ``copilot --help`` cannot be run (e.g. CI, containers).
        It registers every model in the cost map whose multiplier is 0.0.
        """
        binary = self.find_binary() or self._binary
        free_ids = [
            mid
            for mid, cost in COPILOT_COST_MAP.items()
            if cost == 0.0
        ]

        if not free_ids:
            return []

        if self._registry.get_provider("copilot") is None:
            self._registry.register_provider(COPILOT_PROVIDER)

        entries: list[ModelEntry] = []
        for mid in free_ids:
            entry = self._make_entry(mid, binary)
            self._registry.register(entry)
            entries.append(entry)

        logger.info(
            "Registered %d known free copilot model(s): %s",
            len(entries),
            ", ".join(e.model_id for e in entries),
        )
        return entries

    # ------------------------------------------------------------------
    # Background scanning
    # ------------------------------------------------------------------

    async def start_background_scan(
        self,
        interval: float | None = None,
    ) -> None:
        """Start periodic model re-discovery.

        Parameters
        ----------
        interval:
            Seconds between scans.  Defaults to ``scan_interval`` from
            the constructor (3600 s).
        """
        if self._scan_task is not None and not self._scan_task.done():
            logger.warning("Copilot background scan already running")
            return

        actual_interval = interval or self._scan_interval
        self._scan_task = asyncio.create_task(
            self._scan_loop(actual_interval),
            name="copilot-model-scanner",
        )
        logger.info(
            "Copilot model scanner started (interval: %.0f s)", actual_interval
        )

    async def stop_background_scan(self) -> None:
        """Cancel the periodic scan task."""
        if self._scan_task is not None:
            self._scan_task.cancel()
            try:
                await self._scan_task
            except asyncio.CancelledError:
                pass
            self._scan_task = None
            logger.info("Copilot model scanner stopped")

    async def _scan_loop(self, interval: float) -> None:
        """Internal periodic loop. Runs forever until cancelled."""
        while True:
            try:
                await self.scan_and_register()
            except Exception:
                logger.exception("Copilot model scan failed")
            await asyncio.sleep(interval)
