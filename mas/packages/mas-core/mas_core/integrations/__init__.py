"""Provider-neutral integrations for AIAT work management and source control.

The package deliberately contains contracts and deterministic helpers only.
Provider clients belong behind these ports; canonical project/workflow state
continues to be written by the orchestrator.
"""

from .contracts import (
    AIAT_STABLE_PROJECT_FIELDS,
    DEDICATED_PROJECT_MAPPING_PROFILE,
    UMBRELLA_ISSUES_MAPPING_PROFILE,
    AdapterCapabilities,
    BootstrapAction,
    BootstrapApplyResult,
    BootstrapPlan,
    CanonicalIteration,
    CanonicalProject,
    CanonicalWorkItem,
    ExternalEvent,
    ExternalObject,
    IntegrationActor,
    NormalizedCommand,
    ProjectionResult,
    ProjectProvisioningApplyResult,
    ProjectProvisioningPlan,
    LifecyclePlanError,
    LifecyclePlanStatus,
    PMLifecycleTransitionPlan,
    ProviderConnection,
    pm_binding_effective_policy,
    normalize_project_mapping_profile,
)
from .ports import SourceControlProvider, WorkManagementProvider
from .registry import ProviderRegistry
from .conformance import (
    CONFORMANCE_FIXTURE_VERSION,
    ConformanceCaseResult,
    ProviderConformanceReport,
    run_work_management_conformance,
)

__all__ = [
    "AdapterCapabilities",
    "BootstrapAction",
    "BootstrapApplyResult",
    "BootstrapPlan",
    "ProjectProvisioningApplyResult",
    "ProjectProvisioningPlan",
    "LifecyclePlanError",
    "LifecyclePlanStatus",
    "PMLifecycleTransitionPlan",
    "pm_binding_effective_policy",
    "AIAT_STABLE_PROJECT_FIELDS",
    "DEDICATED_PROJECT_MAPPING_PROFILE",
    "UMBRELLA_ISSUES_MAPPING_PROFILE",
    "CanonicalIteration",
    "CanonicalProject",
    "CanonicalWorkItem",
    "ExternalEvent",
    "ExternalObject",
    "IntegrationActor",
    "NormalizedCommand",
    "ProjectionResult",
    "ProviderConnection",
    "normalize_project_mapping_profile",
    "SourceControlProvider",
    "WorkManagementProvider",
    "ProviderRegistry",
    "CONFORMANCE_FIXTURE_VERSION",
    "ConformanceCaseResult",
    "ProviderConformanceReport",
    "run_work_management_conformance",
]
