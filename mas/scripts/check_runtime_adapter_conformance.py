"""Run a deterministic conformance probe for the default framework adapters.

The probe exercises AIAT's actual LangGraph and CrewAI adapter classes with a
small in-process framework fixture. It verifies manifest construction,
MessageEnvelope translation, bounded completion, health, and shutdown without
calling a model, a tool, a provider, or a project service. ``--live`` additionally
requires the selected framework packages to be importable in the environment;
that package check is evidence of installation only, not a worker canary.

This is intentionally separate from runtime benchmarks, sandbox checks,
security scans, provider checks, and live worker certification.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import importlib.metadata
import importlib.util
import json
import sys
import types
from typing import TYPE_CHECKING, Any, TypedDict

if TYPE_CHECKING:
    from collections.abc import Iterator

from mas_core.protocols.enums import AgentRole, MessageType
from mas_core.protocols.envelope import MessageEnvelope
from mas_core.protocols.worker_manifest import WorkerManifest
from mas_core.worker_registry.crewai_adapter import CrewAIAdapter, CrewAICapabilities
from mas_core.worker_registry.langgraph_adapter import LangGraphAdapter, LangGraphCapabilities

CONFORMANCE_SCHEMA = "aiat.runtime-adapter-conformance.v1"
DEFAULT_RUNTIME_IDS = ("langgraph", "crewai")
PACKAGE_NAMES = {"langgraph": "langgraph", "crewai": "crewai"}


class _FixtureState(TypedDict, total=False):
    task: str
    context: str
    project_id: str | None
    messages: list[Any]
    output: str


def _package_probe(runtime_id: str) -> dict[str, Any]:
    package = PACKAGE_NAMES[runtime_id]
    try:
        available = importlib.util.find_spec(package) is not None
    except (ImportError, ModuleNotFoundError, ValueError):
        available = False
    version: str | None = None
    if available:
        try:
            version = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            version = None
    return {"package": package, "available": available, "version": version}


@contextlib.contextmanager
def _module_fixtures(runtime_id: str) -> Iterator[None]:
    """Install only the framework surface used by the adapter probe."""
    names = {runtime_id}
    if runtime_id == "langgraph":
        names.update({"langgraph.graph", "langgraph.checkpoint", "langgraph.checkpoint.memory"})

        class _CompiledGraph:
            def __init__(self, node: Any):
                self._node = node

            async def ainvoke(self, state: dict[str, Any]) -> dict[str, Any]:
                result = self._node(state)
                if hasattr(result, "__await__"):
                    result = await result
                return {**state, **(result if isinstance(result, dict) else {"output": result})}

        class _StateGraph:
            def __init__(self, _state_schema: Any):
                self._nodes: dict[str, Any] = {}
                self._entry: str | None = None

            def add_node(self, name: str, handler: Any) -> None:
                self._nodes[name] = handler

            def add_edge(self, _from_node: str, _to_node: str) -> None:
                return None

            def set_entry_point(self, name: str) -> None:
                self._entry = name

            def set_finish_point(self, _name: str) -> None:
                return None

            def compile(self, *, checkpointer: Any = None) -> _CompiledGraph:
                del checkpointer
                if self._entry is None or self._entry not in self._nodes:
                    raise ValueError("fixture graph entry point is missing")
                return _CompiledGraph(self._nodes[self._entry])

        class _MemorySaver:
            pass

        langgraph = types.ModuleType("langgraph")
        graph = types.ModuleType("langgraph.graph")
        graph.StateGraph = _StateGraph  # type: ignore[attr-defined]
        checkpoint = types.ModuleType("langgraph.checkpoint")
        memory = types.ModuleType("langgraph.checkpoint.memory")
        memory.MemorySaver = _MemorySaver  # type: ignore[attr-defined]
        modules = {
            "langgraph": langgraph,
            "langgraph.graph": graph,
            "langgraph.checkpoint": checkpoint,
            "langgraph.checkpoint.memory": memory,
        }
    else:
        class _Agent:
            def __init__(self, *, role: str, goal: str, backstory: str, **_kwargs: Any):
                self.role = role
                self.goal = goal
                self.backstory = backstory

        class _Task:
            def __init__(self, *, description: str, expected_output: str, agent: Any = None, **_kwargs: Any):
                self.description = description
                self.expected_output = expected_output
                self.agent = agent

        class _Crew:
            def __init__(self, *, agents: list[Any], tasks: list[Any], process: str, **_kwargs: Any):
                self.agents = agents
                self.tasks = tasks
                self.process = process

            def kickoff(self, *, inputs: dict[str, Any]) -> str:
                return f"fixture:{inputs.get('task', '')}"

        crewai = types.ModuleType("crewai")
        crewai.Agent = _Agent  # type: ignore[attr-defined]
        crewai.Task = _Task  # type: ignore[attr-defined]
        crewai.Crew = _Crew  # type: ignore[attr-defined]
        modules = {"crewai": crewai}

    previous = {name: sys.modules.get(name) for name in names}
    sys.modules.update(modules)
    try:
        yield
    finally:
        for name, prior in previous.items():
            if prior is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = prior


def _envelope() -> MessageEnvelope:
    return MessageEnvelope(
        msg_type=MessageType.TASK,
        sender_id="runtime-conformance",
        sender_role=AgentRole.ORCHESTRATOR,
        sender_team="system",
        recipient_id="runtime-fixture",
        project_id="runtime-conformance-project",
        payload={
            "task": "bounded adapter acknowledgement",
            "context": "fixture",
            "messages": [{"role": "user", "content": "fixture context"}],
        },
    )


async def _run_fixture(runtime_id: str) -> dict[str, Any]:
    with _module_fixtures(runtime_id):
        manifest_data: dict[str, Any] = {
            "metadata": {"id": f"{runtime_id}-fixture", "name": f"{runtime_id} fixture"},
            "runtime_tier": runtime_id,
            "integration": {"isolation_mode": runtime_id},
        }
        if runtime_id == "langgraph":
            def handler(state: _FixtureState) -> dict[str, str]:
                return {"output": f"fixture:{state.get('task', '')}"}

            graph_definition = {
                "type": "StateGraph",
                "state_schema": _FixtureState,
                "nodes": {"worker": {"handler": handler}},
                "entry": "worker",
                "finish": "worker",
            }
            manifest_data["runtime_config"] = {"graph_definition": graph_definition}
            manifest = WorkerManifest.model_validate(manifest_data)
            adapter = LangGraphAdapter(
                manifest,
                LangGraphCapabilities(graph_definition=graph_definition, checkpointer="memory"),
            )
        else:
            crew_config = {
                "agents": [{"role": "fixture", "goal": "acknowledge", "backstory": "bounded"}],
                "tasks": [{"description": "acknowledge", "expected_output": "fixture", "agent_index": 0}],
            }
            manifest_data["runtime_config"] = {"crew_config": crew_config, "process": "sequential"}
            manifest = WorkerManifest.model_validate(manifest_data)
            adapter = CrewAIAdapter(manifest, CrewAICapabilities(crew_config=crew_config))

        result = await adapter.send_task(_envelope())
        healthy_before_shutdown = await adapter.health_check()
        await adapter.shutdown()
        healthy_after_shutdown = await adapter.health_check()

    expected_output = "fixture:bounded adapter acknowledgement"
    output = result.get("output")
    output_value = output.get("output") if isinstance(output, dict) else output
    translated_input = result.get("input")
    project_context_preserved = (
        isinstance(translated_input, dict)
        and translated_input.get("project_id") == "runtime-conformance-project"
    )
    message_history_preserved = (
        isinstance(translated_input, dict)
        and translated_input.get("messages") == [{"role": "user", "content": "fixture context"}]
    )
    passed = (
        result.get("status") == "completed"
        and output_value == expected_output
        and project_context_preserved
        and message_history_preserved
        and healthy_before_shutdown is True
        and healthy_after_shutdown is False
    )
    return {
        "runtime_id": runtime_id,
        "status": "pass" if passed else "fail",
        "adapter_class": type(adapter).__name__,
        "result_status": result.get("status"),
        "output_matches": output_value == expected_output,
        "project_context_preserved": project_context_preserved,
        "message_history_preserved": message_history_preserved,
        "health_before_shutdown": healthy_before_shutdown,
        "health_after_shutdown": healthy_after_shutdown,
        "external_model_call": False,
    }


def inspect(*, runtime_ids: tuple[str, ...], require_packages: bool) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    for runtime_id in runtime_ids:
        package = _package_probe(runtime_id)
        fixture = asyncio.run(_run_fixture(runtime_id))
        row = {**package, **fixture}
        if require_packages and not package["available"]:
            row["status"] = "blocked"
            row["reason"] = f"{package['package']} package is not installed"
        rows.append(row)
    failed = [row for row in rows if row["status"] == "fail"]
    blocked = [row for row in rows if row["status"] == "blocked"]
    status = "fail" if failed else ("blocked" if blocked else "pass")
    return {
        "schema_version": CONFORMANCE_SCHEMA,
        "mode": "live" if require_packages else "fixture",
        "status": status,
        "runtimes": rows,
        "scope": "adapter manifest/message translation and bounded fixture completion; no model/tool/provider/project calls",
        "certification_boundary": {
            "package_imports": "checked" if require_packages else "reported_only",
            "framework_execution": "fixture_only",
            "sandbox": "not_checked",
            "security_scan": "not_checked",
            "canary": "not_checked",
            "live_worker_run": "not_checked",
            "rollback": "not_checked",
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", help="require framework packages to be installed")
    parser.add_argument("--runtime", dest="runtimes", action="append", choices=DEFAULT_RUNTIME_IDS)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    report = inspect(runtime_ids=tuple(args.runtimes or DEFAULT_RUNTIME_IDS), require_packages=args.live)
    if args.json:
        print(json.dumps(report, sort_keys=True, indent=2))
    else:
        print(f"runtime adapter conformance: {report['status']} ({report['mode']})")
    return 2 if report["status"] == "blocked" else (1 if report["status"] == "fail" else 0)


if __name__ == "__main__":
    raise SystemExit(main())
