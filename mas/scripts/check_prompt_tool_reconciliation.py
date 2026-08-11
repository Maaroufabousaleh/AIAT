"""Check authority prompts against the canonical runtime tool contract.

Prompt prose is guidance, not an authority boundary, but a prompt that names a
nonexistent tool is still an operational defect.  This check keeps the shipped
authority prompts honest by validating their explicit backtick tool references
against the manifest, concrete registration, and role/team policy.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

from tool_service.tools.all_tools import get_all_tools

from mas_core.policy.privileged_ops import EXECUTIVE_ACTIONS, PRIVILEGED_ACTIONS
from mas_core.policy.tool_access import can_use_tool_with_metadata
from mas_core.protocols.enums import AgentRole
from mas_tools_sdk.manifest import TOOL_ALIASES, TOOL_MANIFEST

MAS_ROOT = Path(__file__).resolve().parents[1]
PROMPTS_ROOT = MAS_ROOT / "prompts"
TOOL_TOKEN_RE = re.compile(r"`([A-Za-z][A-Za-z0-9_.-]*)`")

# Dotted identifiers in prompts that are data fields rather than tools.
NON_TOOL_IDENTIFIERS = frozenset({"MessageEnvelope.sent_at"})
POLICY_ACTION_IDENTIFIERS = frozenset(PRIVILEGED_ACTIONS) | frozenset(EXECUTIVE_ACTIONS)

PROMPT_ACTORS: dict[str, tuple[AgentRole, str]] = {
    "ceo.md": (AgentRole.ORCHESTRATOR, "exec_ceo"),
    "coo.md": (AgentRole.EXECUTIVE, "exec_coo"),
    "cfo.md": (AgentRole.C_SUITE, "office_cfo"),
    "cio.md": (AgentRole.C_SUITE, "office_cio"),
    "chrm.md": (AgentRole.C_SUITE, "office_chrm"),
    "cso.md": (AgentRole.C_SUITE, "office_cso"),
    "cto.md": (AgentRole.C_SUITE, "office_cto"),
    "production_pm.md": (AgentRole.ADMIN, "dept_production"),
    "system_pm.md": (AgentRole.ADMIN, "dept_system"),
    "qa_lead.md": (AgentRole.ADMIN, "dept_qa"),
    "devops_pm.md": (AgentRole.ADMIN, "dept_devops"),
}

# These phrases have caused production-facing prompt/tool contradictions in
# the past. Keep them as explicit regressions even when the named tool exists.
FORBIDDEN_PHRASES: dict[str, tuple[str, ...]] = {
    "cfo.md": ("do not have `document.get_latest`",),
}


def _tool_tokens(text: str) -> list[str]:
    return sorted(set(TOOL_TOKEN_RE.findall(text)))


def reconcile(prompts_root: Path = PROMPTS_ROOT) -> dict[str, Any]:
    errors: list[str] = []
    warnings: list[str] = []
    registered = {tool.name for tool in get_all_tools() if tool is not None}
    actual_prompt_files = {path.name for path in prompts_root.glob("*.md")}
    for unmapped in sorted(actual_prompt_files - set(PROMPT_ACTORS)):
        errors.append(f"unmapped authority prompt: {unmapped}")

    for filename, (role, team_id) in PROMPT_ACTORS.items():
        path = prompts_root / filename
        if not path.exists():
            errors.append(f"missing authority prompt: {path}")
            continue
        text = path.read_text(encoding="utf-8")
        lowered = text.casefold()
        for phrase in FORBIDDEN_PHRASES.get(filename, ()):
            if phrase.casefold() in lowered:
                errors.append(f"{filename}: stale contradictory phrase {phrase!r}")

        for token in _tool_tokens(text):
            if token in NON_TOOL_IDENTIFIERS:
                continue
            if token in POLICY_ACTION_IDENTIFIERS:
                continue
            if token in TOOL_ALIASES:
                errors.append(
                    f"{filename}: legacy tool alias {token!r}; use canonical {TOOL_ALIASES[token]!r}"
                )
                continue
            if token not in TOOL_MANIFEST:
                # Dotted backtick identifiers are the unambiguous tool-like
                # references. Other tokens include worker IDs, enum values,
                # and message/state names and are intentionally ignored.
                if "." in token:
                    errors.append(f"{filename}: unknown tool reference {token!r}")
                continue
            if token not in registered:
                errors.append(f"{filename}: manifest tool {token!r} has no concrete registration")
                continue
            entry = TOOL_MANIFEST[token]
            allowed = can_use_tool_with_metadata(
                role=role,
                tool_name=token,
                sender_team=team_id,
                allowed_roles=entry.get("allowed_roles") or (),
                blocked_roles=entry.get("blocked_roles") or (),
            )
            if allowed is not True:
                errors.append(
                    f"{filename}: {team_id}/{role.value} prompt references denied tool {token!r}"
                )

    return {
        "status": "pass" if not errors else "fail",
        "prompt_count": len(PROMPT_ACTORS),
        "manifest_tool_count": len(TOOL_MANIFEST),
        "registered_tool_count": len(registered),
        "errors": errors,
        "warnings": warnings,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="emit a machine-readable report")
    args = parser.parse_args()
    report = reconcile()
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(
            "prompt-tool-reconciliation: "
            f"status={report['status']} prompts={report['prompt_count']} "
            f"manifest={report['manifest_tool_count']} registered={report['registered_tool_count']}"
        )
        for error in report["errors"]:
            print(f"prompt-tool-reconciliation: error: {error}", file=sys.stderr)
    return 0 if report["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
