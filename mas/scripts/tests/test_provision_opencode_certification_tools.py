"""Fixture tests for reproducible OpenCode tool provisioning."""

from __future__ import annotations

import importlib.util
import io
import tarfile
from pathlib import Path
from subprocess import CompletedProcess

SCRIPT = Path(__file__).resolve().parents[1] / "provision_opencode_certification_tools.py"


def _module():
    spec = importlib.util.spec_from_file_location("provision_opencode_certification_tools", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_fixture_provisions_all_pinned_tools_and_records_provenance(tmp_path, monkeypatch):
    module = _module()
    install_dir = tmp_path / "install"
    executable_names = {"semgrep", "skillspector", "trufflehog", "syft"}
    archive_hashes: dict[Path, str] = {}

    def fake_runner(command, *, timeout=900.0, env=None):
        del timeout, env
        if "-m" in command and "venv" in command:
            venv_bin = Path(command[-1]) / "bin"
            venv_bin.mkdir(parents=True, exist_ok=True)
            return CompletedProcess(command, 0, "", "")
        if "-m" in command and "pip" in command:
            requirement = str(command[-1])
            name = "skillspector" if "skillspector" in requirement else "semgrep"
            executable = install_dir / "venv" / "bin" / name
            executable.write_text("fixture executable", encoding="utf-8")
            report_path = Path(command[command.index("--report") + 1])
            report_path.write_text("{}", encoding="utf-8")
            return CompletedProcess(command, 0, "installed", "")
        executable = Path(command[0]).name
        return CompletedProcess(command, 0, f"{executable} fixture-version", "")

    def fake_download(url: str, destination: Path):
        name = "syft" if "syft_" in url else "trufflehog"
        with tarfile.open(destination, "w:gz") as archive:
            payload = b"fixture executable"
            member = tarfile.TarInfo(name=name)
            member.mode = 0o755
            member.size = len(payload)
            archive.addfile(member, io.BytesIO(payload))
        spec = next(item for item in module.TOOL_SPECS if item["name"] == name)
        archive_hashes[destination] = spec["sha256"]

    monkeypatch.setattr(module, "_sha256", lambda path: archive_hashes[path])
    report = module.provision_tools(
        output_dir=tmp_path / "evidence",
        install_dir=install_dir,
        runner=fake_runner,
        downloader=fake_download,
    )

    assert report["status"] == "pass"
    assert set(report["required_tools"]) == executable_names
    assert {row["name"] for row in report["tools"]} == executable_names
    assert all(row["status"] == "pass" for row in report["tools"])
    assert (tmp_path / "evidence" / "tooling-provisioning.json").is_file()
    assert (tmp_path / "evidence" / "tooling-paths.txt").read_text(encoding="utf-8").strip()


def test_fixture_installation_failure_is_not_a_scan_result(tmp_path):
    module = _module()

    def failed_runner(command, *, timeout=900.0, env=None):
        del timeout, env
        return CompletedProcess(command, 1, "", "pip installation failed")

    def failed_download(_url: str, _destination: Path):
        raise OSError("network unavailable")

    report = module.provision_tools(
        output_dir=tmp_path / "evidence",
        install_dir=tmp_path / "install",
        runner=failed_runner,
        downloader=failed_download,
    )

    assert report["status"] == "blocked"
    assert report["failure_classes"] == [module.TOOL_INSTALLATION_FAILURE]
    assert len(report["installation_errors"]) == 4
