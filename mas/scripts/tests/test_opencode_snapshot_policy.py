"""Keep OpenCode project snapshots disabled for AIAT-managed worker sessions."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import tarfile
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
CANDIDATE_DIR = REPOSITORY_ROOT / "mas/docs/provenance/opencode-candidate/2026-10-02-v1.18.21-r4"
CANDIDATE_FILES = (
    "packages/core/src/v1/config/config.ts",
    "packages/opencode/src/snapshot/index.ts",
    "packages/opencode/src/session/summary.ts",
)


def _candidate_source() -> dict[str, str]:
    manifest = json.loads((CANDIDATE_DIR / "source-manifest.json").read_text(encoding="utf-8"))
    assert manifest["release"] == "1.18.21"
    assert manifest["commit"] == "826d9ad46a22bef0294998e08daa3c4904fea28f"
    archive = CANDIDATE_DIR / str(manifest["snapshot_path"])
    hasher = hashlib.sha256()
    with archive.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            hasher.update(chunk)
    assert hasher.hexdigest() == manifest["snapshot_sha256"]

    result: dict[str, str] = {}
    with tarfile.open(archive, mode="r:xz") as source:
        for path in CANDIDATE_FILES:
            member = source.getmember(path)
            stream = source.extractfile(member)
            assert stream is not None
            result[path] = stream.read().decode("utf-8")
    return result


def test_exact_candidate_schema_and_runtime_disable_snapshot_writes() -> None:
    source = _candidate_source()
    config_schema = source["packages/core/src/v1/config/config.ts"]
    snapshot_runtime = source["packages/opencode/src/snapshot/index.ts"]
    session_summary = source["packages/opencode/src/session/summary.ts"]

    assert "snapshot: Schema.optional(Schema.Boolean)" in config_schema
    assert "When false, filesystem snapshots are not recorded" in config_schema
    assert "return (yield* config.get()).snapshot !== false" in snapshot_runtime
    assert "if (!(yield* enabled())) return" in snapshot_runtime
    # Tracking exits before gitdir creation/add/write-tree; periodic cleanup
    # and project summary diff generation also honor the same setting.
    track = snapshot_runtime.split("const track = Effect.fnUntraced", maxsplit=1)[1]
    track = track.split("const patch = Effect.fnUntraced", maxsplit=1)[0]
    assert track.index("if (!(yield* enabled())) return") < track.index("fs.ensureDir(state.gitdir)")
    assert "const result = yield* git(args([\"write-tree\"]), { cwd: state.directory })" in track
    assert "if ((yield* config.get()).snapshot === false) return" in session_summary


def test_managed_entrypoint_emits_snapshot_disabled_config(tmp_path: Path) -> None:
    config_path = tmp_path / "opencode.json"
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_opencode = fake_bin / "opencode"
    fake_opencode.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    fake_opencode.chmod(0o755)

    env = {
        "PATH": f"{fake_bin}{os.pathsep}/usr/bin:/bin",
        "OPENCODE_CONFIG": str(config_path),
        "OPENCODE_SERVER_PASSWORD": "test-only-password",
        "OPENCODE_SERVER_HOSTNAME": "127.0.0.1",
        "OPENCODE_SERVER_PORT": "4096",
        "AIAT_GATEWAY_URL": "http://gateway.invalid",
        "AIAT_GATEWAY_API_KEY": "test-only-api-key",
    }
    entrypoint = REPOSITORY_ROOT / "mas/infra/docker/opencode-entrypoint.sh"
    subprocess.run(
        ["sh", str(entrypoint)],
        check=True,
        capture_output=True,
        text=True,
        timeout=5,
        env=env,
    )

    managed_config = json.loads(config_path.read_text(encoding="utf-8"))
    assert managed_config["snapshot"] is False
    assert managed_config["provider"]["aiat"]["name"] == "AIAT gateway"
