#!/usr/bin/env python
"""Run MAS operator test prompts through one continued OpenCode session."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import shutil
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_TESTS = ROOT / "prompts" / "mas_opencode_live_tests.json"
DEFAULT_OUT = ROOT / ".tmp" / "opencode-live-tests"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tests-file", type=Path, default=DEFAULT_TESTS)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--phase", help="Run one phase id from the JSON phase list.")
    parser.add_argument("--list-phases", action="store_true", help="List available phases and exit.")
    parser.add_argument("--start", type=int, default=1, help="First test id to run.")
    parser.add_argument("--limit", type=int, help="Maximum number of tests to run.")
    parser.add_argument("--opencode", default="opencode", help="OpenCode executable.")
    parser.add_argument("--model", help="Optional provider/model passed to OpenCode.")
    parser.add_argument("--agent", default="build", help="OpenCode agent to use.")
    parser.add_argument("--dry-run", action="store_true", help="Write prompts but do not call OpenCode.")
    parser.add_argument("--keep-going", action="store_true", help="Continue after an OpenCode process failure.")
    return parser.parse_args()


def load_tests(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as fh:
        data = json.load(fh)
    if not isinstance(data.get("tests"), list):
        raise ValueError(f"{path} does not contain a tests array")
    return data


def list_phases(data: dict) -> None:
    for phase in data.get("phases", []):
        ids = ", ".join(str(test_id) for test_id in phase["test_ids"])
        print(f"{phase['id']}: {phase['title']} ({ids})")


def join_lines(lines: list[str]) -> str:
    return "\n".join(lines).strip("\n")


def phase_by_id(data: dict, phase_id: str | None) -> dict | None:
    if not phase_id:
        return None
    for phase in data.get("phases", []):
        if phase["id"] == phase_id:
            return phase
    known = ", ".join(phase["id"] for phase in data.get("phases", []))
    raise ValueError(f"Unknown phase {phase_id!r}. Known phases: {known}")


def phase_by_test_id(data: dict) -> dict[int, dict]:
    lookup: dict[int, dict] = {}
    for phase in data.get("phases", []):
        for test_id in phase["test_ids"]:
            lookup[int(test_id)] = phase
    return lookup


def select_tests(data: dict, start: int, limit: int | None, phase: dict | None) -> list[dict]:
    allowed_ids = {int(test_id) for test_id in phase["test_ids"]} if phase else None
    selected = [
        test
        for test in data["tests"]
        if int(test["id"]) >= start and (allowed_ids is None or int(test["id"]) in allowed_ids)
    ]
    return selected[:limit] if limit is not None else selected


def build_prompt(data: dict, test: dict, previous_output: str | None, phase_lookup: dict[int, dict]) -> str:
    phase = phase_lookup.get(int(test["id"]))
    chunks = [
        "# AIAT MAS OpenCode Live Test",
        "",
        "## Master Prompt",
        join_lines(data["master_prompt_lines"]),
        "",
        "## Repository and Test Requirements",
        join_lines(data["execution_guidance_lines"]),
        "",
        "## Operator-Style Validation Requirements",
        join_lines(data["operator_validation_lines"]),
        "",
        "## Batch Execution Rules",
        "You are running inside a batch OpenCode session. For this test, inspect the codebase, create or update automated tests, run the relevant test commands, and fix every related code, file, configuration, database, UI, orchestration, policy, and integration problem needed for production-grade behavior.",
        "Do not stop after writing a failing test. Implement the missing behavior, rerun focused tests, and report exact commands and remaining gaps.",
    ]
    if phase:
        chunks.extend(["", "## Current Phase", f"{phase['title']} ({phase['id']})"])
    if phase and phase["id"] == "workflow-orchestration":
        chunks.extend(
            [
                "",
                "## Workflow Operator Validation Requirements",
                join_lines(data["workflow_operator_validation_lines"]),
            ]
        )
    if previous_output:
        chunks.extend(
            [
                "",
                "## Previous OpenCode Output",
                "Before starting the current test, fix ALL missing things, failed assertions, incomplete implementation, regressions, and related problems identified by the previous test output. Treat this as carry-forward context for the batch.",
                "",
                previous_output,
            ]
        )
    chunks.extend(
        [
            "",
            f"## Current Test {test['id']}: {test['title']}",
            join_lines(test["prompt_lines"]),
            "",
            "## Expected Result",
            test["expected_result"],
            "",
            "## Required Final Response",
            "Summarize files changed, tests added or updated, commands run, pass/fail status, and any production-grade gaps that still remain.",
        ]
    )
    return "\n".join(chunks).strip() + "\n"


def resolve_opencode_command(executable: str) -> list[str]:
    resolved = shutil.which(executable)
    if resolved is None and not Path(executable).suffix:
        resolved = shutil.which(f"{executable}.cmd") or shutil.which(f"{executable}.ps1")
    if resolved is None:
        raise FileNotFoundError(
            f"Could not find OpenCode executable {executable!r}. "
            "Install OpenCode or pass --opencode with the full path to opencode.cmd/opencode.ps1."
        )

    path = Path(resolved)
    if path.suffix.lower() == ".ps1":
        pwsh = shutil.which("pwsh") or shutil.which("powershell")
        if pwsh is None:
            raise FileNotFoundError(f"Found {resolved}, but could not find pwsh or powershell to run it.")
        return [pwsh, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", resolved]
    return [resolved]


def run_opencode(args: argparse.Namespace, prompt_file: Path, test: dict, continue_session: bool) -> subprocess.CompletedProcess[str]:
    cmd = [
        *resolve_opencode_command(args.opencode),
        "run",
        "Run the attached AIAT MAS live test prompt completely.",
        "--dir",
        str(ROOT),
        "--agent",
        args.agent,
        "--title",
        f"AIAT MAS live test {test['id']:02d}: {test['title']}",
        "--file",
        str(prompt_file),
    ]
    if continue_session:
        cmd.append("--continue")
    if args.model:
        cmd.extend(["--model", args.model])
    return subprocess.run(
        cmd,
        cwd=ROOT,
        text=True,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
    )


def main() -> int:
    args = parse_args()
    data = load_tests(args.tests_file)
    if args.list_phases:
        list_phases(data)
        return 0

    phase = phase_by_id(data, args.phase)
    phase_lookup = phase_by_test_id(data)
    tests = select_tests(data, args.start, args.limit, phase)
    if not tests:
        print("No tests selected.", file=sys.stderr)
        return 2

    run_id = dt.datetime.now(dt.UTC).strftime("%Y%m%dT%H%M%S%fZ")
    out_dir = args.out_dir / run_id
    out_dir.mkdir(parents=True, exist_ok=True)

    previous_output: str | None = None
    transcript: list[dict] = []
    for index, test in enumerate(tests):
        prompt = build_prompt(data, test, previous_output, phase_lookup)
        prompt_file = out_dir / f"test_{test['id']:02d}_prompt.md"
        output_file = out_dir / f"test_{test['id']:02d}_opencode_output.txt"
        prompt_file.write_text(prompt, encoding="utf-8")

        print(f"[{test['id']:02d}] {test['title']}")
        if args.dry_run:
            result = {"test_id": test["id"], "title": test["title"], "prompt": str(prompt_file), "dry_run": True}
            if phase:
                result["phase"] = phase["id"]
            transcript.append(result)
            previous_output = f"Dry run only. Prompt written to {prompt_file}."
            continue

        completed = run_opencode(args, prompt_file, test, continue_session=index > 0)
        output = completed.stdout or ""
        if completed.stderr:
            output = output + "\n\n[stderr]\n" + completed.stderr
        output_file.write_text(output, encoding="utf-8")
        previous_output = output
        transcript.append(
            {
                "test_id": test["id"],
                "title": test["title"],
                "prompt": str(prompt_file),
                "output": str(output_file),
                "returncode": completed.returncode,
            }
        )
        if phase:
            transcript[-1]["phase"] = phase["id"]
        if completed.returncode != 0 and not args.keep_going:
            print(f"OpenCode failed for test {test['id']} with exit code {completed.returncode}.", file=sys.stderr)
            break

    (out_dir / "transcript.json").write_text(json.dumps(transcript, indent=2), encoding="utf-8")
    print(f"Run artifacts: {out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
