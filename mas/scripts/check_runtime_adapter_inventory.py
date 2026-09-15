"""Report the current runtime-adapter reference inventory.

This is deliberately an inventory tool, not a deletion gate.  AIAT currently
has a canonical ``runtime_adapters.py`` family and a legacy/compatibility
family with dotted configuration and certification references.  The report
keeps those references visible before a later consolidation changes any
module or manifest.

The scan is bounded to checked-in source, test, script, configuration, and
documentation roots.  Operator-created temporary directories are excluded so
the checker remains safe to run in a dirty development workspace.
"""

from __future__ import annotations

import argparse
import ast
import json
import os
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Any

SCHEMA_VERSION = "aiat.runtime-adapter-inventory.v1"
MAS_ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = MAS_ROOT.parent

MODULES: tuple[dict[str, Any], ...] = (
    {
        "path": "mas/packages/mas-core/mas_core/worker_registry/adapter_factory.py",
        "family": "legacy",
        "classes": ("ExternalWorkerAdapter", "ForkedWorkerAdapter"),
        "module_names": ("adapter_factory",),
        "disposition": "deprecate_after_dependency_proof",
    },
    {
        "path": "mas/packages/mas-core/mas_core/worker_registry/langgraph_adapter.py",
        "family": "legacy",
        "classes": ("LangGraphAdapter",),
        "module_names": ("langgraph_adapter",),
        "disposition": "merge_after_compatibility_migration",
    },
    {
        "path": "mas/packages/mas-core/mas_core/worker_registry/crewai_adapter.py",
        "family": "legacy",
        "classes": ("CrewAIAdapter",),
        "module_names": ("crewai_adapter",),
        "disposition": "merge_after_compatibility_migration",
    },
    {
        "path": "mas/packages/mas-core/mas_core/worker_registry/autogen_adapter.py",
        "family": "legacy",
        "classes": ("AutoGenAdapter",),
        "module_names": ("autogen_adapter",),
        "disposition": "deprecate_unless_active_workload",
    },
    {
        "path": "mas/packages/mas-core/mas_core/worker_registry/letta_adapter.py",
        "family": "legacy",
        "classes": ("LettaAdapter",),
        "module_names": ("letta_adapter",),
        "disposition": "deprecate_unless_memory_workload",
    },
    {
        "path": "mas/packages/mas-core/mas_core/worker_registry/microsoft_agent_framework_adapter.py",
        "family": "legacy",
        "classes": ("MicrosoftAgentFrameworkAdapter",),
        "module_names": ("microsoft_agent_framework_adapter",),
        "disposition": "defer_until_inner_team_workload",
    },
    {
        "path": "mas/packages/mas-core/mas_core/worker_registry/runtime_adapters.py",
        "family": "canonical",
        "classes": (
            "LangGraphAdapter",
            "CrewAIAdapter",
            "GatewayWorkerAdapter",
            "OpenCodeAdapter",
        ),
        "module_names": ("runtime_adapters",),
        "disposition": "keep_and_modify",
    },
)

_TEXT_SUFFIXES = frozenset(
    {
        ".json",
        ".md",
        ".py",
        ".toml",
        ".txt",
        ".yaml",
        ".yml",
    }
)
_SOURCE_ROOTS = (
    MAS_ROOT / "apps",
    MAS_ROOT / "packages/mas-core/mas_core",
    MAS_ROOT / "packages/mas-core/tests",
    MAS_ROOT / "scripts",
    MAS_ROOT / "docs",
    REPOSITORY_ROOT / "Docs",
    REPOSITORY_ROOT / "README.md",
    REPOSITORY_ROOT / "ROADMAP.md",
    REPOSITORY_ROOT / "AIAT_TARGET_PROGRAMME.md",
)
_IGNORED_PARTS = frozenset(
    {
        ".git",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        "__pycache__",
        "node_modules",
        ".next",
        "dist",
        "build",
        "coverage",
        ".venv",
        "venv",
    }
)
_MAX_SCANNED_FILE_BYTES = 2 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class Reference:
    path: str
    line: int
    kind: str
    text: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "line": self.line,
            "kind": self.kind,
            "text": self.text,
        }


def _iter_files() -> list[Path]:
    files: set[Path] = set()
    for root in _SOURCE_ROOTS:
        if root.is_file():
            if root.suffix in _TEXT_SUFFIXES:
                files.add(root)
            continue
        if not root.is_dir():
            continue
        # ``Path.rglob`` can spend a long time walking operator-created
        # dependency/temp trees before the caller gets a chance to filter
        # them.  Prune those directories during the walk and never follow
        # symlinked directories.
        for directory, child_dirs, child_files in os.walk(root, followlinks=False):
            child_dirs[:] = sorted(
                name
                for name in child_dirs
                if name not in _IGNORED_PARTS and not name.startswith(".tmp")
            )
            for name in child_files:
                path = Path(directory) / name
                if path.suffix not in _TEXT_SUFFIXES or path.is_symlink():
                    continue
                try:
                    file_stat = path.stat()
                except OSError:
                    continue
                if not stat.S_ISREG(file_stat.st_mode) or file_stat.st_size > _MAX_SCANNED_FILE_BYTES:
                    continue
                files.add(path)
    return sorted(files)


def _relative(path: Path) -> str:
    return path.relative_to(REPOSITORY_ROOT).as_posix()


def _reference_kind(path: Path, line: str, *, self_path: str) -> str:
    relative = _relative(path)
    if relative == self_path:
        return "self"
    if path.suffix == ".py":
        if "/tests/" in f"/{relative}/" or relative.endswith("/tests"):
            return "test"
        if "/scripts/" in f"/{relative}/":
            return "script_or_tooling"
        if relative.startswith("mas/apps/") or relative.startswith("mas/packages/mas-core/mas_core/"):
            if "importlib" in line or "import_module" in line or "create_adapter" in line:
                return "production_dynamic_or_factory_reference"
            return "production_reference"
    if path.suffix in {".yaml", ".yml", ".toml", ".json"}:
        return "manifest_or_configuration"
    return "documentation"


def _ast_reference_facts(path: Path, source: str) -> tuple[set[str], bool]:
    if path.suffix != ".py":
        return set(), False
    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError:
        return {"<unparsed_python>"}, False
    imported_module_basenames: set[str] = set()
    has_create_adapter_call = False
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_module_basenames.update(
                alias.name.rsplit(".", 1)[-1] for alias in node.names
            )
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            imported_module_basenames.add(module.rsplit(".", 1)[-1])
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "create_adapter":
            has_create_adapter_call = True
    return imported_module_basenames, has_create_adapter_call


def build_report() -> dict[str, Any]:
    files = _iter_files()
    # Read each bounded file once.  The inventory has several module families,
    # but repeatedly opening the same Windows-mounted workspace files makes a
    # static audit unnecessarily slow.
    sources = []
    for path in files:
        source = path.read_text(encoding="utf-8", errors="replace")
        imported_module_basenames, has_create_adapter_call = _ast_reference_facts(path, source)
        sources.append((path, source, imported_module_basenames, has_create_adapter_call))
    report_modules: list[dict[str, Any]] = []
    for spec in MODULES:
        module_path = str(spec["path"])
        basename = Path(module_path).name
        tokens = set(spec["module_names"]) | set(spec["classes"]) | {basename}
        references: list[Reference] = []
        ast_kinds: dict[str, list[str]] = {}
        for path, source, imported_module_basenames, has_create_adapter_call in sources:
            for line_number, line in enumerate(source.splitlines(), 1):
                if not any(token in line for token in tokens):
                    continue
                kind = _reference_kind(path, line, self_path=module_path)
                references.append(
                    Reference(
                        path=_relative(path),
                        line=line_number,
                        kind=kind,
                        text=line.strip()[:240],
                    )
                )
            kinds: set[str] = set()
            if set(spec["module_names"]) & imported_module_basenames:
                kinds.add("import")
            if has_create_adapter_call:
                kinds.add("create_adapter_call")
            if kinds:
                ast_kinds[_relative(path)] = sorted(kinds)

        def _paths(
            kind: str | None = None,
            *,
            references_snapshot: tuple[Reference, ...] = tuple(references),
        ) -> list[str]:
            values = {
                item.path
                for item in references_snapshot
                if kind is None or item.kind == kind
            }
            return sorted(values)

        direct_production = [
            reference.as_dict()
            for reference in references
            if reference.kind in {"production_reference", "production_dynamic_or_factory_reference"}
            and reference.path != module_path
        ]
        test_references = [reference.as_dict() for reference in references if reference.kind == "test"]
        config_references = [
            reference.as_dict()
            for reference in references
            if reference.kind == "manifest_or_configuration"
        ]
        dynamic_references = [
            reference.as_dict()
            for reference in references
            if reference.kind == "production_dynamic_or_factory_reference"
            or "import" in ast_kinds.get(reference.path, [])
        ]
        documentation_references = [
            reference.as_dict() for reference in references if reference.kind == "documentation"
        ]
        report_modules.append(
            {
                "path": module_path,
                "family": spec["family"],
                "classes": list(spec["classes"]),
                "disposition": spec["disposition"],
                "exists": (REPOSITORY_ROOT / module_path).is_file(),
                "production_reference_paths": _paths("production_reference")
                + _paths("production_dynamic_or_factory_reference"),
                "production_references": direct_production,
                "test_reference_paths": _paths("test"),
                "test_references": test_references,
                "manifest_config_paths": _paths("manifest_or_configuration"),
                "manifest_config_references": config_references,
                "dynamic_reference_paths": sorted({item["path"] for item in dynamic_references}),
                "dynamic_references": dynamic_references,
                "documentation_paths": _paths("documentation"),
                "documentation_references": documentation_references,
                "ast_reference_kinds": ast_kinds,
            }
        )

    return {
        "schema_version": SCHEMA_VERSION,
        "repository": "Maaroufabousaleh/AIAT",
        "scope": "checked-in AIAT source, tests, scripts, configuration, and documentation",
        "modules": report_modules,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="emit the machine-readable report")
    args = parser.parse_args()
    report = build_report()
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        for module in report["modules"]:
            print(
                f"{module['path']}: production={len(module['production_references'])} "
                f"tests={len(module['test_references'])} "
                f"config={len(module['manifest_config_references'])} "
                f"docs={len(module['documentation_references'])}"
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
