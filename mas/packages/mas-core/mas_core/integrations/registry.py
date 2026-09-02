"""Provider registry with explicit, allow-listed built-ins."""

from __future__ import annotations

from typing import TYPE_CHECKING

from .providers import FakeProvider, GitHubProvider, YouTrackProvider

if TYPE_CHECKING:
    from collections.abc import Callable

    from .ports import SourceControlProvider, WorkManagementProvider


class ProviderRegistry:
    def __init__(
        self,
        *,
        credential_resolver: Callable | None = None,
        run_credential_broker: Callable | None = None,
    ) -> None:
        self._credential_resolver = credential_resolver
        self._run_credential_broker = run_credential_broker
        self._instances: dict[tuple[str, str], object] = {}
        self._factories: dict[str, Callable[[Callable | None], object]] = {}

    def register(self, provider_kind: str, factory: Callable[[Callable | None], object]) -> None:
        """Register a reviewed adapter without changing orchestration code.

        Deployments can load a provider package during startup and register its
        factory here.  The adapter still sits behind the same ports and never
        receives credentials except through the injected resolver.
        """
        kind = provider_kind.strip().lower()
        if not kind or not kind.replace("-", "").replace("_", "").isalnum():
            raise ValueError("provider_kind must be a simple adapter identifier")
        if kind in {"fake", "youtrack", "github"}:
            raise ValueError("built-in provider kinds cannot be replaced at runtime")
        self._factories[kind] = factory

    def get(self, provider_kind: str, connection_id: str = "default") -> object:
        key = (provider_kind.lower(), connection_id)
        if key in self._instances:
            return self._instances[key]
        kind = provider_kind.lower()
        if kind == "fake":
            instance: object = FakeProvider()
        elif kind == "youtrack":
            instance = YouTrackProvider(self._credential_resolver)
        elif kind == "github":
            instance = GitHubProvider(
                self._credential_resolver,
                run_credential_broker=self._run_credential_broker,
            )
        elif kind in self._factories:
            instance = self._factories[kind](self._credential_resolver)
        else:
            raise ValueError(f"unsupported provider kind: {provider_kind}")
        self._instances[key] = instance
        return instance

    def work_management(self, provider_kind: str, connection_id: str = "default") -> WorkManagementProvider:
        provider = self.get(provider_kind, connection_id)
        if not hasattr(provider, "project_work_item"):
            raise ValueError(f"provider {provider_kind} does not support work management")
        return provider  # type: ignore[return-value]

    def source_control(self, provider_kind: str, connection_id: str = "default") -> SourceControlProvider:
        provider = self.get(provider_kind, connection_id)
        if not hasattr(provider, "project_pull_request"):
            raise ValueError(f"provider {provider_kind} does not support source control")
        return provider  # type: ignore[return-value]
