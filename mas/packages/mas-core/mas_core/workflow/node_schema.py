"""Versioned, UI-friendly schemas for orchestration flow nodes.

The flow engine deliberately keeps extension fields open so older definitions and
adapter-specific settings remain readable.  This module supplies the canonical
field catalogue used by validation and by the dashboard form generator.  It is
data, not a second execution engine: runtime behaviour still lives in
``flow_engine.py`` and ``worker_policy.py``.
"""

from __future__ import annotations

from copy import deepcopy
from typing import TYPE_CHECKING, Any
from uuid import UUID

if TYPE_CHECKING:
    from collections.abc import Mapping

    from mas_core.workflow.flow_engine import FlowNodeType


NODE_SCHEMA_VERSION = "1.0"
SUPPORTED_NODE_SCHEMA_VERSIONS = frozenset({NODE_SCHEMA_VERSION})
LEGACY_TASK_ALIAS_FIELDS = frozenset({"team_id", "action"})


# Keep this structure JSON-serialisable and deterministic.  The dashboard can
# render the field editor directly from this catalogue without importing Python
# validation code.  ``additional_properties`` is intentional: adapters may
# carry namespaced extension settings while the common fields stay typed.
_NODE_SCHEMAS: dict[str, dict[str, Any]] = {
    "start": {
        "label": "Start",
        "description": "Entry point for a flow.",
        "fields": [],
    },
    "end": {
        "label": "End",
        "description": "Terminal point for a flow.",
        "fields": [],
    },
    "task": {
        "label": "Task",
        "description": "Execute a governed worker run.",
        "required_any": ["worker_id", "team_id", "action"],
        "fields": [
            {
                "name": "worker_id",
                "label": "Worker",
                "type": "string",
                "widget": "worker-select",
                "description": "Concrete governed worker UUID for universal Worker Run dispatch.",
            },
            {
                "name": "team_id",
                "label": "Team ID",
                "type": "string",
                "widget": "text",
                "deprecated": True,
                "description": "Compatibility assignment for a team-owned task.",
            },
            {
                "name": "action",
                "label": "Legacy action",
                "type": "string",
                "widget": "text",
                "deprecated": True,
                "description": "Compatibility action; governed worker_id is preferred.",
            },
            {
                "name": "task_type",
                "label": "Task type",
                "type": "string",
                "widget": "text",
                "description": "Stable task type passed to the worker contract.",
            },
            {
                "name": "model_mode",
                "label": "Model mode",
                "type": "string",
                "widget": "select",
                "enum": ["none", "aiat_gateway", "certified_external_runtime", "hybrid"],
                "default": "aiat_gateway",
            },
            {
                "name": "model_profile_id",
                "label": "Model Profile",
                "type": "string",
                "widget": "model-profile-select",
                "description": "Governed profile; raw provider/model IDs are not accepted.",
            },
            {
                "name": "required_capabilities",
                "label": "Required capabilities",
                "type": "array",
                "items": "string",
                "widget": "csv",
                "default": [],
            },
            {
                "name": "permission_requirements",
                "label": "Permission requirements",
                "type": "array",
                "items": "string",
                "widget": "csv",
                "default": [],
            },
            {
                "name": "project_workspace_mode",
                "label": "Workspace mode",
                "type": "string",
                "widget": "select",
                "enum": ["isolated", "shared_readonly", "approved_write"],
                "default": "isolated",
            },
            {
                "name": "tool_grants",
                "label": "Tool grants",
                "type": "array",
                "items": "string",
                "widget": "csv",
                "default": [],
            },
            {
                "name": "timeout_seconds",
                "label": "Timeout (seconds)",
                "type": "integer",
                "widget": "number",
                "minimum": 1,
            },
            {
                "name": "retry_policy",
                "label": "Retry policy",
                "type": "object",
                "widget": "json",
                "default": {},
            },
            {
                "name": "cancellation_policy",
                "label": "Cancellation policy",
                "type": "object",
                "widget": "json",
                "default": {},
            },
            {
                "name": "checkpoint_policy",
                "label": "Checkpoint policy",
                "type": "object",
                "widget": "json",
                "default": {},
            },
            {
                "name": "artifact_expectations",
                "label": "Artifact expectations",
                "type": "array",
                "items": "object",
                "widget": "json",
                "default": [],
            },
            {
                "name": "completion_criteria",
                "label": "Completion criteria",
                "type": "object",
                "widget": "json",
                "default": {},
            },
            {
                "name": "runtime_extensions",
                "label": "Runtime extensions",
                "type": "object",
                "widget": "json",
                "default": {},
            },
        ],
    },
    "approval": {
        "label": "Approval",
        "description": "Pause until a configured authority approves or rejects.",
        "required_any": ["approver_role", "approver_user"],
        "fields": [
            {"name": "approver_role", "label": "Approver role", "type": "string", "widget": "text"},
            {"name": "approver_user", "label": "Approver user", "type": "string", "widget": "text"},
        ],
    },
    "condition": {
        "label": "Condition",
        "description": "Evaluate a bounded context/completion expression.",
        "fields": [
            {
                "name": "expression",
                "label": "Expression",
                "type": "string",
                "widget": "text",
                "required": True,
                "placeholder": "node_id completed or context.result == approved",
            }
        ],
    },
    "parallel": {
        "label": "Parallel",
        "description": "Fan out to branch roots and wait at a join.",
        "fields": [
            {
                "name": "branches",
                "label": "Branches",
                "type": "array",
                "items": "string",
                "widget": "csv",
                "required": True,
                "min_items": 1,
            }
        ],
    },
    "join": {
        "label": "Join",
        "description": "Wait for all incoming branches.",
        "fields": [],
    },
    "switch": {
        "label": "Switch",
        "description": "Route using a context field and case-to-node map.",
        "fields": [
            {
                "name": "switch_key",
                "label": "Switch key",
                "type": "string",
                "widget": "text",
                "required": True,
            },
            {
                "name": "switch_cases",
                "label": "Switch cases",
                "type": "object",
                "widget": "json",
                "required": True,
                "min_properties": 1,
                "placeholder": '{"approved": "node_approved"}',
            },
        ],
    },
    "escalate": {
        "label": "Escalate",
        "description": "Delegate a failure or decision to a higher authority.",
        "required_any": ["escalate_to_team", "escalate_to_agent"],
        "fields": [
            {"name": "escalate_to_team", "label": "Escalate to team", "type": "string", "widget": "text"},
            {"name": "escalate_to_agent", "label": "Escalate to agent", "type": "string", "widget": "text"},
        ],
    },
}


def node_schema_catalog() -> dict[str, Any]:
    """Return a defensive, JSON-serialisable copy of the canonical catalogue."""

    return {
        "schema_version": NODE_SCHEMA_VERSION,
        "catalog_id": "aiat.flow-node-schemas",
        "additional_properties": True,
        "node_types": deepcopy(_NODE_SCHEMAS),
    }


def _node_type_value(node_type: Any) -> str:
    return str(getattr(node_type, "value", node_type))


def schema_for_node_type(node_type: FlowNodeType | str) -> dict[str, Any] | None:
    """Return a defensive schema for one node type, or ``None`` if unknown."""

    key = _node_type_value(node_type)
    schema = _NODE_SCHEMAS.get(key)
    return deepcopy(schema) if schema is not None else None


def _matches_type(value: Any, expected: str) -> bool:
    if expected == "string":
        return isinstance(value, str)
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if expected == "boolean":
        return isinstance(value, bool)
    if expected == "array":
        return isinstance(value, list)
    if expected == "object":
        return isinstance(value, dict)
    return True


def validate_node_config_schema(node_type: FlowNodeType | str, config: dict[str, Any]) -> list[str]:
    """Validate common typed fields while allowing adapter extension fields."""

    key = _node_type_value(node_type)
    schema = _NODE_SCHEMAS.get(key)
    if schema is None:
        return [f"unknown node type '{key}'"]
    if not isinstance(config, dict):
        return ["config must be an object"]

    errors: list[str] = []
    fields = {str(field["name"]): field for field in schema.get("fields", [])}
    for field_name, field in fields.items():
        if field.get("required") and field_name not in config:
            errors.append(f"requires '{field_name}'")
        if field_name not in config:
            continue
        value = config[field_name]
        if value is None:
            if field.get("required"):
                errors.append(f"'{field_name}' must not be null")
            continue
        expected = str(field.get("type") or "")
        if not _matches_type(value, expected):
            errors.append(f"'{field_name}' must be {expected}")
            continue
        if expected == "string" and not value.strip():
            errors.append(f"'{field_name}' must not be empty")
        enum = field.get("enum")
        if enum and value not in enum:
            errors.append(f"'{field_name}' must be one of: {', '.join(str(item) for item in enum)}")
        if expected in {"integer", "number"} and field.get("minimum") is not None and value < field["minimum"]:
            errors.append(f"'{field_name}' must be >= {field['minimum']}")
        if expected == "array":
            if field.get("min_items") is not None and len(value) < field["min_items"]:
                errors.append(f"'{field_name}' must contain at least {field['min_items']} item(s)")
            item_type = field.get("items")
            if item_type:
                invalid = [item for item in value if not _matches_type(item, item_type)]
                if invalid:
                    errors.append(f"'{field_name}' items must be {item_type}")
        if expected == "object" and field.get("min_properties") is not None and len(value) < field["min_properties"]:
            errors.append(f"'{field_name}' must contain at least {field['min_properties']} entr(y/ies)")

    required_any = list(schema.get("required_any") or [])
    if required_any and not any(config.get(field) not in (None, "") for field in required_any):
        errors.append("requires at least one of: " + ", ".join(required_any))
    return errors


def audit_legacy_task_aliases(definition: Any) -> list[dict[str, Any]]:
    """Describe deprecated task aliases without changing a saved definition.

    ``team_id`` and ``action`` remain readable for old flows, but only a
    concrete ``worker_id`` can enter the universal Worker Run dispatch path.
    The returned records are intentionally suitable for API dry-run output and
    operator migration reports; this helper never guesses a worker UUID.
    """

    raw_nodes = getattr(definition, "nodes", None)
    if raw_nodes is None and isinstance(definition, dict):
        raw_nodes = definition.get("nodes")
    if not isinstance(raw_nodes, list):
        return []

    findings: list[dict[str, Any]] = []
    for raw_node in raw_nodes:
        node_type = getattr(raw_node, "type", None)
        node_type = getattr(node_type, "value", node_type)
        if node_type is None and isinstance(raw_node, dict):
            node_type = raw_node.get("type")
        if str(node_type) != "task":
            continue
        node_id = getattr(raw_node, "id", None)
        config = getattr(raw_node, "config", None)
        if node_id is None and isinstance(raw_node, dict):
            node_id = raw_node.get("id")
        if config is None and isinstance(raw_node, dict):
            config = raw_node.get("config")
        if not isinstance(config, dict):
            continue
        aliases = sorted(field for field in LEGACY_TASK_ALIAS_FIELDS if config.get(field) not in (None, ""))
        if not aliases:
            continue
        has_worker_id = config.get("worker_id") not in (None, "")
        if has_worker_id:
            recommendation = "remove deprecated aliases; preserve action as task_type when task_type is absent"
            disposition = "normalization_candidate"
        else:
            recommendation = "bind a concrete worker_id before activation; team_id/action cannot dispatch a Worker Run"
            disposition = "manual_worker_binding_required"
        findings.append(
            {
                "node_id": str(node_id),
                "deprecated_fields": aliases,
                "has_worker_id": has_worker_id,
                "disposition": disposition,
                "recommendation": recommendation,
            }
        )
    return sorted(findings, key=lambda item: item["node_id"])


def migrate_legacy_task_aliases(
    definition: Mapping[str, Any],
    *,
    worker_bindings: Mapping[str, UUID | str] | None = None,
    model_profile_bindings: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Build an immutable, worker-bound candidate from a saved flow.

    The compatibility fields ``team_id`` and ``action`` are deliberately
    accepted by the reader, but they are not enough to enter universal Worker
    Run dispatch.  This helper performs the operator-approved part of that
    migration without guessing a worker from a team.  It never mutates the
    supplied mapping and returns a structured preview suitable for both API
    dry-runs and the version-creation endpoint.

    A task without a concrete worker must have an explicit entry in
    ``worker_bindings``.  ``action`` is retained as ``task_type`` only when the
    canonical field is absent.  A legacy task with no model declaration is
    made model-less at the node layer so the worker's governed default model
    policy remains authoritative; an explicit model mode/profile is never
    silently changed.
    """

    candidate = deepcopy(dict(definition))
    raw_nodes = candidate.get("nodes")
    if not isinstance(raw_nodes, list):
        return {
            "definition_json": candidate,
            "changed": False,
            "migrated_node_ids": [],
            "missing_worker_bindings": [],
            "unknown_worker_bindings": sorted(str(key) for key in (worker_bindings or {})),
            "unknown_model_profile_bindings": sorted(
                str(key) for key in (model_profile_bindings or {})
            ),
            "errors": ["flow definition nodes must be a list"],
            "findings_before": [],
            "findings_after": [],
        }

    raw_worker_bindings = dict(worker_bindings or {})
    raw_profile_bindings = dict(model_profile_bindings or {})
    worker_binding_values: dict[str, str] = {}
    errors: list[dict[str, Any]] = []
    for node_id, value in sorted(raw_worker_bindings.items(), key=lambda item: str(item[0])):
        key = str(node_id)
        try:
            worker_binding_values[key] = str(UUID(str(value)))
        except (TypeError, ValueError):
            errors.append(
                {
                    "code": "INVALID_WORKER_BINDING",
                    "node_id": key,
                    "message": "worker binding must be a UUID",
                }
            )

    profile_binding_values: dict[str, str] = {}
    for node_id, value in sorted(raw_profile_bindings.items(), key=lambda item: str(item[0])):
        key = str(node_id)
        profile = str(value).strip()
        if not profile:
            errors.append(
                {
                    "code": "INVALID_MODEL_PROFILE_BINDING",
                    "node_id": key,
                    "message": "model profile binding must be a non-empty profile ID",
                }
            )
        else:
            profile_binding_values[key] = profile

    task_ids: set[str] = set()
    before_findings = audit_legacy_task_aliases(candidate)
    migrated_node_ids: list[str] = []
    missing_worker_bindings: list[str] = []
    changed = False

    for raw_node in raw_nodes:
        if not isinstance(raw_node, dict) or str(raw_node.get("type")) != "task":
            continue
        node_id = str(raw_node.get("id", ""))
        task_ids.add(node_id)
        config = raw_node.get("config")
        if not isinstance(config, dict):
            errors.append(
                {
                    "code": "INVALID_TASK_CONFIG",
                    "node_id": node_id,
                    "message": "task config must be an object",
                }
            )
            continue

        has_worker = config.get("worker_id") not in (None, "")
        supplied_worker = worker_binding_values.get(node_id)
        supplied_profile = profile_binding_values.get(node_id)
        bound_here = False
        if supplied_worker and has_worker:
            errors.append(
                {
                    "code": "WORKER_BINDING_NOT_REQUIRED",
                    "node_id": node_id,
                    "message": "task already has a concrete worker_id; refusing to rebind it",
                }
            )
        if supplied_profile and not has_worker and not supplied_worker:
            errors.append(
                {
                    "code": "MODEL_PROFILE_BINDING_REQUIRES_WORKER",
                    "node_id": node_id,
                    "message": "model profile bindings require a concrete worker binding",
                }
            )

        if not has_worker:
            if supplied_worker:
                config["worker_id"] = supplied_worker
                has_worker = True
                bound_here = True
                changed = True
            else:
                missing_worker_bindings.append(node_id)
                errors.append(
                    {
                        "code": "WORKER_BINDING_REQUIRED",
                        "node_id": node_id,
                        "message": "every task without worker_id requires an explicit worker binding",
                    }
                )

        aliases = sorted(
            field for field in LEGACY_TASK_ALIAS_FIELDS if config.get(field) not in (None, "")
        )
        if not has_worker or not aliases:
            # A profile-only change is still an explicit operator action, but
            # canonical alias removal is only recorded when aliases existed.
            if bound_here and "model_profile_id" not in config and "model_mode" not in config:
                config["model_mode"] = "none"
                changed = True
            if has_worker and supplied_profile:
                current_profile = config.get("model_profile_id")
                current_mode = config.get("model_mode")
                if current_profile and current_profile != supplied_profile:
                    errors.append(
                        {
                            "code": "MODEL_PROFILE_REBIND_NOT_ALLOWED",
                            "node_id": node_id,
                            "message": "task already declares a different model_profile_id",
                        }
                    )
                elif current_mode == "none":
                    errors.append(
                        {
                            "code": "MODEL_PROFILE_CONFLICT",
                            "node_id": node_id,
                            "message": "model_mode none cannot receive a model profile binding",
                        }
                    )
                else:
                    if config.get("model_profile_id") != supplied_profile:
                        config["model_profile_id"] = supplied_profile
                        changed = True
                    if "model_mode" not in config:
                        config["model_mode"] = "aiat_gateway"
                        changed = True
            if bound_here or supplied_profile:
                migrated_node_ids.append(node_id)
            continue

        action = config.get("action")
        if action not in (None, "") and config.get("task_type") in (None, ""):
            config["task_type"] = action
            changed = True
        for field in LEGACY_TASK_ALIAS_FIELDS:
            if field in config:
                config.pop(field, None)
                changed = True

        # A missing declaration is a legacy omission, not an instruction to
        # select a provider.  Preserve explicit model settings, while making
        # the common legacy case valid and worker-policy governed.
        if "model_profile_id" not in config and "model_mode" not in config:
            config["model_mode"] = "none"
            changed = True
        if supplied_profile:
            current_profile = config.get("model_profile_id")
            current_mode = config.get("model_mode")
            if current_profile and current_profile != supplied_profile:
                errors.append(
                    {
                        "code": "MODEL_PROFILE_REBIND_NOT_ALLOWED",
                        "node_id": node_id,
                        "message": "task already declares a different model_profile_id",
                    }
                )
            elif current_mode == "none":
                errors.append(
                    {
                        "code": "MODEL_PROFILE_CONFLICT",
                        "node_id": node_id,
                        "message": "model_mode none cannot receive a model profile binding",
                    }
                )
            else:
                if config.get("model_profile_id") != supplied_profile:
                    config["model_profile_id"] = supplied_profile
                    changed = True
                if "model_mode" not in config:
                    config["model_mode"] = "aiat_gateway"
                    changed = True
        migrated_node_ids.append(node_id)

    unknown_worker_bindings = sorted(set(worker_binding_values) - task_ids)
    unknown_profile_bindings = sorted(set(profile_binding_values) - task_ids)
    for node_id in unknown_worker_bindings:
        errors.append(
            {
                "code": "UNKNOWN_WORKER_BINDING_NODE",
                "node_id": node_id,
                "message": "worker binding must target a task node in the flow",
            }
        )
    for node_id in unknown_profile_bindings:
        errors.append(
            {
                "code": "UNKNOWN_MODEL_PROFILE_BINDING_NODE",
                "node_id": node_id,
                "message": "model profile binding must target a task node in the flow",
            }
        )

    # Validate the candidate here so callers get one deterministic report and
    # the API can refuse to persist a partially migrated version.
    try:
        from mas_core.workflow.flow_engine import (
            FlowValidationError,
            parse_flow_definition,
            validate_flow,
        )

        parsed = parse_flow_definition(candidate)
        errors.extend(
            {"code": "MIGRATED_FLOW_INVALID", "message": message}
            for message in validate_flow(parsed)
        )
    except (FlowValidationError, KeyError, TypeError, ValueError) as exc:
        errors.append({"code": "MIGRATED_FLOW_INVALID", "message": str(exc)})

    return {
        "definition_json": candidate,
        "changed": changed,
        "migrated_node_ids": sorted(set(migrated_node_ids)),
        "missing_worker_bindings": sorted(set(missing_worker_bindings)),
        "unknown_worker_bindings": unknown_worker_bindings,
        "unknown_model_profile_bindings": unknown_profile_bindings,
        "errors": errors,
        "findings_before": before_findings,
        "findings_after": audit_legacy_task_aliases(candidate),
    }


def validate_node_schema_version(version: str | None) -> list[str]:
    """Return an error for an unknown flow node schema version."""

    value = str(version or NODE_SCHEMA_VERSION)
    if value not in SUPPORTED_NODE_SCHEMA_VERSIONS:
        return [f"unsupported flow node schema version '{value}'"]
    return []
