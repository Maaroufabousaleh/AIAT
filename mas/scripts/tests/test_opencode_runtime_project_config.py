"""Pin the untrusted-project config boundary for the OpenCode runtime image.

The exact upstream candidate loads project configuration and local plugins in
the server process.  AIAT sessions use a project-controlled workspace, so the
runtime image must disable project config while retaining managed global
configuration.
"""

from __future__ import annotations

import hashlib
import json
import tarfile
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[3]
CANDIDATE_DIR = (
    REPO_ROOT
    / "mas/docs/provenance/opencode-candidate/2026-10-02-v1.18.21-r4"
)
SOURCE_FILES = (
    "packages/core/src/flag/flag.ts",
    "packages/opencode/src/config/config.ts",
    "packages/opencode/src/config/paths.ts",
    "packages/opencode/src/plugin/index.ts",
)


def _candidate_source() -> dict[str, str]:
    manifest = json.loads((CANDIDATE_DIR / "source-manifest.json").read_text())
    archive = CANDIDATE_DIR / str(manifest["snapshot_path"])
    hasher = hashlib.sha256()
    with archive.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            hasher.update(chunk)
    digest = hasher.hexdigest()
    assert digest == manifest["snapshot_sha256"]
    assert manifest["commit"] == "826d9ad46a22bef0294998e08daa3c4904fea28f"

    result: dict[str, str] = {}
    with tarfile.open(archive, mode="r:xz") as source:
        for path in SOURCE_FILES:
            member = source.getmember(path)
            stream = source.extractfile(member)
            assert stream is not None
            result[path] = stream.read().decode("utf-8")
    return result


def test_exact_candidate_flag_excludes_workspace_config_and_plugins() -> None:
    source = _candidate_source()
    flag = source["packages/core/src/flag/flag.ts"]
    config = source["packages/opencode/src/config/config.ts"]
    paths = source["packages/opencode/src/config/paths.ts"]
    plugins = source["packages/opencode/src/plugin/index.ts"]

    assert 'get OPENCODE_DISABLE_PROJECT_CONFIG()' in flag
    assert 'truthy("OPENCODE_DISABLE_PROJECT_CONFIG")' in flag

    # Both ancestor opencode.json[c] reads and project .opencode directories
    # are behind the same flag.  The plugin service imports configured
    # modules, so excluding those origins prevents project code execution.
    assert "if (!Flag.OPENCODE_DISABLE_PROJECT_CONFIG)" in config
    assert "ConfigPaths.files(\"opencode\", ctx.directory, ctx.worktree)" in config
    assert "!Flag.OPENCODE_DISABLE_PROJECT_CONFIG" in paths
    assert "targets: [\".opencode\"]" in paths
    assert "const plugins = flags.pure ? [] : (cfg.plugin_origins ?? [])" in plugins
    assert "PluginLoader.loadExternal({" in plugins
    assert "applyPlugin(load, input, hooks)" in plugins

    # Global managed config remains present independently of the project flag.
    assert "Global.Path.config" in paths
    assert "Global.Path.home" in paths
    assert "Flag.OPENCODE_CONFIG_DIR" in paths


def test_current_runtime_mitigation_does_not_imply_candidate_version_alignment() -> None:
    dockerfile = (REPO_ROOT / "mas/infra/docker/Dockerfile.opencode").read_text()
    assert "OPENCODE_DISABLE_PROJECT_CONFIG=true" in dockerfile
    candidate_manifest = json.loads(
        (CANDIDATE_DIR / "source-manifest.json").read_text(encoding="utf-8")
    )
    assert candidate_manifest["release"] == "1.18.21"
    reconciliation = json.loads(
        (
            REPO_ROOT
            / "mas/docs/provenance/opencode_version_identity_reconciliation_current_2026-10-12.json"
        ).read_text(encoding="utf-8")
    )
    local_runtime = reconciliation["runtime"]["local_container_observation"]
    assert local_runtime["application_version"] == "1.17.13"
    assert local_runtime["project_config_disabled"] is True
    assert reconciliation["decision"] == "BLOCKED_IDENTITY_COMPATIBILITY_AND_GOVERNANCE"

    compose_paths = (
        REPO_ROOT / "mas/infra/compose/docker-compose.yml",
        REPO_ROOT / "mas/infra/compose/level-a-opencode-integrated.yml",
    )
    for path in compose_paths:
        document = yaml.safe_load(path.read_text())
        service = document["services"]["opencode-runtime"]
        assert service["build"]["dockerfile"] == "infra/docker/Dockerfile.opencode"
        # The local 1.17.13 runtime has the project-config guard enabled. Keep
        # its active identity distinct from the retained 1.18.21 candidate;
        # the candidate still needs its own compatible adapter image and
        # governed certification before the version pin can change.
        assert service["build"]["args"]["OPENCODE_VERSION"] == "1.17.13"
        assert service["build"]["args"]["OPENCODE_VERSION"] != candidate_manifest["release"]
        environment = service.get("environment") or {}
        if isinstance(environment, list):
            explicit = dict(item.split("=", 1) for item in environment if "=" in item)
        else:
            explicit = environment
        configured = explicit.get("OPENCODE_DISABLE_PROJECT_CONFIG")
        assert configured is None or str(configured).lower() in {"true", "1"}
        # Compose passes only explicitly listed environment values; project
        # task payloads and host .env entries cannot set these OpenCode config
        # source overrides for the runtime container.
        assert "OPENCODE_CONFIG_DIR" not in explicit
        assert "OPENCODE_CONFIG_CONTENT" not in explicit
        assert "OPENCODE_CONFIG" not in explicit
