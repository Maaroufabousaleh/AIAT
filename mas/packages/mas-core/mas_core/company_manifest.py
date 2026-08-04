"""Versioned AIAT company manifest models and deterministic compilation helpers.

The manifest is deliberately data-only.  It describes an AIAT company but
cannot grant authority that is not already represented by the control plane.
The orchestrator persists the canonical JSON and its digest before compiling
departments, assignments, and budgets.
"""

from __future__ import annotations

import hashlib
import json
from typing import Annotated, Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

DEFAULT_COMPANY_ID = UUID("00000000-0000-4000-8000-000000000001")
NonNegativeFiniteFloat = Annotated[float, Field(ge=0, allow_inf_nan=False)]


class CompanyManifestError(ValueError):
    """A manifest cannot be safely compiled."""


class DepartmentManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1, max_length=120, pattern=r"^[a-z][a-z0-9_-]*$")
    name: str = Field(min_length=1, max_length=200)
    chief_worker_id: str | None = None
    worker_ids: list[str] = Field(default_factory=list)
    approval_policy: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("worker_ids")
    @classmethod
    def unique_workers(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)):
            raise ValueError("department worker_ids must be unique")
        return value


class WorkerAssignmentManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    worker_id: str = Field(min_length=1, max_length=120)
    department_id: str = Field(min_length=1, max_length=120)
    status: str = Field(default="ACTIVE", pattern=r"^(ACTIVE|INACTIVE|DRAINING)$")
    tool_grants: list[str] = Field(default_factory=list)
    permission_grants: list[str] = Field(default_factory=list)
    model_profile_id: str | None = None
    budget: dict[str, NonNegativeFiniteFloat] = Field(default_factory=dict)
    approval_required: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("tool_grants", "permission_grants")
    @classmethod
    def unique_grants(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)):
            raise ValueError("worker grants must be unique")
        return value


class CompanyManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: str = Field(default="1", pattern=r"^1(?:\.\d+)?$")
    slug: str = Field(min_length=1, max_length=120, pattern=r"^[a-z][a-z0-9-]*$")
    name: str = Field(min_length=1, max_length=200)
    description: str = ""
    ceo_worker_id: str = Field(min_length=1, max_length=120)
    departments: list[DepartmentManifest] = Field(min_length=1)
    worker_assignments: list[WorkerAssignmentManifest] = Field(default_factory=list)
    budgets: dict[str, NonNegativeFiniteFloat] = Field(default_factory=dict)
    policy: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_references(self) -> CompanyManifest:
        department_ids = {item.id for item in self.departments}
        if len(department_ids) != len(self.departments):
            raise CompanyManifestError("department IDs must be unique")
        assignments = {item.worker_id: item for item in self.worker_assignments}
        if len(assignments) != len(self.worker_assignments):
            raise CompanyManifestError("worker assignments must be unique")
        for department in self.departments:
            if department.chief_worker_id and department.chief_worker_id not in assignments:
                raise CompanyManifestError(
                    f"department {department.id!r} chief {department.chief_worker_id!r} "
                    "must have a worker assignment"
                )
            if department.chief_worker_id:
                chief_assignment = assignments[department.chief_worker_id]
                if chief_assignment.department_id != department.id:
                    raise CompanyManifestError(
                        f"department {department.id!r} chief {department.chief_worker_id!r} "
                        f"is assigned to {chief_assignment.department_id!r}"
                    )
            for worker_id in department.worker_ids:
                assignment = assignments.get(worker_id)
                if assignment is None:
                    raise CompanyManifestError(
                        f"department {department.id!r} references unassigned worker {worker_id!r}"
                    )
                if assignment.department_id != department.id:
                    raise CompanyManifestError(
                        f"worker {worker_id!r} is assigned to {assignment.department_id!r}, "
                        f"not department {department.id!r}"
                    )
        for assignment in self.worker_assignments:
            if assignment.department_id not in department_ids:
                raise CompanyManifestError(
                    f"worker {assignment.worker_id!r} references unknown department "
                    f"{assignment.department_id!r}"
                )
            if assignment.worker_id != self.ceo_worker_id and any(
                grant in {"company.manage", "company.delete", "approval.decide"}
                for grant in assignment.permission_grants
            ):
                raise CompanyManifestError(
                    f"worker {assignment.worker_id!r} cannot receive executive authority grants"
                )
        if self.ceo_worker_id not in assignments:
            raise CompanyManifestError("ceo_worker_id must have a worker assignment")
        if not any(
            department.chief_worker_id == self.ceo_worker_id
            for department in self.departments
        ):
            raise CompanyManifestError("ceo_worker_id must be the chief of at least one department")
        return self

    def canonical_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="json", exclude_none=True)

    def digest(self) -> str:
        payload = json.dumps(self.canonical_dict(), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def compile_company_manifest(raw: dict[str, Any]) -> tuple[CompanyManifest, str, dict[str, Any]]:
    """Validate and canonicalize one manifest without performing side effects."""

    try:
        manifest = CompanyManifest.model_validate(raw)
    except Exception as exc:  # Pydantic's nested errors are returned by the API.
        if isinstance(exc, CompanyManifestError):
            raise
        raise CompanyManifestError(str(exc)) from exc
    canonical = manifest.canonical_dict()
    return manifest, manifest.digest(), canonical
