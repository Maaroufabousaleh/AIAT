# ruff: noqa
"""GENERATED FILE — do not edit by hand."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, NotRequired, Required, TypeAlias, TypedDict

class AgentEstimateRequest(TypedDict):
    raw_estimate_hours: Required[float]

class AgentProfileObservationRequest(TypedDict):
    actual_hours: NotRequired[float]
    alpha: NotRequired[float]
    estimated_hours: NotRequired[float]
    role: NotRequired[str | Any]
    tasks_completed: NotRequired[int]
    team_id: NotRequired[str | Any]

class BootstrapAction(TypedDict):
    action: Required[str]
    current: NotRequired[dict[str, Any] | Any]
    desired: NotRequired[dict[str, Any]]
    destructive: NotRequired[bool]
    manual: NotRequired[bool]
    reason: NotRequired[str | Any]
    resource: Required[str]

class BootstrapPlan(TypedDict):
    actions: NotRequired[list[BootstrapAction]]
    blockers: NotRequired[list[str]]
    checks: NotRequired[list[str]]
    connection_id: Required[str]
    generated_at: NotRequired[str]
    plan_id: NotRequired[str]
    provider_kind: Required[str]
    rollback_actions: NotRequired[list[str]]

class CandidateApprovalRequest(TypedDict):
    decided_by: Required[str]
    evidence: NotRequired[dict[str, Any]]
    reason: Required[str]

class CandidateCertificationRequest(TypedDict):
    checks: NotRequired[dict[str, bool]]
    conformance: NotRequired[dict[str, Any]]

class CandidateGenerationRequest(TypedDict):
    adapter_entrypoint: NotRequired[str | Any]
    adapter_version: Required[str]
    diff: NotRequired[dict[str, Any]]
    implementation_ref: NotRequired[str | Any]
    migration_notes: NotRequired[list[str]]
    semantic_version: Required[str]
    upstream_compatibility_range: Required[str]

class CandidateStageAdvanceRequest(TypedDict):
    actor: Required[str]
    evidence: NotRequired[dict[str, Any]]
    target_status: Required[str]

class CanonicalIssueCommentRequest(TypedDict):
    actor_id: NotRequired[str]
    approval_id: NotRequired[str | Any]
    body: Required[str]
    body_blob_ref: NotRequired[str | Any]
    evidence_id: NotRequired[str | Any]
    run_id: NotRequired[str | Any]

class CanonicalIssueCreateRequest(TypedDict):
    assigned_agent: NotRequired[str | Any]
    assigned_team: NotRequired[str | Any]
    description: NotRequired[str | Any]
    estimated_hours: NotRequired[float | Any]
    issue_type: NotRequired[str]
    priority: NotRequired[str]
    sprint_id: NotRequired[str | Any]
    story_points: NotRequired[int | Any]
    title: NotRequired[str]

class CanonicalIssueLinkRequest(TypedDict):
    link_type: Required[str]
    metadata: NotRequired[dict[str, Any]]
    target_id: Required[str]
    target_type: Required[str]

class CanonicalIssueUpdateRequest(TypedDict):
    actual_hours: NotRequired[float | Any]
    assigned_agent: NotRequired[str | Any]
    assigned_team: NotRequired[str | Any]
    description: NotRequired[str | Any]
    estimated_hours: NotRequired[float | Any]
    expected_revision: NotRequired[int | Any]
    priority: NotRequired[str | Any]
    status: NotRequired[str | Any]
    story_points: NotRequired[int | Any]
    title: NotRequired[str | Any]

class CanonicalSprintCreateRequest(TypedDict):
    estimated_hours: NotRequired[float | Any]
    goal: NotRequired[str | Any]
    milestone: NotRequired[str | Any]
    planned_story_points: NotRequired[int | Any]
    sprint_number: NotRequired[int]

class CanonicalSprintUpdateRequest(TypedDict):
    actual_hours: NotRequired[float | Any]
    completed_story_points: NotRequired[int | Any]
    estimated_hours: NotRequired[float | Any]
    expected_revision: NotRequired[int | Any]
    goal: NotRequired[str | Any]
    milestone: NotRequired[str | Any]
    planned_story_points: NotRequired[int | Any]
    status: NotRequired[str | Any]

class CapabilitySearchRequest(TypedDict):
    min_sandbox_tier: NotRequired[int]
    name: NotRequired[str | Any]
    role: NotRequired[str | Any]

class CapacityForecast(TypedDict):
    active_project_count: NotRequired[int]
    average_daily_cost_usd: NotRequired[float]
    average_daily_tokens: NotRequired[float]
    basis: NotRequired[Literal['project_usage_events']]
    budget_limit_usd: NotRequired[float | Any]
    budget_source: NotRequired[Literal['company_budgets', 'not_configured', 'caller']]
    confidence: NotRequired[Literal['high', 'medium', 'low', 'none']]
    forecast_days: Required[int]
    generated_at: NotRequired[str | Any]
    notices: NotRequired[list[dict[str, str]]]
    observed_cost_usd: NotRequired[float]
    observed_event_count: NotRequired[int]
    observed_span_days: NotRequired[float]
    observed_total_tokens: NotRequired[int]
    projected_budget_headroom_usd: NotRequired[float | Any]
    projected_cost_usd: NotRequired[float]
    projected_tokens: NotRequired[int]
    schema_version: NotRequired[str]
    status: Required[Literal['clear', 'attention', 'insufficient_data']]
    window_days: Required[int]

class ChatCompletionRequest(TypedDict):
    max_tokens: NotRequired[int | Any]
    messages: Required[list[ChatMessage]]
    model: NotRequired[str]
    response_format: NotRequired[dict[str, Any] | Any]
    stop: NotRequired[str | list[str] | Any]
    stream: NotRequired[bool]
    temperature: NotRequired[float | Any]
    tool_choice: NotRequired[str | dict[str, Any] | Any]
    tools: NotRequired[list[dict[str, Any]] | Any]
    top_p: NotRequired[float | Any]
    user: NotRequired[str | Any]

class ChatMessage(TypedDict):
    content: NotRequired[str | list[dict[str, Any]] | Any]
    name: NotRequired[str | Any]
    role: Required[str]
    tool_call_id: NotRequired[str | Any]
    tool_calls: NotRequired[list[dict[str, Any]] | Any]

ChunkingStrategy: TypeAlias = Literal['fixed_size', 'semantic', 'sliding_window']

class CompanyCreateRequest(TypedDict):
    created_by: NotRequired[str]
    description: NotRequired[str]
    name: Required[str]
    slug: Required[str]

class CompanyManifestRequest(TypedDict):
    manifest: Required[dict[str, Any]]
    source: NotRequired[str]

class CompanyManifestRollbackRequest(TypedDict):
    manifest_version: Required[int]
    reason: Required[str]

class CreateArtifactRequest(TypedDict):
    agent_id: NotRequired[str]
    metadata: NotRequired[dict[str, Any]]
    path: Required[str]
    sha256: NotRequired[str | Any]
    size_bytes: NotRequired[int | Any]

class CreateContextItemRequest(TypedDict):
    blob_bucket: NotRequired[str | Any]
    blob_key: NotRequired[str | Any]
    blob_sha256: NotRequired[str | Any]
    chunk_overlap: NotRequired[int]
    chunk_size: NotRequired[int]
    chunking_strategy: NotRequired[ChunkingStrategy]
    content_text: NotRequired[str | Any]
    description: NotRequired[str | Any]
    generate_embeddings: NotRequired[bool]
    item_type: Required[str]
    metadata: NotRequired[dict[str, Any] | Any]
    mime_type: NotRequired[str | Any]
    name: Required[str]
    size_bytes: NotRequired[int | Any]
    tags: NotRequired[list[str] | Any]
    url: NotRequired[str | Any]

class CreateCredentialRequest(TypedDict):
    created_by: NotRequired[str]
    description: NotRequired[str]
    name: Required[str]
    policy: NotRequired[dict[str, Any]]
    secret_type: NotRequired[str]
    value: Required[str]

class CreateDocumentRequest(TypedDict):
    blob_bucket: NotRequired[str | Any]
    blob_key: NotRequired[str | Any]
    blob_sha256: NotRequired[str | Any]
    created_by: NotRequired[str]
    doc_type: Required[str]

class CreateDocumentRevisionRequest(TypedDict):
    blob_bucket: NotRequired[str | Any]
    blob_key: NotRequired[str | Any]
    blob_sha256: NotRequired[str | Any]
    created_by: NotRequired[str]

class CreateFlowInstanceRequest(TypedDict):
    department_id: NotRequired[str | Any]
    flow_id: Required[str]
    project_id: Required[str]
    task_id: NotRequired[str | Any]

class CreateFlowRequest(TypedDict):
    created_by: NotRequired[str]
    definition_json: Required[dict[str, Any]]
    description: NotRequired[str | Any]
    is_active: NotRequired[bool]
    name: Required[str]
    version_from_flow_id: NotRequired[str | Any]

class CreateProjectRequest(TypedDict):
    company_id: NotRequired[str]
    config: NotRequired[dict[str, Any] | Any]
    description: NotRequired[str | Any]
    flow_id: NotRequired[str | Any]
    human_requester: NotRequired[str | Any]
    initial_context: NotRequired[list[ProjectContextSeedRequest]]
    name: Required[str]
    workspace: NotRequired[ProjectWorkspaceRequest | Any]

class CredentialApprovalDecisionRequest(TypedDict):
    approved: Required[bool]
    decided_by: NotRequired[str]
    reason: NotRequired[str]

class CredentialApprovalRequest(TypedDict):
    context: Required[str]
    requested_by: NotRequired[str]
    requester: Required[str]
    ttl_seconds: NotRequired[int]

class DashboardSectionACLRequest(TypedDict):
    principals: NotRequired[list[str]]

class DecisionRequest(TypedDict):
    comments: NotRequired[str | Any]
    decided_by: NotRequired[str]
    decision: Required[str]
    edits: NotRequired[dict[str, Any] | Any]

class DoclingCertificationRequest(TypedDict):
    artifact_path: NotRequired[str | Any]
    content_text: NotRequired[str | Any]
    mime_type: NotRequired[str]
    project_id: NotRequired[str | Any]
    source_name: NotRequired[str]

class DocumentStatusRequest(TypedDict):
    status: Required[str]

class DocumentationSnapshotRequest(TypedDict):
    content_ref: NotRequired[str | Any]
    content_sha256: Required[str]
    extracted_interfaces: NotRequired[dict[str, Any]]
    security_findings: NotRequired[list[str]]
    untrusted: NotRequired[bool]
    uri: Required[str]
    version: Required[str]

class EvidencePolicyRequest(TypedDict):
    milestone: NotRequired[str | Any]
    policy_id: Required[str]
    policy_version: NotRequired[str]
    requirements: NotRequired[dict[str, Any]]
    scope: NotRequired[Literal['project', 'milestone']]

class ExecutiveCEOPrivilegedActionRequest(TypedDict):
    action: Required[str]
    payload: NotRequired[dict[str, Any]]
    requested_by: NotRequired[str]

class ExecutiveCFOModelOverrideRequest(TypedDict):
    project_id: Required[str]
    reason: Required[str]
    requested_by: NotRequired[str]
    requested_profile_id: Required[str]
    scope: NotRequired[dict[str, Any]]

class ExecutiveCTOWorkerRunRequest(TypedDict):
    dispatch: Required[WorkerRunDispatchRequest]
    requested_by: NotRequired[str]

class FlowDiffRequest(TypedDict):
    from_flow_id: Required[str]
    to_flow_id: Required[str]

class FlowDryRunRequest(TypedDict):
    definition_json: Required[dict[str, Any]]
    project_id: NotRequired[str | Any]

class FlowFromTemplateRequest(TypedDict):
    created_by: NotRequired[str]
    description: NotRequired[str | Any]
    is_active: NotRequired[bool]
    name: NotRequired[str | Any]
    template_id: Required[str]

class FlowImportRequest(TypedDict):
    created_by: NotRequired[str]
    definition_json: Required[dict[str, Any]]
    description: NotRequired[str | Any]
    is_active: NotRequired[bool]
    name: Required[str]
    version_from_flow_id: NotRequired[str | Any]

class FlowInstanceActionRequest(TypedDict):
    action: Required[str]
    node_id: NotRequired[str | Any]

class FlowLegacyTaskMigrationRequest(TypedDict):
    actor_id: NotRequired[str]
    description: NotRequired[str | Any]
    dry_run: NotRequired[bool]
    is_active: NotRequired[bool]
    model_profile_bindings: NotRequired[dict[str, str]]
    name: NotRequired[str | Any]
    worker_bindings: NotRequired[dict[str, str]]

class FlowMigrationRequest(TypedDict):
    active_node_mapping: NotRequired[dict[str, str]]
    actor_id: NotRequired[str]
    allow_graph_rewrite: NotRequired[bool]
    flow_id: Required[str]
    preserve_context: NotRequired[bool]

class FlowNodeActionRequest(TypedDict):
    action: Required[str]
    approved: NotRequired[bool | Any]
    decision: NotRequired[str | Any]
    error: NotRequired[str | Any]
    node_id: Required[str]
    output: NotRequired[dict[str, Any] | Any]
    worker_run_id: NotRequired[str | Any]

class FlowOverrideRequest(TypedDict):
    actor_id: NotRequired[str]
    actor_role: NotRequired[str]
    reason: NotRequired[str | Any]
    target_node_id: Required[str]

GateName: TypeAlias = Literal['coding', 'testing', 'review', 'security', 'migration', 'rollback', 'human_approval']

class GitHubMetadataRequest(TypedDict):
    credential_approval_id: NotRequired[str | Any]
    credential_name: NotRequired[str | Any]
    dry_run: NotRequired[bool]
    repo_url: Required[str]
    requester: NotRequired[str]

class HTTPValidationError(TypedDict):
    detail: NotRequired[list[ValidationError]]

class HybridSearchRequest(TypedDict):
    filters: NotRequired[dict[str, Any] | Any]
    limit: NotRequired[int]
    query: Required[str]
    query_vector: NotRequired[list[float] | Any]
    use_semantic: NotRequired[bool]

class IdentityDashboardActionRequest(TypedDict):
    action: Required[Literal['approval.approve', 'approval.reject', 'identity.suspend', 'identity.archive', 'external.rotate_credentials', 'external.suspend', 'external.close', 'session.revoke']]
    id: NotRequired[str | Any]
    reason: NotRequired[str]
    service: NotRequired[str | Any]
    service_category: NotRequired[str]
    worker_id: NotRequired[str | Any]

class ImportWorkersRequest(TypedDict):
    dry_run: NotRequired[bool]
    workers_dir: NotRequired[str]

class ImprovementArtifact(TypedDict):
    artifact_id: NotRequired[str]
    candidate_version: Required[str]
    canonical_artifact_id: NotRequired[str | Any]
    immutable: NotRequired[Literal[True]]
    kind: Required[ImprovementArtifactKind]
    metadata: NotRequired[dict[str, str]]
    sha256: Required[str]
    size_bytes: Required[int]
    source_revision: Required[str]
    target_version: NotRequired[str | Any]
    uri: Required[str]

class ImprovementArtifactBundle(TypedDict):
    artifacts: Required[list[ImprovementArtifact]]
    bundle_id: NotRequired[str]
    candidate_version: Required[str]
    generated_by: Required[str]
    generated_by_kind: Required[Literal['human', 'agent', 'system']]
    manifest_sha256: NotRequired[str]
    metadata: NotRequired[dict[str, str]]
    schema_version: NotRequired[str]

ImprovementArtifactKind: TypeAlias = Literal['change', 'provenance', 'sbom', 'migration', 'rollback']

class ImprovementOpportunity(TypedDict):
    budget_usd: Required[float | str]
    company_id: NotRequired[str | Any]
    created_by: Required[str]
    created_by_kind: Required[Literal['human', 'agent', 'system']]
    description: Required[str]
    evidence_policy: Required[str]
    licence_metadata: NotRequired[dict[str, Any]]
    opportunity_id: NotRequired[str]
    owner: Required[str]
    owner_kind: NotRequired[Literal['human', 'agent', 'system']]
    risk: Required[ImprovementRisk]
    source: Required[str]
    title: Required[str]

ImprovementOutcomeKind: TypeAlias = Literal['success', 'failure', 'rolled_back', 'cancelled']

ImprovementRisk: TypeAlias = Literal['low', 'medium', 'high', 'critical']

class KpiSnapshotRequest(TypedDict):
    budget_adherence: NotRequired[float | Any]
    defect_rate: NotRequired[float | Any]
    estimation_accuracy: NotRequired[float | Any]
    infra_lead_time_seconds: NotRequired[int | Any]
    raw_data: NotRequired[dict[str, Any] | Any]
    resource_utilization: NotRequired[float | Any]
    review_pass_rate: NotRequired[float | Any]
    rework_rate: NotRequired[float | Any]
    scope: Required[str]
    sprint_id: NotRequired[str | Any]
    task_completion_rate: NotRequired[float | Any]
    velocity: NotRequired[float | Any]

class LegacyCompletionRequest(TypedDict):
    max_tokens: NotRequired[int | Any]
    model: NotRequired[str]
    prompt: Required[str | list[str]]
    stream: NotRequired[bool]
    temperature: NotRequired[float | Any]

class ModelOverrideCreateRequest(TypedDict):
    project_id: Required[str]
    reason: Required[str]
    requested_by: Required[str]
    requested_profile_id: Required[str]
    scope: NotRequired[dict[str, Any]]

class ModelOverrideDecisionRequest(TypedDict):
    decided_by: Required[str]
    decision: Required[Literal['APPROVED', 'REJECTED']]
    evidence: NotRequired[dict[str, Any]]
    expires_at: NotRequired[str | Any]
    reason: Required[str]

class ModelProfileCreateRequest(TypedDict):
    approved_provider_ids: NotRequired[list[str]]
    fallback_profile_ids: NotRequired[list[str]]
    profile_id: Required[str]
    purpose: Required[str]
    required_capabilities: NotRequired[list[str]]
    status: NotRequired[str]

class ModelProfileVersionRequest(TypedDict):
    capabilities: NotRequired[list[str]]
    context_window: NotRequired[int]
    cost_per_1k_input_usd: NotRequired[float]
    cost_per_1k_output_usd: NotRequired[float]
    effective_from: NotRequired[str | Any]
    effective_until: NotRequired[str | Any]
    embedding: NotRequired[bool]
    exact_model_id: Required[str]
    latency_target_ms: NotRequired[int | Any]
    local: NotRequired[bool]
    max_concurrency: NotRequired[int | Any]
    max_cost_usd: NotRequired[float | Any]
    max_output_tokens: NotRequired[int]
    max_tokens_per_request: NotRequired[int | Any]
    privacy_class: NotRequired[str]
    provider_id: Required[str]
    provider_settings: NotRequired[dict[str, Any]]
    reasoning: NotRequired[bool]
    regions: NotRequired[list[str]]
    status: NotRequired[str]
    streaming: NotRequired[bool]
    structured_output: NotRequired[bool]
    tool_calling: NotRequired[bool]
    version: Required[str]
    vision: NotRequired[bool]

class ModelResolutionPreviewRequest(TypedDict):
    adapter_required_capabilities: NotRequired[list[str]]
    budget_usd: NotRequired[float | Any]
    expected_output_tokens: NotRequired[int]
    layers: NotRequired[list[dict[str, Any]]]
    prompt_tokens: NotRequired[int]
    requested_profile_id: NotRequired[str | Any]
    requested_raw_model_id: NotRequired[str | Any]
    steward_required_capabilities: NotRequired[list[str]]
    task_required_capabilities: NotRequired[list[str]]
    task_type: Required[str]
    worker_required_capabilities: NotRequired[list[str]]

class N8nEdgePolicyRequest(TypedDict):
    allow_control_plane: NotRequired[bool]
    credential_name: NotRequired[str | Any]
    owner_department: NotRequired[str]
    webhook_url: Required[str]

class OperatorToCeoRequest(TypedDict):
    async_mode: NotRequired[bool]
    context_confirmation_token: NotRequired[str | Any]
    context_worker_id: NotRequired[str | Any]
    message: Required[str]
    request_id: NotRequired[str | Any]

class PMApplyRequest(TypedDict):
    confirm: NotRequired[bool]
    plan: Required[BootstrapPlan]
    plan_digest: Required[str]

class PMBindingCreateRequest(TypedDict):
    connection_id: Required[str]
    direction: NotRequired[Literal['outbound', 'inbound', 'both']]
    external_project_id: NotRequired[str | Any]
    external_project_key: NotRequired[str | Any]
    external_repository: NotRequired[str | Any]
    mapping_profile: NotRequired[str]
    status: NotRequired[Literal['DISABLED', 'SHADOW', 'READ_ONLY', 'ACTIVE', 'DRAINING']]

class PMBindingUpdateRequest(TypedDict):
    direction: NotRequired[Literal['outbound', 'inbound', 'both'] | Any]
    external_project_id: NotRequired[str | Any]
    external_project_key: NotRequired[str | Any]
    external_repository: NotRequired[str | Any]
    mapping_profile: NotRequired[str | Any]
    status: NotRequired[Literal['DISABLED', 'SHADOW', 'READ_ONLY', 'ACTIVE', 'DRAINING'] | Any]

class PMConflictResolutionRequest(TypedDict):
    resolution: NotRequired[dict[str, Any]]
    status: NotRequired[Literal['RESOLVED', 'IGNORED', 'REOPENED']]

class PMConnectionCreateRequest(TypedDict):
    base_url: Required[str]
    capability_profile: NotRequired[str]
    config: NotRequired[dict[str, Any]]
    created_by: NotRequired[str]
    credential_ref: Required[str]
    display_name: Required[str]
    provider_kind: Required[str]

class PMConnectionStatusRequest(TypedDict):
    status: Required[Literal['DISABLED', 'SHADOW', 'READ_ONLY', 'ACTIVE', 'DRAINING']]

class PMCutoverRequest(TypedDict):
    binding_id: Required[str]
    confirm: NotRequired[bool]
    project_id: Required[str]

class PMExternalActorMappingCreateRequest(TypedDict):
    authorized_scopes: NotRequired[list[Literal['issue.priority']]]
    inbox_event_ids: Required[list[str]]
    reason: NotRequired[str]

class PMInboundCanaryPlanActionRequest(TypedDict):
    confirm: NotRequired[bool]
    digest: Required[str]
    reason: NotRequired[str | Any]

class PMInboundCanaryPlanCreateRequest(TypedDict):
    actor_mapping_id: Required[str]
    binding_id: Required[str]
    canonical_issue_id: Required[str]
    external_issue_id: Required[str]
    mapping_id: Required[str]
    target_priority: NotRequired[Literal['low', 'medium', 'high', 'urgent', 'critical', 'normal'] | Any]
    ttl_seconds: NotRequired[int]

class PMInboundCanaryReplayRequest(TypedDict):
    confirm: NotRequired[bool]
    digest: Required[str]
    inbox_id: Required[str]
    reason: Required[str]

class PMLifecyclePlanApplyRequest(TypedDict):
    confirm: NotRequired[bool]
    plan_digest: Required[str]

class PMLifecyclePlanApprovalRequest(TypedDict):
    plan_digest: Required[str]
    reason: NotRequired[str | Any]

class PMLifecyclePlanCreateRequest(TypedDict):
    binding_id: NotRequired[str | Any]
    connection_id: Required[str]
    desired_binding_status: NotRequired[Literal['DISABLED', 'SHADOW', 'READ_ONLY', 'ACTIVE', 'DRAINING'] | Any]
    desired_connection_status: NotRequired[Literal['DISABLED', 'SHADOW', 'READ_ONLY', 'ACTIVE', 'DRAINING'] | Any]
    target_type: NotRequired[Literal['pm_connection', 'pm_binding']]
    ttl_seconds: NotRequired[int]

class PMLifecyclePlanRejectRequest(TypedDict):
    plan_digest: Required[str]
    reason: NotRequired[str | Any]

class PMOutboxDispositionRequest(TypedDict):
    disposition: Required[Literal['RESOLVED', 'SUPERSEDED']]
    provider_state: NotRequired[dict[str, Any]]
    reason: Required[str]

class PMPlanRequest(TypedDict):
    desired: NotRequired[dict[str, Any]]

class PMProjectProvisioningApplyRequest(TypedDict):
    confirm: NotRequired[bool]
    plan: Required[ProjectProvisioningPlan]
    plan_digest: Required[str]

class PMProjectProvisioningRequest(TypedDict):
    connection_id: Required[str]
    external_project_id: NotRequired[str | Any]
    mapping_profile: NotRequired[str]

class PMReconcileRequest(TypedDict):
    binding_id: NotRequired[str | Any]
    cursor: NotRequired[str | Any]
    limit: NotRequired[int]
    mode: NotRequired[Literal['audit', 'repair_proposal']]

class PMRollbackRequest(TypedDict):
    binding_id: Required[str]
    confirm: NotRequired[bool]
    project_id: Required[str]

class PrivilegedActionRequest(TypedDict):
    action: Required[str]
    actor_id: NotRequired[str]
    actor_role: NotRequired[str]
    payload: NotRequired[dict[str, Any]]

class PrivilegedApprovalRequest(TypedDict):
    approved: Required[bool]
    decided_by: Required[str]
    reason: NotRequired[str]

class ProjectContextSeedRequest(TypedDict):
    blob_bucket: NotRequired[str | Any]
    blob_key: NotRequired[str | Any]
    blob_sha256: NotRequired[str | Any]
    content_text: NotRequired[str | Any]
    description: NotRequired[str | Any]
    item_type: NotRequired[str]
    metadata: NotRequired[dict[str, Any] | Any]
    mime_type: NotRequired[str | Any]
    name: NotRequired[str]
    size_bytes: NotRequired[int | Any]
    tags: NotRequired[list[str]]
    url: NotRequired[str | Any]

class ProjectEvidencePolicyRequest(TypedDict):
    milestone: NotRequired[str | Any]
    policy_id: Required[str]
    policy_version: NotRequired[str]
    requirements: NotRequired[dict[str, Any]]
    scope: NotRequired[Literal['project', 'milestone']]

class ProjectProvisioningPlan(TypedDict):
    actions: NotRequired[list[BootstrapAction]]
    blockers: NotRequired[list[str]]
    checks: NotRequired[list[str]]
    connection_id: Required[str]
    external_project_id: NotRequired[str | Any]
    external_project_key: NotRequired[str | Any]
    generated_at: NotRequired[str]
    manual_actions: NotRequired[list[str]]
    mapping_profile: NotRequired[str]
    plan_id: NotRequired[str]
    project_id: Required[str]
    provider_kind: Required[str]
    rollback_actions: NotRequired[list[str]]

class ProjectRepositoryActionRequest(TypedDict):
    message: NotRequired[str | Any]
    operation: NotRequired[Literal['status', 'sync', 'commit', 'push']]

class ProjectWorkspaceRequest(TypedDict):
    branch: NotRequired[str | Any]
    mode: NotRequired[Literal['init', 'clone', 'none']]
    remote_name: NotRequired[str]
    repository_url: NotRequired[str | Any]

class RegisterWorkerRequest(TypedDict):
    adapter_config: NotRequired[dict[str, Any]]
    adapter_type: Required[str]
    capability_ids: NotRequired[list[str]]
    capability_names: NotRequired[list[str]]
    identity_mailbox_class: NotRequired[str]
    model_mode: NotRequired[str]
    model_profile_id: NotRequired[str | Any]
    name: Required[str]
    required_tools: NotRequired[list[str]]
    role: NotRequired[str | Any]
    sandbox_profile: NotRequired[str]
    source_repo: NotRequired[str | Any]
    team_id: NotRequired[str | Any]
    update_policy: NotRequired[str]
    version_pin: NotRequired[str | Any]

class ResolveCredentialRequest(TypedDict):
    context: NotRequired[str]
    requester: NotRequired[str]

class RollbackRequest(TypedDict):
    reason: Required[str]

class RolloutAdvanceRequest(TypedDict):
    comparison_metrics: NotRequired[dict[str, float]]
    sample_count: NotRequired[int | Any]
    target_status: Required[str]

class RolloutStartRequest(TypedDict):
    actor: Required[str]
    eligible_task_classes: NotRequired[list[str]]

class RuntimeValidationRequest(TypedDict):
    dry_run: NotRequired[bool]
    runtime_config: NotRequired[dict[str, Any]]
    runtime_tier: Required[str]

class SCMActionRequest(TypedDict):
    payload: NotRequired[dict[str, Any]]

class SLOPolicy(TypedDict):
    policy_version: NotRequired[str]
    schema_version: NotRequired[str]
    source: NotRequired[Literal['aiat_default', 'company_manifest']]
    targets: NotRequired[list[SLOTarget]]

class SLOReport(TypedDict):
    generated_at: NotRequired[str | Any]
    notices: NotRequired[list[dict[str, str]]]
    observed_service_count: NotRequired[int]
    policy: Required[SLOPolicy]
    schema_version: NotRequired[str]
    status: Required[Literal['healthy', 'attention', 'no_data']]
    statuses: NotRequired[list[SLOStatus]]

class SLOStatus(TypedDict):
    error_budget_remaining: NotRequired[float | Any]
    good_count: NotRequired[int]
    latency_p95_ms: NotRequired[float | Any]
    max_latency_ms: NotRequired[float | Any]
    name: Required[str]
    objective: Required[float]
    observed_success_rate: NotRequired[float | Any]
    sample_count: NotRequired[int]
    service: Required[str]
    source: NotRequired[str]
    status: Required[Literal['healthy', 'attention', 'no_data']]
    window: Required[str]

class SLOTarget(TypedDict):
    max_latency_ms: NotRequired[float | Any]
    minimum_samples: NotRequired[int]
    name: Required[str]
    objective: Required[float]
    service: Required[Literal['orchestrator_api', 'queue_age', 'worker_startup', 'worker_run', 'tool_latency', 'model_routing', 'pm_scm_sync', 'mail_delivery', 'recovery']]
    source: NotRequired[Literal['aiat_default', 'company_manifest']]
    window: NotRequired[Literal['rolling_24h', 'rolling_7d', 'rolling_30d']]

class ScheduleRequest(TypedDict):
    auto_resume: NotRequired[bool]
    auto_shutdown: NotRequired[bool]
    days: NotRequired[list[str]]
    enabled: NotRequired[bool]
    end_hour: NotRequired[int]
    start_hour: NotRequired[int]
    timezone: NotRequired[str | Any]

class SearchContextRequest(TypedDict):
    limit: NotRequired[int]
    query: Required[str]

class SelfImprovementActionRequest(TypedDict):
    action: Required[Literal['record_gate', 'start_shadow', 'record_observation', 'start_canary', 'request_promotion', 'approve_promotion', 'rollback', 'record_outcome', 'record_artifacts', 'record_artifact_readback']]
    actual_sha256: NotRequired[str | Any]
    actual_size_bytes: NotRequired[int | Any]
    artifact_bundle: NotRequired[ImprovementArtifactBundle | Any]
    artifact_id: NotRequired[str | Any]
    candidate_version: NotRequired[str | Any]
    canonical_artifact_id: NotRequired[str | Any]
    cost_usd: NotRequired[float | str | Any]
    detail: NotRequired[str | Any]
    evidence_refs: NotRequired[list[str]]
    gate: NotRequired[GateName | Any]
    incident_count: NotRequired[int | Any]
    irreversible_side_effects: NotRequired[int]
    kpi_learning: NotRequired[dict[str, float]]
    outcome: NotRequired[ImprovementOutcomeKind | Any]
    outcome_id: NotRequired[str | Any]
    passed: NotRequired[bool | Any]
    readback_source: NotRequired[str | Any]
    reason: NotRequired[str | Any]
    regression_fraction: NotRequired[float | Any]
    rollback_performed: NotRequired[bool | Any]
    sample_count: NotRequired[int | Any]
    stage: NotRequired[Literal['shadow', 'canary'] | Any]

class SelfImprovementReferenceRequest(TypedDict):
    kind: Required[Literal['issue', 'worker_run', 'artifact', 'artifact_readback', 'budget_reservation', 'branch', 'sbom', 'deployment', 'evidence', 'repository']]
    reference: Required[str]

class StewardCreateRequest(TypedDict):
    adapter_version: NotRequired[str | Any]
    commit_sha: NotRequired[str | Any]
    dependency_lock_hash: NotRequired[str | Any]
    exact_release: NotRequired[str | Any]
    license_id: NotRequired[str | Any]
    monitoring_cadence: NotRequired[str]
    oci_image_digest: NotRequired[str | Any]
    package_version: NotRequired[str | Any]
    protocol_api_version: NotRequired[str | Any]
    redistribution_status: NotRequired[str]
    security_scan_status: NotRequired[str]
    source_provider: NotRequired[str]
    source_repo: NotRequired[str | Any]
    transport_type: NotRequired[str]

class TeamRunnerStorageRequest(TypedDict):
    operation: Required[Literal['storage_health', 'checkpoint_save', 'checkpoint_load', 'checkpoint_latest', 'checkpoint_delete', 'usage_record', 'document_get', 'document_create', 'document_update_status', 'review_create', 'review_get', 'review_update', 'review_comment_add', 'review_comments_get', 'review_list']]
    payload: NotRequired[dict[str, Any]]

class TraceEvidence(TypedDict):
    coverage: NotRequired[dict[str, str]]
    first_observed_at: NotRequired[str | Any]
    generated_at: NotRequired[str | Any]
    item_count: NotRequired[int]
    items: NotRequired[list[TraceEvidenceItem]]
    last_observed_at: NotRequired[str | Any]
    notices: NotRequired[list[dict[str, str]]]
    project_ids: NotRequired[list[str]]
    retention: NotRequired[TraceRetentionPolicy]
    schema_version: NotRequired[str]
    source_counts: NotRequired[dict[str, int]]
    status: Required[Literal['observed', 'not_found']]
    trace_id: Required[str]

class TraceEvidenceItem(TypedDict):
    agent_id: NotRequired[str | Any]
    artifact_id: NotRequired[str | Any]
    completion_tokens: NotRequired[int | Any]
    connection_id: NotRequired[str | Any]
    cost_usd: NotRequired[float | Any]
    duration_ms: NotRequired[float | Any]
    event_type: NotRequired[str | Any]
    exact_model_id: NotRequired[str | Any]
    id: Required[str]
    kind: Required[str]
    model: NotRequired[str | Any]
    occurred_at: NotRequired[str | Any]
    operation: NotRequired[str | Any]
    parent_span_id: NotRequired[str | Any]
    project_id: NotRequired[str | Any]
    prompt_tokens: NotRequired[int | Any]
    provider_id: NotRequired[str | Any]
    request_method: NotRequired[str | Any]
    route: NotRequired[str | Any]
    sampled: NotRequired[bool | Any]
    service: NotRequired[str | Any]
    sha256: NotRequired[str | Any]
    size_bytes: NotRequired[int | Any]
    source: Required[Literal['api_requests', 'task_log', 'project_usage_events', 'worker_run_transitions', 'worker_usage_records', 'worker_artifacts', 'pm_inbox_events', 'integration_evidence', 'native_spans']]
    span_id: NotRequired[str | Any]
    status: NotRequired[str | Any]
    status_code: NotRequired[int | Any]
    team_id: NotRequired[str | Any]
    tool_name: NotRequired[str | Any]
    total_tokens: NotRequired[int | Any]
    worker_run_id: NotRequired[str | Any]

class TraceRetentionPolicy(TypedDict):
    retention_days: NotRequired[int]
    sample_rate: NotRequired[float]
    schema_version: NotRequired[str]
    source: NotRequired[Literal['company_manifest', 'default']]
    terminal_mode: NotRequired[Literal['archive', 'delete']]

class TransitionRequest(TypedDict):
    actor_id: Required[str]
    context: NotRequired[dict[str, Any] | Any]
    event: Required[str]

class UpdateCredentialRequest(TypedDict):
    description: NotRequired[str | Any]
    policy: NotRequired[dict[str, Any] | Any]
    value: NotRequired[str | Any]

class UpdateFlowRequest(TypedDict):
    definition_json: NotRequired[dict[str, Any] | Any]
    description: NotRequired[str | Any]
    is_active: NotRequired[bool | Any]
    name: NotRequired[str | Any]

class UpdateWorkerRequest(TypedDict):
    adapter_config: NotRequired[dict[str, Any] | Any]
    adapter_entrypoint: NotRequired[str | Any]
    adapter_module: NotRequired[str | Any]
    adapter_type: NotRequired[str | Any]
    capability_ids: NotRequired[list[str] | Any]
    isolation_mode: NotRequired[str | Any]
    model_mode: NotRequired[str | Any]
    model_profile_id: NotRequired[str | Any]
    sandbox_profile: NotRequired[str | Any]
    source_repo: NotRequired[str | Any]
    team_id: NotRequired[str | Any]
    update_policy: NotRequired[str | Any]
    version: NotRequired[str | Any]
    version_pin: NotRequired[str | Any]
    wrapper_config: NotRequired[dict[str, Any] | Any]

class ValidationError(TypedDict):
    ctx: NotRequired[dict[str, Any]]
    input: NotRequired[Any]
    loc: Required[list[str | int]]
    msg: Required[str]
    type: Required[str]

class WorkerEvaluateRequest(TypedDict):
    checks: NotRequired[list[str] | Any]
    source_repo: NotRequired[str | Any]

class WorkerRunDispatchRequest(TypedDict):
    adapter_required_model_capabilities: NotRequired[list[str]]
    budget: NotRequired[dict[str, float]]
    budget_usd: NotRequired[float | Any]
    capability_requirements: NotRequired[list[dict[str, Any]]]
    checkpoint_policy: NotRequired[dict[str, Any]]
    dispatch_mode: NotRequired[Literal['queued', 'inline'] | Any]
    expected_output_tokens: NotRequired[int]
    flow_id: NotRequired[str | Any]
    flow_instance_id: NotRequired[str | Any]
    flow_node_execution_id: NotRequired[int | Any]
    idempotency_key: Required[str]
    lease_seconds: NotRequired[int]
    model_override_approval_id: NotRequired[str | Any]
    model_override_request_id: NotRequired[str | Any]
    model_policy_layers: NotRequired[list[dict[str, Any]]]
    permission_requirements: NotRequired[list[str]]
    project_id: NotRequired[str | Any]
    prompt_tokens: NotRequired[int]
    queue_priority: NotRequired[int]
    requested_model_profile: NotRequired[dict[str, Any] | Any]
    resolved_model_profile: NotRequired[dict[str, Any] | Any]
    retry_policy: NotRequired[dict[str, Any]]
    runtime_extensions: NotRequired[dict[str, Any]]
    steward_required_model_capabilities: NotRequired[list[str]]
    task_input: NotRequired[dict[str, Any]]
    task_required_model_capabilities: NotRequired[list[str]]
    task_type: Required[str]
    timeout_seconds: NotRequired[int | Any]
    tool_grants: NotRequired[list[str]]
    worker_id: Required[str]
    worker_required_model_capabilities: NotRequired[list[str]]
    workspace_mode: NotRequired[str]

class WorkerRunPauseRequest(TypedDict):
    reason: NotRequired[str]
    requested_by: NotRequired[str]

class WorkerRunResumeRequest(TypedDict):
    checkpoint_id: NotRequired[str | Any]
    requested_by: NotRequired[str]

class WorkerStatusTransition(TypedDict):
    action: Required[str]
    new_role: NotRequired[str | Any]
    new_status: NotRequired[str | Any]

class WorkerUpgradeRequest(TypedDict):
    run_compat_tests: NotRequired[bool]
    source_revision: NotRequired[str | Any]

@dataclass(frozen=True, slots=True)
class ApiOperation:
    operation_id: str
    method: str
    path: str
    path_params: tuple[str, ...]
    query_params: tuple[str, ...]
    request_body_type: str | None
    response_types: tuple[str, ...]


OPERATIONS: dict[str, ApiOperation] = {
    'get_agent_profile_agent_profiles__agent_id__get': ApiOperation(
        operation_id='get_agent_profile_agent_profiles__agent_id__get',
        method='GET',
        path='/agent-profiles/{agent_id}',
        path_params=('agent_id',),
        query_params=(),
        request_body_type=None,
        response_types=('HTTPValidationError',),
    ),
    'estimate_with_agent_profile_agent_profiles__agent_id__estimate_post': ApiOperation(
        operation_id='estimate_with_agent_profile_agent_profiles__agent_id__estimate_post',
        method='POST',
        path='/agent-profiles/{agent_id}/estimate',
        path_params=('agent_id',),
        query_params=(),
        request_body_type='AgentEstimateRequest',
        response_types=('HTTPValidationError',),
    ),
    'observe_agent_profile_agent_profiles__agent_id__observations_post': ApiOperation(
        operation_id='observe_agent_profile_agent_profiles__agent_id__observations_post',
        method='POST',
        path='/agent-profiles/{agent_id}/observations',
        path_params=('agent_id',),
        query_params=(),
        request_body_type='AgentProfileObservationRequest',
        response_types=('HTTPValidationError',),
    ),
    'get_artifact_evidence_artifacts__artifact_id__get': ApiOperation(
        operation_id='get_artifact_evidence_artifacts__artifact_id__get',
        method='GET',
        path='/artifacts/{artifact_id}',
        path_params=('artifact_id',),
        query_params=(),
        request_body_type=None,
        response_types=('HTTPValidationError',),
    ),
    'list_capabilities_capabilities_get': ApiOperation(
        operation_id='list_capabilities_capabilities_get',
        method='GET',
        path='/capabilities',
        path_params=(),
        query_params=('risk_level',),
        request_body_type=None,
        response_types=('HTTPValidationError',),
    ),
    'search_capabilities_capabilities_search_post': ApiOperation(
        operation_id='search_capabilities_capabilities_search_post',
        method='POST',
        path='/capabilities/search',
        path_params=(),
        query_params=(),
        request_body_type='CapabilitySearchRequest',
        response_types=('HTTPValidationError',),
    ),
    'list_capability_workers_capabilities_workers_get': ApiOperation(
        operation_id='list_capability_workers_capabilities_workers_get',
        method='GET',
        path='/capabilities/workers',
        path_params=(),
        query_params=('status', 'team_id'),
        request_body_type=None,
        response_types=('HTTPValidationError',),
    ),
    'register_worker_capabilities_workers_post': ApiOperation(
        operation_id='register_worker_capabilities_workers_post',
        method='POST',
        path='/capabilities/workers',
        path_params=(),
        query_params=(),
        request_body_type='RegisterWorkerRequest',
        response_types=('HTTPValidationError',),
    ),
    'import_workers_capabilities_workers_import_post': ApiOperation(
        operation_id='import_workers_capabilities_workers_import_post',
        method='POST',
        path='/capabilities/workers/import',
        path_params=(),
        query_params=(),
        request_body_type='ImportWorkersRequest',
        response_types=('HTTPValidationError',),
    ),
    'deregister_worker_capabilities_workers__worker_id__delete': ApiOperation(
        operation_id='deregister_worker_capabilities_workers__worker_id__delete',
        method='DELETE',
        path='/capabilities/workers/{worker_id}',
        path_params=('worker_id',),
        query_params=('permanent',),
        request_body_type=None,
        response_types=('HTTPValidationError',),
    ),
    'update_worker_capabilities_workers__worker_id__put': ApiOperation(
        operation_id='update_worker_capabilities_workers__worker_id__put',
        method='PUT',
        path='/capabilities/workers/{worker_id}',
        path_params=('worker_id',),
        query_params=(),
        request_body_type='UpdateWorkerRequest',
        response_types=('HTTPValidationError',),
    ),
    'evaluate_worker_capabilities_workers__worker_id__evaluate_post': ApiOperation(
        operation_id='evaluate_worker_capabilities_workers__worker_id__evaluate_post',
        method='POST',
        path='/capabilities/workers/{worker_id}/evaluate',
        path_params=('worker_id',),
        query_params=(),
        request_body_type='WorkerEvaluateRequest',
        response_types=('HTTPValidationError',),
    ),
    'get_worker_evaluations_capabilities_workers__worker_id__evaluations_get': ApiOperation(
        operation_id='get_worker_evaluations_capabilities_workers__worker_id__evaluations_get',
        method='GET',
        path='/capabilities/workers/{worker_id}/evaluations',
        path_params=('worker_id',),
        query_params=('limit',),
        request_body_type=None,
        response_types=('HTTPValidationError',),
    ),
    'get_worker_health_capabilities_workers__worker_id__health_get': ApiOperation(
        operation_id='get_worker_health_capabilities_workers__worker_id__health_get',
        method='GET',
        path='/capabilities/workers/{worker_id}/health',
        path_params=('worker_id',),
        query_params=(),
        request_body_type=None,
        response_types=('HTTPValidationError',),
    ),
    'transition_worker_status_capabilities_workers__worker_id__status_patch': ApiOperation(
        operation_id='transition_worker_status_capabilities_workers__worker_id__status_patch',
        method='PATCH',
        path='/capabilities/workers/{worker_id}/status',
        path_params=('worker_id',),
        query_params=(),
        request_body_type='WorkerStatusTransition',
        response_types=('HTTPValidationError',),
    ),
    'get_worker_steward_capabilities_workers__worker_id__steward_get': ApiOperation(
        operation_id='get_worker_steward_capabilities_workers__worker_id__steward_get',
        method='GET',
        path='/capabilities/workers/{worker_id}/steward',
        path_params=('worker_id',),
        query_params=(),
        request_body_type=None,
        response_types=('HTTPValidationError',),
    ),
    'create_worker_steward_capabilities_workers__worker_id__steward_post': ApiOperation(
        operation_id='create_worker_steward_capabilities_workers__worker_id__steward_post',
        method='POST',
        path='/capabilities/workers/{worker_id}/steward',
        path_params=('worker_id',),
        query_params=(),
        request_body_type='StewardCreateRequest',
        response_types=('HTTPValidationError',),
    ),
    'list_steward_candidates_capabilities_workers__worker_id__steward_candidates_get': ApiOperation(
        operation_id='list_steward_candidates_capabilities_workers__worker_id__steward_candidates_get',
        method='GET',
        path='/capabilities/workers/{worker_id}/steward/candidates',
        path_params=('worker_id',),
        query_params=(),
        request_body_type=None,
        response_types=('HTTPValidationError',),
    ),
    'generate_steward_candidate_capabilities_workers__worker_id__steward_candidates_post': ApiOperation(
        operation_id='generate_steward_candidate_capabilities_workers__worker_id__steward_candidates_post',
        method='POST',
        path='/capabilities/workers/{worker_id}/steward/candidates',
        path_params=('worker_id',),
        query_params=(),
        request_body_type='CandidateGenerationRequest',
        response_types=('HTTPValidationError',),
    ),
    'approve_steward_candidate_capabilities_workers__worker_id__steward_candidates__candidate_id__approve_post': ApiOperation(
        operation_id='approve_steward_candidate_capabilities_workers__worker_id__steward_candidates__candidate_id__approve_post',
        method='POST',
        path='/capabilities/workers/{worker_id}/steward/candidates/{candidate_id}/approve',
        path_params=('candidate_id', 'worker_id'),
        query_params=(),
        request_body_type='CandidateApprovalRequest',
        response_types=('HTTPValidationError',),
    ),
    'certify_steward_candidate_capabilities_workers__worker_id__steward_candidates__candidate_id__certify_post': ApiOperation(
        operation_id='certify_steward_candidate_capabilities_workers__worker_id__steward_candidates__candidate_id__certify_post',
        method='POST',
        path='/capabilities/workers/{worker_id}/steward/candidates/{candidate_id}/certify',
        path_params=('candidate_id', 'worker_id'),
        query_params=(),
        request_body_type='CandidateCertificationRequest',
        response_types=('HTTPValidationError',),
    ),
    'advance_steward_candidate_stage_capabilities_workers__worker_id__steward_candidates__candidate_id__stage_post': ApiOperation(
        operation_id='advance_steward_candidate_stage_capabilities_workers__worker_id__steward_candidates__candidate_id__stage_post',
        method='POST',
        path='/capabilities/workers/{worker_id}/steward/candidates/{candidate_id}/stage',
        path_params=('candidate_id', 'worker_id'),
        query_params=(),
        request_body_type='CandidateStageAdvanceRequest',
        response_types=('HTTPValidationError',),
    ),
    'record_steward_capabilities_capabilities_workers__worker_id__steward_capabilities_post': ApiOperation(
        operation_id='record_steward_capabilities_capabilities_workers__worker_id__steward_capabilities_post',
        method='POST',
        path='/capabilities/workers/{worker_id}/steward/capabilities',
        path_params=('worker_id',),
        query_params=(),
        request_body_type=None,
        response_types=('HTTPValidationError',),
    ),
    'add_steward_documentation_capabilities_workers__worker_id__steward_documentation_post': ApiOperation(
        operation_id='add_steward_documentation_capabilities_workers__worker_id__steward_documentation_post',
        method='POST',
        path='/capabilities/workers/{worker_id}/steward/documentation',
        path_params=('worker_id',),
        query_params=(),
        request_body_type='DocumentationSnapshotRequest',
        response_types=('HTTPValidationError',),
    ),
    'run_worker_steward_monitor_capabilities_workers__worker_id__steward_monitor_post': ApiOperation(
        operation_id='run_worker_steward_monitor_capabilities_workers__worker_id__steward_monitor_post',
        method='POST',
        path='/capabilities/workers/{worker_id}/steward/monitor',
        path_params=('worker_id',),
        query_params=(),
        request_body_type=None,
        response_types=('HTTPValidationError',),
    ),
    'list_worker_steward_monitoring_capabilities_workers__worker_id__steward_monitoring_get': ApiOperation(
        operation_id='list_worker_steward_monitoring_capabilities_workers__worker_id__steward_monitoring_get',
        method='GET',
        path='/capabilities/workers/{worker_id}/steward/monitoring',
        path_params=('worker_id',),
        query_params=(),
        request_body_type=None,
        response_types=('HTTPValidationError',),
    ),
    'start_steward_rollout_capabilities_workers__worker_id__steward_rollouts_post': ApiOperation(
        operation_id='start_steward_rollout_capabilities_workers__worker_id__steward_rollouts_post',
        method='POST',
        path='/capabilities/workers/{worker_id}/steward/rollouts',
        path_params=('worker_id',),
        query_params=(),
        request_body_type='RolloutStartRequest',
        response_types=('HTTPValidationError',),
    ),
    'advance_steward_rollout_capabilities_workers__worker_id__steward_rollouts__rollout_id__advance_post': ApiOperation(
        operation_id='advance_steward_rollout_capabilities_workers__worker_id__steward_rollouts__rollout_id__advance_post',
        method='POST',
        path='/capabilities/workers/{worker_id}/steward/rollouts/{rollout_id}/advance',
        path_params=('rollout_id', 'worker_id'),
        query_params=(),
        request_body_type='RolloutAdvanceRequest',
        response_types=('HTTPValidationError',),
    ),
    'rollback_steward_rollout_capabilities_workers__worker_id__steward_rollouts__rollout_id__rollback_post': ApiOperation(
        operation_id='rollback_steward_rollout_capabilities_workers__worker_id__steward_rollouts__rollout_id__rollback_post',
        method='POST',
        path='/capabilities/workers/{worker_id}/steward/rollouts/{rollout_id}/rollback',
        path_params=('rollout_id', 'worker_id'),
        query_params=(),
        request_body_type='RollbackRequest',
        response_types=('HTTPValidationError',),
    ),
    'upgrade_worker_capabilities_workers__worker_id__upgrade_post': ApiOperation(
        operation_id='upgrade_worker_capabilities_workers__worker_id__upgrade_post',
        method='POST',
        path='/capabilities/workers/{worker_id}/upgrade',
        path_params=('worker_id',),
        query_params=(),
        request_body_type='WorkerUpgradeRequest',
        response_types=('HTTPValidationError',),
    ),
    'get_worker_upstream_capabilities_workers__worker_id__upstream_get': ApiOperation(
        operation_id='get_worker_upstream_capabilities_workers__worker_id__upstream_get',
        method='GET',
        path='/capabilities/workers/{worker_id}/upstream',
        path_params=('worker_id',),
        query_params=(),
        request_body_type=None,
        response_types=('HTTPValidationError',),
    ),
    'operator_send_to_ceo_ceo_message_post': ApiOperation(
        operation_id='operator_send_to_ceo_ceo_message_post',
        method='POST',
        path='/ceo/message',
        path_params=(),
        query_params=(),
        request_body_type='OperatorToCeoRequest',
        response_types=('HTTPValidationError',),
    ),
    'request_privileged_action_ceo_privileged_action_post': ApiOperation(
        operation_id='request_privileged_action_ceo_privileged_action_post',
        method='POST',
        path='/ceo/privileged-action',
        path_params=(),
        query_params=(),
        request_body_type='PrivilegedActionRequest',
        response_types=('HTTPValidationError',),
    ),
    'approve_privileged_action_ceo_privileged_action__record_id__approve_post': ApiOperation(
        operation_id='approve_privileged_action_ceo_privileged_action__record_id__approve_post',
        method='POST',
        path='/ceo/privileged-action/{record_id}/approve',
        path_params=('record_id',),
        query_params=(),
        request_body_type='PrivilegedApprovalRequest',
        response_types=('HTTPValidationError',),
    ),
    'privileged_actions_audit_ceo_privileged_actions_audit_get': ApiOperation(
        operation_id='privileged_actions_audit_ceo_privileged_actions_audit_get',
        method='GET',
        path='/ceo/privileged-actions/audit',
        path_params=(),
        query_params=('limit',),
        request_body_type=None,
        response_types=('HTTPValidationError',),
    ),
    'list_pending_privileged_actions_ceo_privileged_actions_pending_get': ApiOperation(
        operation_id='list_pending_privileged_actions_ceo_privileged_actions_pending_get',
        method='GET',
        path='/ceo/privileged-actions/pending',
        path_params=(),
        query_params=(),
        request_body_type=None,
        response_types=(),
    ),
    'list_companies_companies_get': ApiOperation(
        operation_id='list_companies_companies_get',
        method='GET',
        path='/companies',
        path_params=(),
        query_params=(),
        request_body_type=None,
        response_types=(),
    ),
    'create_company_companies_post': ApiOperation(
        operation_id='create_company_companies_post',
        method='POST',
        path='/companies',
        path_params=(),
        query_params=(),
        request_body_type='CompanyCreateRequest',
        response_types=('HTTPValidationError',),
    ),
    'get_company_companies__company_id__get': ApiOperation(
        operation_id='get_company_companies__company_id__get',
        method='GET',
        path='/companies/{company_id}',
        path_params=('company_id',),
        query_params=(),
        request_body_type=None,
        response_types=('HTTPValidationError',),
    ),
    'list_company_assignments_companies__company_id__assignments_get': ApiOperation(
        operation_id='list_company_assignments_companies__company_id__assignments_get',
        method='GET',
        path='/companies/{company_id}/assignments',
        path_params=('company_id',),
        query_params=(),
        request_body_type=None,
        response_types=('HTTPValidationError',),
    ),
    'list_company_budget_reservations_companies__company_id__budget_reservations_get': ApiOperation(
        operation_id='list_company_budget_reservations_companies__company_id__budget_reservations_get',
        method='GET',
        path='/companies/{company_id}/budget-reservations',
        path_params=('company_id',),
        query_params=('limit', 'run_id'),
        request_body_type=None,
        response_types=('HTTPValidationError',),
    ),
    'list_company_budgets_companies__company_id__budgets_get': ApiOperation(
        operation_id='list_company_budgets_companies__company_id__budgets_get',
        method='GET',
        path='/companies/{company_id}/budgets',
        path_params=('company_id',),
        query_params=(),
        request_body_type=None,
        response_types=('HTTPValidationError',),
    ),
    'get_company_budget_companies__company_id__budgets__budget_key__get': ApiOperation(
        operation_id='get_company_budget_companies__company_id__budgets__budget_key__get',
        method='GET',
        path='/companies/{company_id}/budgets/{budget_key}',
        path_params=('budget_key', 'company_id'),
        query_params=(),
        request_body_type=None,
        response_types=('HTTPValidationError',),
    ),
    'list_company_departments_companies__company_id__departments_get': ApiOperation(
        operation_id='list_company_departments_companies__company_id__departments_get',
        method='GET',
        path='/companies/{company_id}/departments',
        path_params=('company_id',),
        query_params=(),
        request_body_type=None,
        response_types=('HTTPValidationError',),
    ),
    'set_company_evidence_policy_companies__company_id__evidence_policy_put': ApiOperation(
        operation_id='set_company_evidence_policy_companies__company_id__evidence_policy_put',
        method='PUT',
        path='/companies/{company_id}/evidence-policy',
        path_params=('company_id',),
        query_params=(),
        request_body_type='ProjectEvidencePolicyRequest',
        response_types=('HTTPValidationError',),
    ),
    'apply_company_manifest_companies__company_id__manifest_apply_post': ApiOperation(
        operation_id='apply_company_manifest_companies__company_id__manifest_apply_post',
        method='POST',
        path='/companies/{company_id}/manifest/apply',
        path_params=('company_id',),
        query_params=(),
        request_body_type='CompanyManifestRequest',
        response_types=('HTTPValidationError',),
    ),
    'company_manifest_history_companies__company_id__manifest_history_get': ApiOperation(
        operation_id='company_manifest_history_companies__company_id__manifest_history_get',
        method='GET',
        path='/companies/{company_id}/manifest/history',
        path_params=('company_id',),
        query_params=('limit',),
        request_body_type=None,
        response_types=('HTTPValidationError',),
    ),
    'rollback_company_manifest_companies__company_id__manifest_rollback_post': ApiOperation(
        operation_id='rollback_company_manifest_companies__company_id__manifest_rollback_post',
        method='POST',
        path='/companies/{company_id}/manifest/rollback',
        path_params=('company_id',),
        query_params=(),
        request_body_type='CompanyManifestRollbackRequest',
        response_types=('HTTPValidationError',),
    ),
    'validate_company_manifest_companies__company_id__manifest_validate_post': ApiOperation(
        operation_id='validate_company_manifest_companies__company_id__manifest_validate_post',
        method='POST',
        path='/companies/{company_id}/manifest/validate',
        path_params=('company_id',),
        query_params=(),
        request_body_type='CompanyManifestRequest',
        response_types=('HTTPValidationError',),
    ),
    'list_credentials_credentials_get': ApiOperation(
        operation_id='list_credentials_credentials_get',
        method='GET',
        path='/credentials',
        path_params=(),
        query_params=(),
        request_body_type=None,
        response_types=(),
    ),
    'create_credential_credentials_post': ApiOperation(
        operation_id='create_credential_credentials_post',
        method='POST',
        path='/credentials',
        path_params=(),
        query_params=(),
        request_body_type='CreateCredentialRequest',
        response_types=('HTTPValidationError',),
    ),
    'full_audit_log_credentials_audit_get': ApiOperation(
        operation_id='full_audit_log_credentials_audit_get',
        method='GET',
        path='/credentials-audit',
        path_params=(),
        query_params=('limit',),
        request_body_type=None,
        response_types=('HTTPValidationError',),
    ),
    'decide_credential_approval_credentials_approval_requests__approval_id__decision_post': ApiOperation(
        operation_id='decide_credential_approval_credentials_approval_requests__approval_id__decision_post',
        method='POST',
        path='/credentials/approval-requests/{approval_id}/decision',
        path_params=('approval_id',),
        query_params=(),
        request_body_type='CredentialApprovalDecisionRequest',
        response_types=('HTTPValidationError',),
    ),
    'delete_credential_credentials__name__delete': ApiOperation(
        operation_id='delete_credential_credentials__name__delete',
        method='DELETE',
        path='/credentials/{name}',
        path_params=('name',),
        query_params=(),
        request_body_type=None,
        response_types=('HTTPValidationError',),
    ),
    'get_credential_credentials__name__get': ApiOperation(
        operation_id='get_credential_credentials__name__get',
        method='GET',
        path='/credentials/{name}',
        path_params=('name',),
        query_params=(),
        request_body_type=None,
        response_types=('HTTPValidationError',),
    ),
    'update_credential_credentials__name__patch': ApiOperation(
        operation_id='update_credential_credentials__name__patch',
        method='PATCH',
        path='/credentials/{name}',
        path_params=('name',),
        query_params=(),
        request_body_type='UpdateCredentialRequest',
        response_types=('HTTPValidationError',),
    ),
    'request_credential_approval_credentials__name__approval_requests_post': ApiOperation(
        operation_id='request_credential_approval_credentials__name__approval_requests_post',
        method='POST',
        path='/credentials/{name}/approval-requests',
        path_params=('name',),
        query_params=(),
        request_body_type='CredentialApprovalRequest',
        response_types=('HTTPValidationError',),
    ),
    'credential_audit_log_credentials__name__audit_get': ApiOperation(
        operation_id='credential_audit_log_credentials__name__audit_get',
        method='GET',
        path='/credentials/{name}/audit',
        path_params=('name',),
        query_params=('limit',),
        request_body_type=None,
        response_types=('HTTPValidationError',),
    ),
    'resolve_credential_credentials__name__resolve_post': ApiOperation(
        operation_id='resolve_credential_credentials__name__resolve_post',
        method='POST',
        path='/credentials/{name}/resolve',
        path_params=('name',),
        query_params=(),
        request_body_type='ResolveCredentialRequest',
        response_types=('HTTPValidationError',),
    ),
    'dashboard_access_dashboard_access_get': ApiOperation(
        operation_id='dashboard_access_dashboard_access_get',
        method='GET',
        path='/dashboard/access',
        path_params=(),
        query_params=(),
        request_body_type=None,
        response_types=(),
    ),
    'dashboard_section_access_dashboard_sections__section__get': ApiOperation(
        operation_id='dashboard_section_access_dashboard_sections__section__get',
        method='GET',
        path='/dashboard/sections/{section}',
        path_params=('section',),
        query_params=(),
        request_body_type=None,
        response_types=('HTTPValidationError',),
    ),
    'update_dashboard_section_acl_dashboard_sections__section__acl_put': ApiOperation(
        operation_id='update_dashboard_section_acl_dashboard_sections__section__acl_put',
        method='PUT',
        path='/dashboard/sections/{section}/acl',
        path_params=('section',),
        query_params=(),
        request_body_type='DashboardSectionACLRequest',
        response_types=('HTTPValidationError',),
    ),
    'list_dead_letters_dead_letters_get': ApiOperation(
        operation_id='list_dead_letters_dead_letters_get',
        method='GET',
        path='/dead-letters',
        path_params=(),
        query_params=('limit', 'project_id', 'recipient_team'),
        request_body_type=None,
        response_types=('HTTPValidationError',),
    ),
    'get_dead_letter_dead_letters__letter_id__get': ApiOperation(
        operation_id='get_dead_letter_dead_letters__letter_id__get',
        method='GET',
        path='/dead-letters/{letter_id}',
        path_params=('letter_id',),
        query_params=(),
        request_body_type=None,
        response_types=('HTTPValidationError',),
    ),
    'replay_dead_letter_dead_letters__letter_id__replay_post': ApiOperation(
        operation_id='replay_dead_letter_dead_letters__letter_id__replay_post',
        method='POST',
        path='/dead-letters/{letter_id}/replay',
        path_params=('letter_id',),
        query_params=(),
        request_body_type=None,
        response_types=('HTTPValidationError',),
    ),
    'evaluate_firecracker_integration_evaluations_firecracker_get': ApiOperation(
        operation_id='evaluate_firecracker_integration_evaluations_firecracker_get',
        method='GET',
        path='/evaluations/firecracker',
        path_params=(),
        query_params=(),
        request_body_type=None,
        response_types=(),
    ),
    'evaluate_garage_integration_evaluations_garage_get': ApiOperation(
        operation_id='evaluate_garage_integration_evaluations_garage_get',
        method='GET',
        path='/evaluations/garage',
        path_params=(),
        query_params=(),
        request_body_type=None,
        response_types=(),
    ),
    'evaluate_temporal_integration_evaluations_temporal_get': ApiOperation(
        operation_id='evaluate_temporal_integration_evaluations_temporal_get',
        method='GET',
        path='/evaluations/temporal',
        path_params=(),
        query_params=(),
        request_body_type=None,
        response_types=(),
    ),
    'evaluate_vault_integration_evaluations_vault_get': ApiOperation(
        operation_id='evaluate_vault_integration_evaluations_vault_get',
        method='GET',
        path='/evaluations/vault',
        path_params=(),
        query_params=(),
        request_body_type=None,
        response_types=(),
    ),
    'evaluate_zitadel_integration_evaluations_zitadel_get': ApiOperation(
        operation_id='evaluate_zitadel_integration_evaluations_zitadel_get',
        method='GET',
        path='/evaluations/zitadel',
        path_params=(),
        query_params=(),
        request_body_type=None,
        response_types=(),
    ),
    'list_evidence_policies_evidence_policies_get': ApiOperation(
        operation_id='list_evidence_policies_evidence_policies_get',
        method='GET',
        path='/evidence-policies',
        path_params=(),
        query_params=(),
        request_body_type=None,
        response_types=(),
    ),
    'executive_ceo_privileged_action_executive_actions_ceo_privileged_actions_post': ApiOperation(
        operation_id='executive_ceo_privileged_action_executive_actions_ceo_privileged_actions_post',
        method='POST',
        path='/executive/actions/ceo/privileged-actions',
        path_params=(),
        query_params=(),
        request_body_type='ExecutiveCEOPrivilegedActionRequest',
        response_types=('HTTPValidationError',),
    ),
    'executive_cfo_model_override_executive_actions_cfo_model_overrides_post': ApiOperation(
        operation_id='executive_cfo_model_override_executive_actions_cfo_model_overrides_post',
        method='POST',
        path='/executive/actions/cfo/model-overrides',
        path_params=(),
        query_params=(),
        request_body_type='ExecutiveCFOModelOverrideRequest',
        response_types=('HTTPValidationError',),
    ),
    'executive_cto_worker_run_executive_actions_cto_worker_runs_post': ApiOperation(
        operation_id='executive_cto_worker_run_executive_actions_cto_worker_runs_post',
        method='POST',
        path='/executive/actions/cto/worker-runs',
        path_params=(),
        query_params=(),
        request_body_type='ExecutiveCTOWorkerRunRequest',
        response_types=('HTTPValidationError',),
    ),
    'executive_reconciliation_executive_reconciliation_get': ApiOperation(
        operation_id='executive_reconciliation_executive_reconciliation_get',
        method='GET',
        path='/executive/reconciliation',
        path_params=(),
        query_params=('company_id',),
        request_body_type=None,
        response_types=('HTTPValidationError',),
    ),
    'executive_role_view_executive_views__role__get': ApiOperation(
        operation_id='executive_role_view_executive_views__role__get',
        method='GET',
        path='/executive/views/{role}',
        path_params=('role',),
        query_params=('company_id',),
        request_body_type=None,
        response_types=('HTTPValidationError',),
    ),
    'list_flow_templates_flow_templates_get': ApiOperation(
        operation_id='list_flow_templates_flow_templates_get',
        method='GET',
        path='/flow-templates',
        path_params=(),
        query_params=(),
        request_body_type=None,
        response_types=(),
    ),
    'list_flows_flows_get': ApiOperation(
        operation_id='list_flows_flows_get',
        method='GET',
        path='/flows',
        path_params=(),
        query_params=('is_active', 'limit', 'offset'),
        request_body_type=None,
        response_types=('HTTPValidationError',),
    ),
    'create_flow_flows_post': ApiOperation(
        operation_id='create_flow_flows_post',
        method='POST',
        path='/flows',
        path_params=(),
        query_params=(),
        request_body_type='CreateFlowRequest',
        response_types=('HTTPValidationError',),
    ),
    'diff_flows_flows_diff_post': ApiOperation(
        operation_id='diff_flows_flows_diff_post',
        method='POST',
        path='/flows/diff',
        path_params=(),
        query_params=(),
        request_body_type='FlowDiffRequest',
        response_types=('HTTPValidationError',),
    ),
    'dry_run_flow_flows_dry_run_post': ApiOperation(
        operation_id='dry_run_flow_flows_dry_run_post',
        method='POST',
        path='/flows/dry-run',
        path_params=(),
        query_params=(),
        request_body_type='FlowDryRunRequest',
        response_types=('HTTPValidationError',),
    ),
    'create_flow_from_template_flows_from_template_post': ApiOperation(
        operation_id='create_flow_from_template_flows_from_template_post',
        method='POST',
        path='/flows/from-template',
        path_params=(),
        query_params=(),
        request_body_type='FlowFromTemplateRequest',
        response_types=('HTTPValidationError',),
    ),
    'import_flow_flows_import_post': ApiOperation(
        operation_id='import_flow_flows_import_post',
        method='POST',
        path='/flows/import',
        path_params=(),
        query_params=(),
        request_body_type='FlowImportRequest',
        response_types=('HTTPValidationError',),
    ),
    'list_flow_instances_early_flows_instances_get': ApiOperation(
        operation_id='list_flow_instances_early_flows_instances_get',
        method='GET',
        path='/flows/instances',
        path_params=(),
        query_params=('flow_id', 'limit', 'offset', 'project_id', 'status'),
        request_body_type=None,
        response_types=('HTTPValidationError',),
    ),
    'create_flow_instance_flows_instances_post': ApiOperation(
        operation_id='create_flow_instance_flows_instances_post',
        method='POST',
        path='/flows/instances',
        path_params=(),
        query_params=(),
        request_body_type='CreateFlowInstanceRequest',
        response_types=('HTTPValidationError',),
    ),
    'list_active_flow_instances_early_flows_instances_active_get': ApiOperation(
        operation_id='list_active_flow_instances_early_flows_instances_active_get',
        method='GET',
        path='/flows/instances/active',
        path_params=(),
        query_params=(),
        request_body_type=None,
        response_types=(),
    ),
    'get_flow_instance_flows_instances__instance_id__get': ApiOperation(
        operation_id='get_flow_instance_flows_instances__instance_id__get',
        method='GET',
        path='/flows/instances/{instance_id}',
        path_params=('instance_id',),
        query_params=(),
        request_body_type=None,
        response_types=('HTTPValidationError',),
    ),
    'flow_instance_action_flows_instances__instance_id__action_post': ApiOperation(
        operation_id='flow_instance_action_flows_instances__instance_id__action_post',
        method='POST',
        path='/flows/instances/{instance_id}/action',
        path_params=('instance_id',),
        query_params=(),
        request_body_type='FlowInstanceActionRequest',
        response_types=('HTTPValidationError',),
    ),
    'update_flow_instance_context_flows_instances__instance_id__context_post': ApiOperation(
        operation_id='update_flow_instance_context_flows_instances__instance_id__context_post',
        method='POST',
        path='/flows/instances/{instance_id}/context',
        path_params=('instance_id',),
        query_params=(),
        request_body_type=None,
        response_types=('HTTPValidationError',),
    ),
    'escalate_flow_instance_flows_instances__instance_id__escalate_post': ApiOperation(
        operation_id='escalate_flow_instance_flows_instances__instance_id__escalate_post',
        method='POST',
        path='/flows/instances/{instance_id}/escalate',
        path_params=('instance_id',),
        query_params=(),
        request_body_type=None,
        response_types=('HTTPValidationError',),
    ),
    'list_flow_node_executions_flows_instances__instance_id__executions_get': ApiOperation(
        operation_id='list_flow_node_executions_flows_instances__instance_id__executions_get',
        method='GET',
        path='/flows/instances/{instance_id}/executions',
        path_params=('instance_id',),
        query_params=('limit', 'offset'),
        request_body_type=None,
        response_types=('HTTPValidationError',),
    ),
    'migrate_flow_instance_flows_instances__instance_id__migrate_post': ApiOperation(
        operation_id='migrate_flow_instance_flows_instances__instance_id__migrate_post',
        method='POST',
        path='/flows/instances/{instance_id}/migrate',
        path_params=('instance_id',),
        query_params=(),
        request_body_type='FlowMigrationRequest',
        response_types=('HTTPValidationError',),
    ),
    'flow_node_action_flows_instances__instance_id__node_action_post': ApiOperation(
        operation_id='flow_node_action_flows_instances__instance_id__node_action_post',
        method='POST',
        path='/flows/instances/{instance_id}/node-action',
        path_params=('instance_id',),
        query_params=(),
        request_body_type='FlowNodeActionRequest',
        response_types=('HTTPValidationError',),
    ),
    'override_flow_instance_flows_instances__instance_id__override_post': ApiOperation(
        operation_id='override_flow_instance_flows_instances__instance_id__override_post',
        method='POST',
        path='/flows/instances/{instance_id}/override',
        path_params=('instance_id',),
        query_params=(),
        request_body_type='FlowOverrideRequest',
        response_types=('HTTPValidationError',),
    ),
    'retry_flow_instance_flows_instances__instance_id__retry_post': ApiOperation(
        operation_id='retry_flow_instance_flows_instances__instance_id__retry_post',
        method='POST',
        path='/flows/instances/{instance_id}/retry',
        path_params=('instance_id',),
        query_params=(),
        request_body_type=None,
        response_types=('HTTPValidationError',),
    ),
    'switch_flow_instance_flows_instances__instance_id__switch_post': ApiOperation(
        operation_id='switch_flow_instance_flows_instances__instance_id__switch_post',
        method='POST',
        path='/flows/instances/{instance_id}/switch',
        path_params=('instance_id',),
        query_params=(),
        request_body_type=None,
        response_types=('HTTPValidationError',),
    ),
    'list_flow_node_schemas_flows_node_schemas_get': ApiOperation(
        operation_id='list_flow_node_schemas_flows_node_schemas_get',
        method='GET',
        path='/flows/node-schemas',
        path_params=(),
        query_params=(),
        request_body_type=None,
        response_types=(),
    ),
    'delete_flow_flows__flow_id__delete': ApiOperation(
        operation_id='delete_flow_flows__flow_id__delete',
        method='DELETE',
        path='/flows/{flow_id}',
        path_params=('flow_id',),
        query_params=(),
        request_body_type=None,
        response_types=('HTTPValidationError',),
    ),
    'get_flow_flows__flow_id__get': ApiOperation(
        operation_id='get_flow_flows__flow_id__get',
        method='GET',
        path='/flows/{flow_id}',
        path_params=('flow_id',),
        query_params=(),
        request_body_type=None,
        response_types=('HTTPValidationError',),
    ),
    'update_flow_flows__flow_id__put': ApiOperation(
        operation_id='update_flow_flows__flow_id__put',
        method='PUT',
        path='/flows/{flow_id}',
        path_params=('flow_id',),
        query_params=(),
        request_body_type='UpdateFlowRequest',
        response_types=('HTTPValidationError',),
    ),
    'deprecate_flow_flows__flow_id__deprecate_post': ApiOperation(
        operation_id='deprecate_flow_flows__flow_id__deprecate_post',
        method='POST',
        path='/flows/{flow_id}/deprecate',
        path_params=('flow_id',),
        query_params=(),
        request_body_type=None,
        response_types=('HTTPValidationError',),
    ),
    'export_flow_flows__flow_id__export_get': ApiOperation(
        operation_id='export_flow_flows__flow_id__export_get',
        method='GET',
        path='/flows/{flow_id}/export',
        path_params=('flow_id',),
        query_params=(),
        request_body_type=None,
        response_types=('HTTPValidationError',),
    ),
    'migrate_legacy_flow_tasks_flows__flow_id__migrate_legacy_tasks_post': ApiOperation(
        operation_id='migrate_legacy_flow_tasks_flows__flow_id__migrate_legacy_tasks_post',
        method='POST',
        path='/flows/{flow_id}/migrate-legacy-tasks',
        path_params=('flow_id',),
        query_params=(),
        request_body_type='FlowLegacyTaskMigrationRequest',
        response_types=('HTTPValidationError',),
    ),
    'publish_flow_flows__flow_id__publish_post': ApiOperation(
        operation_id='publish_flow_flows__flow_id__publish_post',
        method='POST',
        path='/flows/{flow_id}/publish',
        path_params=('flow_id',),
        query_params=(),
        request_body_type=None,
        response_types=('HTTPValidationError',),
    ),
    'health_health_get': ApiOperation(
        operation_id='health_health_get',
        method='GET',
        path='/health',
        path_params=(),
        query_params=(),
        request_body_type=None,
        response_types=(),
    ),
    'identity_dashboard_action_identity_dashboard_action_post': ApiOperation(
        operation_id='identity_dashboard_action_identity_dashboard_action_post',
        method='POST',
        path='/identity/dashboard/action',
        path_params=(),
        query_params=(),
        request_body_type='IdentityDashboardActionRequest',
        response_types=('HTTPValidationError',),
    ),
    'identity_dashboard_resource_identity_dashboard__resource__get': ApiOperation(
        operation_id='identity_dashboard_resource_identity_dashboard__resource__get',
        method='GET',
        path='/identity/dashboard/{resource}',
        path_params=('resource',),
        query_params=(),
        request_body_type=None,
        response_types=('HTTPValidationError',),
    ),
    'list_integration_conflicts_integrations_conflicts_get': ApiOperation(
        operation_id='list_integration_conflicts_integrations_conflicts_get',
        method='GET',
        path='/integrations/conflicts',
        path_params=(),
        query_params=('connection_id', 'limit'),
        request_body_type=None,
        response_types=('HTTPValidationError',),
    ),
    'resolve_integration_conflict_integrations_conflicts__conflict_id__resolve_post': ApiOperation(
        operation_id='resolve_integration_conflict_integrations_conflicts__conflict_id__resolve_post',
        method='POST',
        path='/integrations/conflicts/{conflict_id}/resolve',
        path_params=('conflict_id',),
        query_params=(),
        request_body_type='PMConflictResolutionRequest',
        response_types=('HTTPValidationError',),
    ),
    'list_integration_connections_integrations_connections_get': ApiOperation(
        operation_id='list_integration_connections_integrations_connections_get',
        method='GET',
        path='/integrations/connections',
        path_params=(),
        query_params=('status',),
        request_body_type=None,
        response_types=('HTTPValidationError',),
    ),
    'create_integration_connection_integrations_connections_post': ApiOperation(
        operation_id='create_integration_connection_integrations_connections_post',
        method='POST',
        path='/integrations/connections',
        path_params=(),
        query_params=(),
        request_body_type='PMConnectionCreateRequest',
        response_types=('HTTPValidationError',),
    ),
    'apply_integration_bootstrap_integrations_connections__connection_id__apply_post': ApiOperation(
        operation_id='apply_integration_bootstrap_integrations_connections__connection_id__apply_post',
        method='POST',
        path='/integrations/connections/{connection_id}/apply',
        path_params=('connection_id',),
        query_params=(),
        request_body_type='PMApplyRequest',
        response_types=('HTTPValidationError',),
    ),
    'integration_connection_capabilities_integrations_connections__connection_id__capabilities_get': ApiOperation(
        operation_id='integration_connection_capabilities_integrations_connections__connection_id__capabilities_get',
        method='GET',
        path='/integrations/connections/{connection_id}/capabilities',
        path_params=('connection_id',),
        query_params=(),
        request_body_type=None,
        response_types=('HTTPValidationError',),
    ),
    'doctor_integration_connection_integrations_connections__connection_id__doctor_get': ApiOperation(
        operation_id='doctor_integration_connection_integrations_connections__connection_id__doctor_get',
        method='GET',
        path='/integrations/connections/{connection_id}/doctor',
        path_params=('connection_id',),
        query_params=(),
        request_body_type=None,
        response_types=('HTTPValidationError',),
    ),
    'create_external_actor_mapping_integrations_connections__connection_id__external_actor_mappings_post': ApiOperation(
        operation_id='create_external_actor_mapping_integrations_connections__connection_id__external_actor_mappings_post',
        method='POST',
        path='/integrations/connections/{connection_id}/external-actor-mappings',
        path_params=('connection_id',),
        query_params=(),
        request_body_type='PMExternalActorMappingCreateRequest',
        response_types=('HTTPValidationError',),
    ),
    'revoke_external_actor_mapping_integrations_connections__connection_id__external_actor_mappings__mapping_id__revoke_post': ApiOperation(
        operation_id='revoke_external_actor_mapping_integrations_connections__connection_id__external_actor_mappings__mapping_id__revoke_post',
        method='POST',
        path='/integrations/connections/{connection_id}/external-actor-mappings/{mapping_id}/revoke',
        path_params=('connection_id', 'mapping_id'),
        query_params=('reason',),
        request_body_type=None,
        response_types=('HTTPValidationError',),
    ),
    'integration_connection_health_integrations_connections__connection_id__health_get': ApiOperation(
        operation_id='integration_connection_health_integrations_connections__connection_id__health_get',
        method='GET',
        path='/integrations/connections/{connection_id}/health',
        path_params=('connection_id',),
        query_params=(),
        request_body_type=None,
        response_types=('HTTPValidationError',),
    ),
    'create_inbound_priority_canary_plan_integrations_connections__connection_id__inbound_canaries_post': ApiOperation(
        operation_id='create_inbound_priority_canary_plan_integrations_connections__connection_id__inbound_canaries_post',
        method='POST',
        path='/integrations/connections/{connection_id}/inbound-canaries',
        path_params=('connection_id',),
        query_params=(),
        request_body_type='PMInboundCanaryPlanCreateRequest',
        response_types=('HTTPValidationError',),
    ),
    'plan_integration_bootstrap_integrations_connections__connection_id__plan_post': ApiOperation(
        operation_id='plan_integration_bootstrap_integrations_connections__connection_id__plan_post',
        method='POST',
        path='/integrations/connections/{connection_id}/plan',
        path_params=('connection_id',),
        query_params=(),
        request_body_type='PMPlanRequest',
        response_types=('HTTPValidationError',),
    ),
    'reconcile_integration_connection_integrations_connections__connection_id__reconcile_post': ApiOperation(
        operation_id='reconcile_integration_connection_integrations_connections__connection_id__reconcile_post',
        method='POST',
        path='/integrations/connections/{connection_id}/reconcile',
        path_params=('connection_id',),
        query_params=(),
        request_body_type='PMReconcileRequest',
        response_types=('HTTPValidationError',),
    ),
    'create_source_control_branch_integrations_connections__connection_id__source_control_branches_post': ApiOperation(
        operation_id='create_source_control_branch_integrations_connections__connection_id__source_control_branches_post',
        method='POST',
        path='/integrations/connections/{connection_id}/source-control/branches',
        path_params=('connection_id',),
        query_params=(),
        request_body_type='SCMActionRequest',
        response_types=('HTTPValidationError',),
    ),
    'publish_source_control_check_integrations_connections__connection_id__source_control_checks_post': ApiOperation(
        operation_id='publish_source_control_check_integrations_connections__connection_id__source_control_checks_post',
        method='POST',
        path='/integrations/connections/{connection_id}/source-control/checks',
        path_params=('connection_id',),
        query_params=(),
        request_body_type='SCMActionRequest',
        response_types=('HTTPValidationError',),
    ),
    'capture_source_control_commit_integrations_connections__connection_id__source_control_commits_post': ApiOperation(
        operation_id='capture_source_control_commit_integrations_connections__connection_id__source_control_commits_post',
        method='POST',
        path='/integrations/connections/{connection_id}/source-control/commits',
        path_params=('connection_id',),
        query_params=(),
        request_body_type='SCMActionRequest',
        response_types=('HTTPValidationError',),
    ),
    'list_source_control_evidence_integrations_connections__connection_id__source_control_evidence_get': ApiOperation(
        operation_id='list_source_control_evidence_integrations_connections__connection_id__source_control_evidence_get',
        method='GET',
        path='/integrations/connections/{connection_id}/source-control/evidence',
        path_params=('connection_id',),
        query_params=('evidence_type', 'limit'),
        request_body_type=None,
        response_types=('HTTPValidationError',),
    ),
    'discover_source_control_installation_integrations_connections__connection_id__source_control_installation_post': ApiOperation(
        operation_id='discover_source_control_installation_integrations_connections__connection_id__source_control_installation_post',
        method='POST',
        path='/integrations/connections/{connection_id}/source-control/installation',
        path_params=('connection_id',),
        query_params=(),
        request_body_type=None,
        response_types=('HTTPValidationError',),
    ),
    'project_source_control_pull_request_integrations_connections__connection_id__source_control_pull_requests_post': ApiOperation(
        operation_id='project_source_control_pull_request_integrations_connections__connection_id__source_control_pull_requests_post',
        method='POST',
        path='/integrations/connections/{connection_id}/source-control/pull-requests',
        path_params=('connection_id',),
        query_params=(),
        request_body_type='SCMActionRequest',
        response_types=('HTTPValidationError',),
    ),
    'publish_source_control_review_comment_integrations_connections__connection_id__source_control_review_comments_post': ApiOperation(
        operation_id='publish_source_control_review_comment_integrations_connections__connection_id__source_control_review_comments_post',
        method='POST',
        path='/integrations/connections/{connection_id}/source-control/review-comments',
        path_params=('connection_id',),
        query_params=(),
        request_body_type='SCMActionRequest',
        response_types=('HTTPValidationError',),
    ),
    'mint_source_control_run_credential_integrations_connections__connection_id__source_control_run_credentials_post': ApiOperation(
        operation_id='mint_source_control_run_credential_integrations_connections__connection_id__source_control_run_credentials_post',
        method='POST',
        path='/integrations/connections/{connection_id}/source-control/run-credentials',
        path_params=('connection_id',),
        query_params=(),
        request_body_type='SCMActionRequest',
        response_types=('HTTPValidationError',),
    ),
    'update_integration_connection_status_integrations_connections__connection_id__status_patch': ApiOperation(
        operation_id='update_integration_connection_status_integrations_connections__connection_id__status_patch',
        method='PATCH',
        path='/integrations/connections/{connection_id}/status',
        path_params=('connection_id',),
        query_params=(),
        request_body_type='PMConnectionStatusRequest',
        response_types=('HTTPValidationError',),
    ),
    'list_integration_cutovers_integrations_cutovers_get': ApiOperation(
        operation_id='list_integration_cutovers_integrations_cutovers_get',
        method='GET',
        path='/integrations/cutovers',
        path_params=(),
        query_params=('limit', 'project_id'),
        request_body_type=None,
        response_types=('HTTPValidationError',),
    ),
    'cutover_integration_binding_integrations_cutovers_post': ApiOperation(
        operation_id='cutover_integration_binding_integrations_cutovers_post',
        method='POST',
        path='/integrations/cutovers',
        path_params=(),
        query_params=(),
        request_body_type='PMCutoverRequest',
        response_types=('HTTPValidationError',),
    ),
    'get_delta_integration_readiness_integrations_delta_readiness_get': ApiOperation(
        operation_id='get_delta_integration_readiness_integrations_delta_readiness_get',
        method='GET',
        path='/integrations/delta-readiness',
        path_params=(),
        query_params=(),
        request_body_type=None,
        response_types=(),
    ),
    'check_docling_certification_integrations_docling_certification_check_post': ApiOperation(
        operation_id='check_docling_certification_integrations_docling_certification_check_post',
        method='POST',
        path='/integrations/docling/certification-check',
        path_params=(),
        query_params=(),
        request_body_type='DoclingCertificationRequest',
        response_types=('HTTPValidationError',),
    ),
    'github_repository_metadata_integrations_github_repository_metadata_post': ApiOperation(
        operation_id='github_repository_metadata_integrations_github_repository_metadata_post',
        method='POST',
        path='/integrations/github/repository-metadata',
        path_params=(),
        query_params=(),
        request_body_type='GitHubMetadataRequest',
        response_types=('HTTPValidationError',),
    ),
    'get_inbound_canary_plan_integrations_inbound_canaries__plan_id__get': ApiOperation(
        operation_id='get_inbound_canary_plan_integrations_inbound_canaries__plan_id__get',
        method='GET',
        path='/integrations/inbound-canaries/{plan_id}',
        path_params=('plan_id',),
        query_params=(),
        request_body_type=None,
        response_types=('HTTPValidationError',),
    ),
    'approve_inbound_canary_plan_integrations_inbound_canaries__plan_id__approve_post': ApiOperation(
        operation_id='approve_inbound_canary_plan_integrations_inbound_canaries__plan_id__approve_post',
        method='POST',
        path='/integrations/inbound-canaries/{plan_id}/approve',
        path_params=('plan_id',),
        query_params=(),
        request_body_type='PMInboundCanaryPlanActionRequest',
        response_types=('HTTPValidationError',),
    ),
    'arm_inbound_canary_plan_integrations_inbound_canaries__plan_id__arm_post': ApiOperation(
        operation_id='arm_inbound_canary_plan_integrations_inbound_canaries__plan_id__arm_post',
        method='POST',
        path='/integrations/inbound-canaries/{plan_id}/arm',
        path_params=('plan_id',),
        query_params=(),
        request_body_type='PMInboundCanaryPlanActionRequest',
        response_types=('HTTPValidationError',),
    ),
    'record_inbound_canary_audit_evidence_integrations_inbound_canaries__plan_id__audit_evidence_post': ApiOperation(
        operation_id='record_inbound_canary_audit_evidence_integrations_inbound_canaries__plan_id__audit_evidence_post',
        method='POST',
        path='/integrations/inbound-canaries/{plan_id}/audit-evidence',
        path_params=('plan_id',),
        query_params=(),
        request_body_type='PMInboundCanaryPlanActionRequest',
        response_types=('HTTPValidationError',),
    ),
    'disarm_inbound_canary_plan_integrations_inbound_canaries__plan_id__disarm_post': ApiOperation(
        operation_id='disarm_inbound_canary_plan_integrations_inbound_canaries__plan_id__disarm_post',
        method='POST',
        path='/integrations/inbound-canaries/{plan_id}/disarm',
        path_params=('plan_id',),
        query_params=(),
        request_body_type='PMInboundCanaryPlanActionRequest',
        response_types=('HTTPValidationError',),
    ),
    'expire_inbound_canary_plan_integrations_inbound_canaries__plan_id__expire_post': ApiOperation(
        operation_id='expire_inbound_canary_plan_integrations_inbound_canaries__plan_id__expire_post',
        method='POST',
        path='/integrations/inbound-canaries/{plan_id}/expire',
        path_params=('plan_id',),
        query_params=(),
        request_body_type='PMInboundCanaryPlanActionRequest',
        response_types=('HTTPValidationError',),
    ),
    'replay_verified_inbound_canary_event_integrations_inbound_canaries__plan_id__replay_verified_event_post': ApiOperation(
        operation_id='replay_verified_inbound_canary_event_integrations_inbound_canaries__plan_id__replay_verified_event_post',
        method='POST',
        path='/integrations/inbound-canaries/{plan_id}/replay-verified-event',
        path_params=('plan_id',),
        query_params=(),
        request_body_type='PMInboundCanaryReplayRequest',
        response_types=('HTTPValidationError',),
    ),
    'list_pm_lifecycle_plans_integrations_lifecycle_plans_get': ApiOperation(
        operation_id='list_pm_lifecycle_plans_integrations_lifecycle_plans_get',
        method='GET',
        path='/integrations/lifecycle-plans',
        path_params=(),
        query_params=('connection_id', 'limit', 'status', 'target_id'),
        request_body_type=None,
        response_types=('HTTPValidationError',),
    ),
    'create_pm_lifecycle_plan_integrations_lifecycle_plans_post': ApiOperation(
        operation_id='create_pm_lifecycle_plan_integrations_lifecycle_plans_post',
        method='POST',
        path='/integrations/lifecycle-plans',
        path_params=(),
        query_params=(),
        request_body_type='PMLifecyclePlanCreateRequest',
        response_types=('HTTPValidationError',),
    ),
    'get_pm_lifecycle_plan_integrations_lifecycle_plans__plan_id__get': ApiOperation(
        operation_id='get_pm_lifecycle_plan_integrations_lifecycle_plans__plan_id__get',
        method='GET',
        path='/integrations/lifecycle-plans/{plan_id}',
        path_params=('plan_id',),
        query_params=(),
        request_body_type=None,
        response_types=('HTTPValidationError',),
    ),
    'apply_pm_lifecycle_plan_integrations_lifecycle_plans__plan_id__apply_post': ApiOperation(
        operation_id='apply_pm_lifecycle_plan_integrations_lifecycle_plans__plan_id__apply_post',
        method='POST',
        path='/integrations/lifecycle-plans/{plan_id}/apply',
        path_params=('plan_id',),
        query_params=(),
        request_body_type='PMLifecyclePlanApplyRequest',
        response_types=('HTTPValidationError',),
    ),
    'approve_pm_lifecycle_plan_integrations_lifecycle_plans__plan_id__approve_post': ApiOperation(
        operation_id='approve_pm_lifecycle_plan_integrations_lifecycle_plans__plan_id__approve_post',
        method='POST',
        path='/integrations/lifecycle-plans/{plan_id}/approve',
        path_params=('plan_id',),
        query_params=(),
        request_body_type='PMLifecyclePlanApprovalRequest',
        response_types=('HTTPValidationError',),
    ),
    'get_pm_lifecycle_audit_integrations_lifecycle_plans__plan_id__audit_get': ApiOperation(
        operation_id='get_pm_lifecycle_audit_integrations_lifecycle_plans__plan_id__audit_get',
        method='GET',
        path='/integrations/lifecycle-plans/{plan_id}/audit',
        path_params=('plan_id',),
        query_params=(),
        request_body_type=None,
        response_types=('HTTPValidationError',),
    ),
    'reject_pm_lifecycle_plan_integrations_lifecycle_plans__plan_id__reject_post': ApiOperation(
        operation_id='reject_pm_lifecycle_plan_integrations_lifecycle_plans__plan_id__reject_post',
        method='POST',
        path='/integrations/lifecycle-plans/{plan_id}/reject',
        path_params=('plan_id',),
        query_params=(),
        request_body_type='PMLifecyclePlanRejectRequest',
        response_types=('HTTPValidationError',),
    ),
    'n8n_edge_policy_integrations_n8n_edge_policy_post': ApiOperation(
        operation_id='n8n_edge_policy_integrations_n8n_edge_policy_post',
        method='POST',
        path='/integrations/n8n/edge-policy',
        path_params=(),
        query_params=(),
        request_body_type='N8nEdgePolicyRequest',
        response_types=('HTTPValidationError',),
    ),
    'list_integration_outbox_integrations_outbox_get': ApiOperation(
        operation_id='list_integration_outbox_integrations_outbox_get',
        method='GET',
        path='/integrations/outbox',
        path_params=(),
        query_params=('connection_id', 'limit'),
        request_body_type=None,
        response_types=('HTTPValidationError',),
    ),
    'drain_integration_outbox_integrations_outbox_drain_post': ApiOperation(
        operation_id='drain_integration_outbox_integrations_outbox_drain_post',
        method='POST',
        path='/integrations/outbox/drain',
        path_params=(),
        query_params=('limit',),
        request_body_type=None,
        response_types=('HTTPValidationError',),
    ),
    'dispose_integration_outbox_integrations_outbox__outbox_id__disposition_post': ApiOperation(
        operation_id='dispose_integration_outbox_integrations_outbox__outbox_id__disposition_post',
        method='POST',
        path='/integrations/outbox/{outbox_id}/disposition',
        path_params=('outbox_id',),
        query_params=(),
        request_body_type='PMOutboxDispositionRequest',
        response_types=('HTTPValidationError',),
    ),
    'list_integration_reconciliation_runs_integrations_reconciliation_runs_get': ApiOperation(
        operation_id='list_integration_reconciliation_runs_integrations_reconciliation_runs_get',
        method='GET',
        path='/integrations/reconciliation-runs',
        path_params=(),
        query_params=('connection_id', 'limit'),
        request_body_type=None,
        response_types=('HTTPValidationError',),
    ),
    'rollback_integration_binding_integrations_rollbacks_post': ApiOperation(
        operation_id='rollback_integration_binding_integrations_rollbacks_post',
        method='POST',
        path='/integrations/rollbacks',
        path_params=(),
        query_params=(),
        request_body_type='PMRollbackRequest',
        response_types=('HTTPValidationError',),
    ),
    'receive_integration_webhook_integrations_webhooks__connection_id__post': ApiOperation(
        operation_id='receive_integration_webhook_integrations_webhooks__connection_id__post',
        method='POST',
        path='/integrations/webhooks/{connection_id}',
        path_params=('connection_id',),
        query_params=(),
        request_body_type=None,
        response_types=('HTTPValidationError',),
    ),
    'team_runner_storage_internal_team_runners__team_id__storage_post': ApiOperation(
        operation_id='team_runner_storage_internal_team_runners__team_id__storage_post',
        method='POST',
        path='/internal/team-runners/{team_id}/storage',
        path_params=('team_id',),
        query_params=(),
        request_body_type='TeamRunnerStorageRequest',
        response_types=('HTTPValidationError',),
    ),
    'prometheus_metrics_metrics_get': ApiOperation(
        operation_id='prometheus_metrics_metrics_get',
        method='GET',
        path='/metrics',
        path_params=(),
        query_params=(),
        request_body_type=None,
        response_types=(),
    ),
    'create_model_override_model_overrides_post': ApiOperation(
        operation_id='create_model_override_model_overrides_post',
        method='POST',
        path='/model-overrides',
        path_params=(),
        query_params=(),
        request_body_type='ModelOverrideCreateRequest',
        response_types=('HTTPValidationError',),
    ),
    'decide_model_override_model_overrides__override_id__decision_post': ApiOperation(
        operation_id='decide_model_override_model_overrides__override_id__decision_post',
        method='POST',
        path='/model-overrides/{override_id}/decision',
        path_params=('override_id',),
        query_params=(),
        request_body_type='ModelOverrideDecisionRequest',
        response_types=('HTTPValidationError',),
    ),
    'list_model_profiles_model_profiles_get': ApiOperation(
        operation_id='list_model_profiles_model_profiles_get',
        method='GET',
        path='/model-profiles',
        path_params=(),
        query_params=(),
        request_body_type=None,
        response_types=(),
    ),
    'create_model_profile_model_profiles_post': ApiOperation(
        operation_id='create_model_profile_model_profiles_post',
        method='POST',
        path='/model-profiles',
        path_params=(),
        query_params=(),
        request_body_type='ModelProfileCreateRequest',
        response_types=('HTTPValidationError',),
    ),
    'model_profile_catalogue_model_profiles_catalogue_get': ApiOperation(
        operation_id='model_profile_catalogue_model_profiles_catalogue_get',
        method='GET',
        path='/model-profiles/catalogue',
        path_params=(),
        query_params=(),
        request_body_type=None,
        response_types=(),
    ),
    'preview_model_resolution_model_profiles_resolve_preview_post': ApiOperation(
        operation_id='preview_model_resolution_model_profiles_resolve_preview_post',
        method='POST',
        path='/model-profiles/resolve-preview',
        path_params=(),
        query_params=(),
        request_body_type='ModelResolutionPreviewRequest',
        response_types=('HTTPValidationError',),
    ),
    'add_model_profile_version_model_profiles__profile_id__versions_post': ApiOperation(
        operation_id='add_model_profile_version_model_profiles__profile_id__versions_post',
        method='POST',
        path='/model-profiles/{profile_id}/versions',
        path_params=('profile_id',),
        query_params=(),
        request_body_type='ModelProfileVersionRequest',
        response_types=('HTTPValidationError',),
    ),
    'get_capacity_forecast_observability_capacity_forecast_get': ApiOperation(
        operation_id='get_capacity_forecast_observability_capacity_forecast_get',
        method='GET',
        path='/observability/capacity/forecast',
        path_params=(),
        query_params=('company_id', 'forecast_days', 'window_days'),
        request_body_type=None,
        response_types=('CapacityForecast', 'HTTPValidationError'),
    ),
    'get_operational_slo_report_observability_slo_get': ApiOperation(
        operation_id='get_operational_slo_report_observability_slo_get',
        method='GET',
        path='/observability/slo',
        path_params=(),
        query_params=('company_id', 'window_days'),
        request_body_type=None,
        response_types=('SLOReport', 'HTTPValidationError'),
    ),
    'get_trace_evidence_observability_traces__trace_id__get': ApiOperation(
        operation_id='get_trace_evidence_observability_traces__trace_id__get',
        method='GET',
        path='/observability/traces/{trace_id}',
        path_params=('trace_id',),
        query_params=('limit',),
        request_body_type=None,
        response_types=('TraceEvidence', 'HTTPValidationError'),
    ),
    'list_projects_projects_get': ApiOperation(
        operation_id='list_projects_projects_get',
        method='GET',
        path='/projects',
        path_params=(),
        query_params=('limit', 'offset', 'state'),
        request_body_type=None,
        response_types=('HTTPValidationError',),
    ),
    'create_project_projects_post': ApiOperation(
        operation_id='create_project_projects_post',
        method='POST',
        path='/projects',
        path_params=(),
        query_params=(),
        request_body_type='CreateProjectRequest',
        response_types=('HTTPValidationError',),
    ),
    'create_self_improvement_project_projects_self_improvement_post': ApiOperation(
        operation_id='create_self_improvement_project_projects_self_improvement_post',
        method='POST',
        path='/projects/self-improvement',
        path_params=(),
        query_params=(),
        request_body_type='ImprovementOpportunity',
        response_types=('HTTPValidationError',),
    ),
    'delete_project_projects__project_id__delete': ApiOperation(
        operation_id='delete_project_projects__project_id__delete',
        method='DELETE',
        path='/projects/{project_id}',
        path_params=('project_id',),
        query_params=(),
        request_body_type=None,
        response_types=('HTTPValidationError',),
    ),
    'get_project_projects__project_id__get': ApiOperation(
        operation_id='get_project_projects__project_id__get',
        method='GET',
        path='/projects/{project_id}',
        path_params=('project_id',),
        query_params=(),
        request_body_type=None,
        response_types=('HTTPValidationError',),
    ),
    'allowed_transitions_projects__project_id__allowed_transitions_get': ApiOperation(
        operation_id='allowed_transitions_projects__project_id__allowed_transitions_get',
        method='GET',
        path='/projects/{project_id}/allowed-transitions',
        path_params=('project_id',),
        query_params=(),
        request_body_type=None,
        response_types=('HTTPValidationError',),
    ),
    'archive_project_projects__project_id__archive_post': ApiOperation(
        operation_id='archive_project_projects__project_id__archive_post',
        method='POST',
        path='/projects/{project_id}/archive',
        path_params=('project_id',),
        query_params=(),
        request_body_type=None,
        response_types=('HTTPValidationError',),
    ),
    'list_project_artifacts_projects__project_id__artifacts_get': ApiOperation(
        operation_id='list_project_artifacts_projects__project_id__artifacts_get',
        method='GET',
        path='/projects/{project_id}/artifacts',
        path_params=('project_id',),
        query_params=('limit',),
        request_body_type=None,
        response_types=('HTTPValidationError',),
    ),
    'create_project_artifact_projects__project_id__artifacts_post': ApiOperation(
        operation_id='create_project_artifact_projects__project_id__artifacts_post',
        method='POST',
        path='/projects/{project_id}/artifacts',
        path_params=('project_id',),
        query_params=(),
        request_body_type='CreateArtifactRequest',
        response_types=('HTTPValidationError',),
    ),
    'delete_project_artifact_projects__project_id__artifacts__artifact_id__delete': ApiOperation(
        operation_id='delete_project_artifact_projects__project_id__artifacts__artifact_id__delete',
        method='DELETE',
        path='/projects/{project_id}/artifacts/{artifact_id}',
        path_params=('artifact_id', 'project_id'),
        query_params=(),
        request_body_type=None,
        response_types=('HTTPValidationError',),
    ),
    'get_project_audit_timeline_projects__project_id__audit_timeline_get': ApiOperation(
        operation_id='get_project_audit_timeline_projects__project_id__audit_timeline_get',
        method='GET',
        path='/projects/{project_id}/audit-timeline',
        path_params=('project_id',),
        query_params=('limit',),
        request_body_type=None,
        response_types=('HTTPValidationError',),
    ),
    'list_project_context_projects__project_id__context_get': ApiOperation(
        operation_id='list_project_context_projects__project_id__context_get',
        method='GET',
        path='/projects/{project_id}/context',
        path_params=('project_id',),
        query_params=('include_revisions', 'item_type', 'tags'),
        request_body_type=None,
        response_types=('HTTPValidationError',),
    ),
    'create_project_context_item_projects__project_id__context_post': ApiOperation(
        operation_id='create_project_context_item_projects__project_id__context_post',
        method='POST',
        path='/projects/{project_id}/context',
        path_params=('project_id',),
        query_params=(),
        request_body_type='CreateContextItemRequest',
        response_types=('HTTPValidationError',),
    ),
    'create_context_chunk_projects__project_id__context_chunks_post': ApiOperation(
        operation_id='create_context_chunk_projects__project_id__context_chunks_post',
        method='POST',
        path='/projects/{project_id}/context/chunks',
        path_params=('project_id',),
        query_params=(),
        request_body_type='CreateContextItemRequest',
        response_types=('HTTPValidationError',),
    ),
    'hybrid_search_context_projects__project_id__context_hybrid_search_post': ApiOperation(
        operation_id='hybrid_search_context_projects__project_id__context_hybrid_search_post',
        method='POST',
        path='/projects/{project_id}/context/hybrid-search',
        path_params=('project_id',),
        query_params=(),
        request_body_type='HybridSearchRequest',
        response_types=('HTTPValidationError',),
    ),
    'search_project_context_projects__project_id__context_search_post': ApiOperation(
        operation_id='search_project_context_projects__project_id__context_search_post',
        method='POST',
        path='/projects/{project_id}/context/search',
        path_params=('project_id',),
        query_params=(),
        request_body_type='SearchContextRequest',
        response_types=('HTTPValidationError',),
    ),
    'delete_project_context_item_projects__project_id__context__item_id__delete': ApiOperation(
        operation_id='delete_project_context_item_projects__project_id__context__item_id__delete',
        method='DELETE',
        path='/projects/{project_id}/context/{item_id}',
        path_params=('item_id', 'project_id'),
        query_params=(),
        request_body_type=None,
        response_types=('HTTPValidationError',),
    ),
    'get_project_context_item_projects__project_id__context__item_id__get': ApiOperation(
        operation_id='get_project_context_item_projects__project_id__context__item_id__get',
        method='GET',
        path='/projects/{project_id}/context/{item_id}',
        path_params=('item_id', 'project_id'),
        query_params=(),
        request_body_type=None,
        response_types=('HTTPValidationError',),
    ),
    'submit_decision_projects__project_id__decisions_post': ApiOperation(
        operation_id='submit_decision_projects__project_id__decisions_post',
        method='POST',
        path='/projects/{project_id}/decisions',
        path_params=('project_id',),
        query_params=(),
        request_body_type='DecisionRequest',
        response_types=('HTTPValidationError',),
    ),
    'list_documents_projects__project_id__documents_get': ApiOperation(
        operation_id='list_documents_projects__project_id__documents_get',
        method='GET',
        path='/projects/{project_id}/documents',
        path_params=('project_id',),
        query_params=('doc_type',),
        request_body_type=None,
        response_types=('HTTPValidationError',),
    ),
    'create_project_document_projects__project_id__documents_post': ApiOperation(
        operation_id='create_project_document_projects__project_id__documents_post',
        method='POST',
        path='/projects/{project_id}/documents',
        path_params=('project_id',),
        query_params=(),
        request_body_type='CreateDocumentRequest',
        response_types=('HTTPValidationError',),
    ),
    'get_document_projects__project_id__documents__doc_id__get': ApiOperation(
        operation_id='get_document_projects__project_id__documents__doc_id__get',
        method='GET',
        path='/projects/{project_id}/documents/{doc_id}',
        path_params=('doc_id', 'project_id'),
        query_params=(),
        request_body_type=None,
        response_types=('HTTPValidationError',),
    ),
    'download_project_document_projects__project_id__documents__doc_id__download_get': ApiOperation(
        operation_id='download_project_document_projects__project_id__documents__doc_id__download_get',
        method='GET',
        path='/projects/{project_id}/documents/{doc_id}/download',
        path_params=('doc_id', 'project_id'),
        query_params=(),
        request_body_type=None,
        response_types=('HTTPValidationError',),
    ),
    'preview_project_document_projects__project_id__documents__doc_id__preview_get': ApiOperation(
        operation_id='preview_project_document_projects__project_id__documents__doc_id__preview_get',
        method='GET',
        path='/projects/{project_id}/documents/{doc_id}/preview',
        path_params=('doc_id', 'project_id'),
        query_params=(),
        request_body_type=None,
        response_types=('HTTPValidationError',),
    ),
    'create_project_document_revision_projects__project_id__documents__doc_id__revisions_post': ApiOperation(
        operation_id='create_project_document_revision_projects__project_id__documents__doc_id__revisions_post',
        method='POST',
        path='/projects/{project_id}/documents/{doc_id}/revisions',
        path_params=('doc_id', 'project_id'),
        query_params=(),
        request_body_type='CreateDocumentRevisionRequest',
        response_types=('HTTPValidationError',),
    ),
    'update_project_document_status_projects__project_id__documents__doc_id__status_patch': ApiOperation(
        operation_id='update_project_document_status_projects__project_id__documents__doc_id__status_patch',
        method='PATCH',
        path='/projects/{project_id}/documents/{doc_id}/status',
        path_params=('doc_id', 'project_id'),
        query_params=(),
        request_body_type='DocumentStatusRequest',
        response_types=('HTTPValidationError',),
    ),
    'get_project_evidence_projects__project_id__evidence_get': ApiOperation(
        operation_id='get_project_evidence_projects__project_id__evidence_get',
        method='GET',
        path='/projects/{project_id}/evidence',
        path_params=('project_id',),
        query_params=(),
        request_body_type=None,
        response_types=('HTTPValidationError',),
    ),
    'set_project_evidence_policy_projects__project_id__evidence_policy_put': ApiOperation(
        operation_id='set_project_evidence_policy_projects__project_id__evidence_policy_put',
        method='PUT',
        path='/projects/{project_id}/evidence-policy',
        path_params=('project_id',),
        query_params=(),
        request_body_type='ProjectEvidencePolicyRequest',
        response_types=('HTTPValidationError',),
    ),
    'get_project_evidence_package_projects__project_id__evidence_package_get': ApiOperation(
        operation_id='get_project_evidence_package_projects__project_id__evidence_package_get',
        method='GET',
        path='/projects/{project_id}/evidence/package',
        path_params=('project_id',),
        query_params=(),
        request_body_type=None,
        response_types=('HTTPValidationError',),
    ),
    'persist_project_evidence_package_projects__project_id__evidence_package_post': ApiOperation(
        operation_id='persist_project_evidence_package_projects__project_id__evidence_package_post',
        method='POST',
        path='/projects/{project_id}/evidence/package',
        path_params=('project_id',),
        query_params=(),
        request_body_type=None,
        response_types=('HTTPValidationError',),
    ),
    'validate_project_evidence_projects__project_id__evidence_validate_post': ApiOperation(
        operation_id='validate_project_evidence_projects__project_id__evidence_validate_post',
        method='POST',
        path='/projects/{project_id}/evidence/validate',
        path_params=('project_id',),
        query_params=(),
        request_body_type=None,
        response_types=('HTTPValidationError',),
    ),
    'get_feasibility_projects__project_id__feasibility_get': ApiOperation(
        operation_id='get_feasibility_projects__project_id__feasibility_get',
        method='GET',
        path='/projects/{project_id}/feasibility',
        path_params=('project_id',),
        query_params=(),
        request_body_type=None,
        response_types=('HTTPValidationError',),
    ),
    'get_project_flow_instance_projects__project_id__flow_instance_get': ApiOperation(
        operation_id='get_project_flow_instance_projects__project_id__flow_instance_get',
        method='GET',
        path='/projects/{project_id}/flow-instance',
        path_params=('project_id',),
        query_params=(),
        request_body_type=None,
        response_types=('HTTPValidationError',),
    ),
    'list_project_issues_projects__project_id__issues_get': ApiOperation(
        operation_id='list_project_issues_projects__project_id__issues_get',
        method='GET',
        path='/projects/{project_id}/issues',
        path_params=('project_id',),
        query_params=('assigned_team', 'limit', 'sprint_id', 'status'),
        request_body_type=None,
        response_types=('HTTPValidationError',),
    ),
    'create_canonical_issue_projects__project_id__issues_post': ApiOperation(
        operation_id='create_canonical_issue_projects__project_id__issues_post',
        method='POST',
        path='/projects/{project_id}/issues',
        path_params=('project_id',),
        query_params=(),
        request_body_type='CanonicalIssueCreateRequest',
        response_types=('HTTPValidationError',),
    ),
    'get_canonical_issue_projects__project_id__issues__issue_id__get': ApiOperation(
        operation_id='get_canonical_issue_projects__project_id__issues__issue_id__get',
        method='GET',
        path='/projects/{project_id}/issues/{issue_id}',
        path_params=('issue_id', 'project_id'),
        query_params=(),
        request_body_type=None,
        response_types=('HTTPValidationError',),
    ),
    'update_canonical_issue_projects__project_id__issues__issue_id__patch': ApiOperation(
        operation_id='update_canonical_issue_projects__project_id__issues__issue_id__patch',
        method='PATCH',
        path='/projects/{project_id}/issues/{issue_id}',
        path_params=('issue_id', 'project_id'),
        query_params=(),
        request_body_type='CanonicalIssueUpdateRequest',
        response_types=('HTTPValidationError',),
    ),
    'list_canonical_issue_comments_projects__project_id__issues__issue_id__comments_get': ApiOperation(
        operation_id='list_canonical_issue_comments_projects__project_id__issues__issue_id__comments_get',
        method='GET',
        path='/projects/{project_id}/issues/{issue_id}/comments',
        path_params=('issue_id', 'project_id'),
        query_params=(),
        request_body_type=None,
        response_types=('HTTPValidationError',),
    ),
    'comment_on_canonical_issue_projects__project_id__issues__issue_id__comments_post': ApiOperation(
        operation_id='comment_on_canonical_issue_projects__project_id__issues__issue_id__comments_post',
        method='POST',
        path='/projects/{project_id}/issues/{issue_id}/comments',
        path_params=('issue_id', 'project_id'),
        query_params=(),
        request_body_type='CanonicalIssueCommentRequest',
        response_types=('HTTPValidationError',),
    ),
    'list_canonical_issue_links_projects__project_id__issues__issue_id__links_get': ApiOperation(
        operation_id='list_canonical_issue_links_projects__project_id__issues__issue_id__links_get',
        method='GET',
        path='/projects/{project_id}/issues/{issue_id}/links',
        path_params=('issue_id', 'project_id'),
        query_params=(),
        request_body_type=None,
        response_types=('HTTPValidationError',),
    ),
    'link_canonical_issue_projects__project_id__issues__issue_id__links_post': ApiOperation(
        operation_id='link_canonical_issue_projects__project_id__issues__issue_id__links_post',
        method='POST',
        path='/projects/{project_id}/issues/{issue_id}/links',
        path_params=('issue_id', 'project_id'),
        query_params=(),
        request_body_type='CanonicalIssueLinkRequest',
        response_types=('HTTPValidationError',),
    ),
    'list_project_kpi_projects__project_id__kpi_get': ApiOperation(
        operation_id='list_project_kpi_projects__project_id__kpi_get',
        method='GET',
        path='/projects/{project_id}/kpi',
        path_params=('project_id',),
        query_params=('scope',),
        request_body_type=None,
        response_types=('HTTPValidationError',),
    ),
    'save_project_kpi_projects__project_id__kpi_post': ApiOperation(
        operation_id='save_project_kpi_projects__project_id__kpi_post',
        method='POST',
        path='/projects/{project_id}/kpi',
        path_params=('project_id',),
        query_params=(),
        request_body_type='KpiSnapshotRequest',
        response_types=('HTTPValidationError',),
    ),
    'get_project_overview_projects__project_id__overview_get': ApiOperation(
        operation_id='get_project_overview_projects__project_id__overview_get',
        method='GET',
        path='/projects/{project_id}/overview',
        path_params=('project_id',),
        query_params=(),
        request_body_type=None,
        response_types=('HTTPValidationError',),
    ),
    'get_pending_decisions_projects__project_id__pending_decisions_get': ApiOperation(
        operation_id='get_pending_decisions_projects__project_id__pending_decisions_get',
        method='GET',
        path='/projects/{project_id}/pending-decisions',
        path_params=('project_id',),
        query_params=(),
        request_body_type=None,
        response_types=('HTTPValidationError',),
    ),
    'list_project_pm_bindings_projects__project_id__pm_bindings_get': ApiOperation(
        operation_id='list_project_pm_bindings_projects__project_id__pm_bindings_get',
        method='GET',
        path='/projects/{project_id}/pm-bindings',
        path_params=('project_id',),
        query_params=(),
        request_body_type=None,
        response_types=('HTTPValidationError',),
    ),
    'create_project_pm_binding_projects__project_id__pm_bindings_post': ApiOperation(
        operation_id='create_project_pm_binding_projects__project_id__pm_bindings_post',
        method='POST',
        path='/projects/{project_id}/pm-bindings',
        path_params=('project_id',),
        query_params=(),
        request_body_type='PMBindingCreateRequest',
        response_types=('HTTPValidationError',),
    ),
    'update_project_pm_binding_projects__project_id__pm_bindings__binding_id__patch': ApiOperation(
        operation_id='update_project_pm_binding_projects__project_id__pm_bindings__binding_id__patch',
        method='PATCH',
        path='/projects/{project_id}/pm-bindings/{binding_id}',
        path_params=('binding_id', 'project_id'),
        query_params=(),
        request_body_type='PMBindingUpdateRequest',
        response_types=('HTTPValidationError',),
    ),
    'apply_project_pm_provisioning_projects__project_id__pm_provisioning_apply_post': ApiOperation(
        operation_id='apply_project_pm_provisioning_projects__project_id__pm_provisioning_apply_post',
        method='POST',
        path='/projects/{project_id}/pm-provisioning/apply',
        path_params=('project_id',),
        query_params=(),
        request_body_type='PMProjectProvisioningApplyRequest',
        response_types=('HTTPValidationError',),
    ),
    'plan_project_pm_provisioning_projects__project_id__pm_provisioning_plan_post': ApiOperation(
        operation_id='plan_project_pm_provisioning_projects__project_id__pm_provisioning_plan_post',
        method='POST',
        path='/projects/{project_id}/pm-provisioning/plan',
        path_params=('project_id',),
        query_params=(),
        request_body_type='PMProjectProvisioningRequest',
        response_types=('HTTPValidationError',),
    ),
    'get_project_repository_projects__project_id__repository_get': ApiOperation(
        operation_id='get_project_repository_projects__project_id__repository_get',
        method='GET',
        path='/projects/{project_id}/repository',
        path_params=('project_id',),
        query_params=(),
        request_body_type=None,
        response_types=('HTTPValidationError',),
    ),
    'manage_project_repository_projects__project_id__repository_post': ApiOperation(
        operation_id='manage_project_repository_projects__project_id__repository_post',
        method='POST',
        path='/projects/{project_id}/repository',
        path_params=('project_id',),
        query_params=(),
        request_body_type='ProjectRepositoryActionRequest',
        response_types=('HTTPValidationError',),
    ),
    'retry_project_projects__project_id__retry_post': ApiOperation(
        operation_id='retry_project_projects__project_id__retry_post',
        method='POST',
        path='/projects/{project_id}/retry',
        path_params=('project_id',),
        query_params=(),
        request_body_type=None,
        response_types=('HTTPValidationError',),
    ),
    'list_project_review_sessions_projects__project_id__review_sessions_get': ApiOperation(
        operation_id='list_project_review_sessions_projects__project_id__review_sessions_get',
        method='GET',
        path='/projects/{project_id}/review-sessions',
        path_params=('project_id',),
        query_params=('limit',),
        request_body_type=None,
        response_types=('HTTPValidationError',),
    ),
    'get_project_review_session_projects__project_id__review_sessions__session_id__get': ApiOperation(
        operation_id='get_project_review_session_projects__project_id__review_sessions__session_id__get',
        method='GET',
        path='/projects/{project_id}/review-sessions/{session_id}',
        path_params=('project_id', 'session_id'),
        query_params=(),
        request_body_type=None,
        response_types=('HTTPValidationError',),
    ),
    'get_self_improvement_lifecycle_projects__project_id__self_improvement_get': ApiOperation(
        operation_id='get_self_improvement_lifecycle_projects__project_id__self_improvement_get',
        method='GET',
        path='/projects/{project_id}/self-improvement',
        path_params=('project_id',),
        query_params=(),
        request_body_type=None,
        response_types=('HTTPValidationError',),
    ),
    'apply_self_improvement_action_projects__project_id__self_improvement_actions_post': ApiOperation(
        operation_id='apply_self_improvement_action_projects__project_id__self_improvement_actions_post',
        method='POST',
        path='/projects/{project_id}/self-improvement/actions',
        path_params=('project_id',),
        query_params=(),
        request_body_type='SelfImprovementActionRequest',
        response_types=('HTTPValidationError',),
    ),
    'link_self_improvement_reference_projects__project_id__self_improvement_references_post': ApiOperation(
        operation_id='link_self_improvement_reference_projects__project_id__self_improvement_references_post',
        method='POST',
        path='/projects/{project_id}/self-improvement/references',
        path_params=('project_id',),
        query_params=(),
        request_body_type='SelfImprovementReferenceRequest',
        response_types=('HTTPValidationError',),
    ),
    'get_sprints_projects__project_id__sprints_get': ApiOperation(
        operation_id='get_sprints_projects__project_id__sprints_get',
        method='GET',
        path='/projects/{project_id}/sprints',
        path_params=('project_id',),
        query_params=(),
        request_body_type=None,
        response_types=('HTTPValidationError',),
    ),
    'create_canonical_sprint_projects__project_id__sprints_post': ApiOperation(
        operation_id='create_canonical_sprint_projects__project_id__sprints_post',
        method='POST',
        path='/projects/{project_id}/sprints',
        path_params=('project_id',),
        query_params=(),
        request_body_type='CanonicalSprintCreateRequest',
        response_types=('HTTPValidationError',),
    ),
    'get_canonical_sprint_projects__project_id__sprints__sprint_id__get': ApiOperation(
        operation_id='get_canonical_sprint_projects__project_id__sprints__sprint_id__get',
        method='GET',
        path='/projects/{project_id}/sprints/{sprint_id}',
        path_params=('project_id', 'sprint_id'),
        query_params=(),
        request_body_type=None,
        response_types=('HTTPValidationError',),
    ),
    'update_canonical_sprint_projects__project_id__sprints__sprint_id__patch': ApiOperation(
        operation_id='update_canonical_sprint_projects__project_id__sprints__sprint_id__patch',
        method='PATCH',
        path='/projects/{project_id}/sprints/{sprint_id}',
        path_params=('project_id', 'sprint_id'),
        query_params=(),
        request_body_type='CanonicalSprintUpdateRequest',
        response_types=('HTTPValidationError',),
    ),
    'get_state_history_projects__project_id__state_history_get': ApiOperation(
        operation_id='get_state_history_projects__project_id__state_history_get',
        method='GET',
        path='/projects/{project_id}/state-history',
        path_params=('project_id',),
        query_params=('limit',),
        request_body_type=None,
        response_types=('HTTPValidationError',),
    ),
    'transition_project_projects__project_id__transition_post': ApiOperation(
        operation_id='transition_project_projects__project_id__transition_post',
        method='POST',
        path='/projects/{project_id}/transition',
        path_params=('project_id',),
        query_params=(),
        request_body_type='TransitionRequest',
        response_types=('HTTPValidationError',),
    ),
    'list_project_usage_events_projects__project_id__usage_events_get': ApiOperation(
        operation_id='list_project_usage_events_projects__project_id__usage_events_get',
        method='GET',
        path='/projects/{project_id}/usage/events',
        path_params=('project_id',),
        query_params=('limit', 'offset'),
        request_body_type=None,
        response_types=('HTTPValidationError',),
    ),
    'get_project_workspace_projects__project_id__workspace_get': ApiOperation(
        operation_id='get_project_workspace_projects__project_id__workspace_get',
        method='GET',
        path='/projects/{project_id}/workspace',
        path_params=('project_id',),
        query_params=(),
        request_body_type=None,
        response_types=('HTTPValidationError',),
    ),
    'list_available_runtimes_runtimes_get': ApiOperation(
        operation_id='list_available_runtimes_runtimes_get',
        method='GET',
        path='/runtimes',
        path_params=(),
        query_params=(),
        request_body_type=None,
        response_types=(),
    ),
    'benchmark_runtime_runtimes_benchmark_post': ApiOperation(
        operation_id='benchmark_runtime_runtimes_benchmark_post',
        method='POST',
        path='/runtimes/benchmark',
        path_params=(),
        query_params=(),
        request_body_type='RuntimeValidationRequest',
        response_types=('HTTPValidationError',),
    ),
    'validate_runtime_runtimes_validate_post': ApiOperation(
        operation_id='validate_runtime_runtimes_validate_post',
        method='POST',
        path='/runtimes/validate',
        path_params=(),
        query_params=(),
        request_body_type='RuntimeValidationRequest',
        response_types=('HTTPValidationError',),
    ),
    'list_stewards_stewards_get': ApiOperation(
        operation_id='list_stewards_stewards_get',
        method='GET',
        path='/stewards',
        path_params=(),
        query_params=('limit', 'status'),
        request_body_type=None,
        response_types=('HTTPValidationError',),
    ),
    'get_company_overview_system_company_get': ApiOperation(
        operation_id='get_company_overview_system_company_get',
        method='GET',
        path='/system/company',
        path_params=(),
        query_params=(),
        request_body_type=None,
        response_types=(),
    ),
    'stream_container_logs_system_logs__container__get': ApiOperation(
        operation_id='stream_container_logs_system_logs__container__get',
        method='GET',
        path='/system/logs/{container}',
        path_params=('container',),
        query_params=('follow', 'tail'),
        request_body_type=None,
        response_types=('HTTPValidationError',),
    ),
    'get_org_graph_system_org_graph_get': ApiOperation(
        operation_id='get_org_graph_system_org_graph_get',
        method='GET',
        path='/system/org-graph',
        path_params=(),
        query_params=(),
        request_body_type=None,
        response_types=(),
    ),
    'system_resume_system_resume_post': ApiOperation(
        operation_id='system_resume_system_resume_post',
        method='POST',
        path='/system/resume',
        path_params=(),
        query_params=(),
        request_body_type=None,
        response_types=(),
    ),
    'update_schedule_system_schedule_put': ApiOperation(
        operation_id='update_schedule_system_schedule_put',
        method='PUT',
        path='/system/schedule',
        path_params=(),
        query_params=(),
        request_body_type='ScheduleRequest',
        response_types=('HTTPValidationError',),
    ),
    'seed_default_company_system_seed_default_company_post': ApiOperation(
        operation_id='seed_default_company_system_seed_default_company_post',
        method='POST',
        path='/system/seed-default-company',
        path_params=(),
        query_params=(),
        request_body_type=None,
        response_types=(),
    ),
    'system_shutdown_system_shutdown_post': ApiOperation(
        operation_id='system_shutdown_system_shutdown_post',
        method='POST',
        path='/system/shutdown',
        path_params=(),
        query_params=(),
        request_body_type=None,
        response_types=(),
    ),
    'shutdown_ack_system_shutdown_ack_post': ApiOperation(
        operation_id='shutdown_ack_system_shutdown_ack_post',
        method='POST',
        path='/system/shutdown-ack',
        path_params=(),
        query_params=(),
        request_body_type=None,
        response_types=('HTTPValidationError',),
    ),
    'shutdown_nack_system_shutdown_nack_post': ApiOperation(
        operation_id='shutdown_nack_system_shutdown_nack_post',
        method='POST',
        path='/system/shutdown-nack',
        path_params=(),
        query_params=(),
        request_body_type=None,
        response_types=('HTTPValidationError',),
    ),
    'system_status_system_status_get': ApiOperation(
        operation_id='system_status_system_status_get',
        method='GET',
        path='/system/status',
        path_params=(),
        query_params=(),
        request_body_type=None,
        response_types=(),
    ),
    'create_task_tasks_post': ApiOperation(
        operation_id='create_task_tasks_post',
        method='POST',
        path='/tasks',
        path_params=(),
        query_params=(),
        request_body_type=None,
        response_types=('HTTPValidationError',),
    ),
    'get_task_tasks__task_id__get': ApiOperation(
        operation_id='get_task_tasks__task_id__get',
        method='GET',
        path='/tasks/{task_id}',
        path_params=('task_id',),
        query_params=(),
        request_body_type=None,
        response_types=('HTTPValidationError',),
    ),
    'list_teams_teams_get': ApiOperation(
        operation_id='list_teams_teams_get',
        method='GET',
        path='/teams',
        path_params=(),
        query_params=(),
        request_body_type=None,
        response_types=(),
    ),
    'get_usage_event_evidence_usage_events__event_id__get': ApiOperation(
        operation_id='get_usage_event_evidence_usage_events__event_id__get',
        method='GET',
        path='/usage/events/{event_id}',
        path_params=('event_id',),
        query_params=(),
        request_body_type=None,
        response_types=('HTTPValidationError',),
    ),
    'chat_completions_v1_chat_completions_post': ApiOperation(
        operation_id='chat_completions_v1_chat_completions_post',
        method='POST',
        path='/v1/chat/completions',
        path_params=(),
        query_params=(),
        request_body_type='ChatCompletionRequest',
        response_types=('HTTPValidationError',),
    ),
    'legacy_completions_v1_completions_post': ApiOperation(
        operation_id='legacy_completions_v1_completions_post',
        method='POST',
        path='/v1/completions',
        path_params=(),
        query_params=(),
        request_body_type='LegacyCompletionRequest',
        response_types=('HTTPValidationError',),
    ),
    'list_models_v1_models_get': ApiOperation(
        operation_id='list_models_v1_models_get',
        method='GET',
        path='/v1/models',
        path_params=(),
        query_params=(),
        request_body_type=None,
        response_types=('HTTPValidationError',),
    ),
    'worker_contract_version_worker_contract_version_get': ApiOperation(
        operation_id='worker_contract_version_worker_contract_version_get',
        method='GET',
        path='/worker-contract/version',
        path_params=(),
        query_params=(),
        request_body_type=None,
        response_types=(),
    ),
    'list_worker_runs_api_workers_runs_get': ApiOperation(
        operation_id='list_worker_runs_api_workers_runs_get',
        method='GET',
        path='/workers/runs',
        path_params=(),
        query_params=('flow_instance_id', 'limit', 'offset', 'project_id', 'state', 'worker_id'),
        request_body_type=None,
        response_types=('HTTPValidationError',),
    ),
    'dispatch_worker_run_workers_runs_post': ApiOperation(
        operation_id='dispatch_worker_run_workers_runs_post',
        method='POST',
        path='/workers/runs',
        path_params=(),
        query_params=(),
        request_body_type='WorkerRunDispatchRequest',
        response_types=('HTTPValidationError',),
    ),
    'recover_expired_worker_runs_workers_runs_recover_expired_post': ApiOperation(
        operation_id='recover_expired_worker_runs_workers_runs_recover_expired_post',
        method='POST',
        path='/workers/runs/recover-expired',
        path_params=(),
        query_params=('limit',),
        request_body_type=None,
        response_types=('HTTPValidationError',),
    ),
    'get_worker_run_api_workers_runs__run_id__get': ApiOperation(
        operation_id='get_worker_run_api_workers_runs__run_id__get',
        method='GET',
        path='/workers/runs/{run_id}',
        path_params=('run_id',),
        query_params=(),
        request_body_type=None,
        response_types=('HTTPValidationError',),
    ),
    'get_worker_run_artifacts_workers_runs__run_id__artifacts_get': ApiOperation(
        operation_id='get_worker_run_artifacts_workers_runs__run_id__artifacts_get',
        method='GET',
        path='/workers/runs/{run_id}/artifacts',
        path_params=('run_id',),
        query_params=('limit',),
        request_body_type=None,
        response_types=('HTTPValidationError',),
    ),
    'cancel_worker_run_workers_runs__run_id__cancel_post': ApiOperation(
        operation_id='cancel_worker_run_workers_runs__run_id__cancel_post',
        method='POST',
        path='/workers/runs/{run_id}/cancel',
        path_params=('run_id',),
        query_params=(),
        request_body_type=None,
        response_types=('HTTPValidationError',),
    ),
    'get_worker_run_checkpoints_workers_runs__run_id__checkpoints_get': ApiOperation(
        operation_id='get_worker_run_checkpoints_workers_runs__run_id__checkpoints_get',
        method='GET',
        path='/workers/runs/{run_id}/checkpoints',
        path_params=('run_id',),
        query_params=('limit',),
        request_body_type=None,
        response_types=('HTTPValidationError',),
    ),
    'get_worker_run_events_workers_runs__run_id__events_get': ApiOperation(
        operation_id='get_worker_run_events_workers_runs__run_id__events_get',
        method='GET',
        path='/workers/runs/{run_id}/events',
        path_params=('run_id',),
        query_params=('limit', 'offset'),
        request_body_type=None,
        response_types=('HTTPValidationError',),
    ),
    'heartbeat_worker_run_workers_runs__run_id__heartbeat_post': ApiOperation(
        operation_id='heartbeat_worker_run_workers_runs__run_id__heartbeat_post',
        method='POST',
        path='/workers/runs/{run_id}/heartbeat',
        path_params=('run_id',),
        query_params=(),
        request_body_type=None,
        response_types=('HTTPValidationError',),
    ),
    'pause_worker_run_workers_runs__run_id__pause_post': ApiOperation(
        operation_id='pause_worker_run_workers_runs__run_id__pause_post',
        method='POST',
        path='/workers/runs/{run_id}/pause',
        path_params=('run_id',),
        query_params=(),
        request_body_type='WorkerRunPauseRequest',
        response_types=('HTTPValidationError',),
    ),
    'resume_worker_run_workers_runs__run_id__resume_post': ApiOperation(
        operation_id='resume_worker_run_workers_runs__run_id__resume_post',
        method='POST',
        path='/workers/runs/{run_id}/resume',
        path_params=('run_id',),
        query_params=(),
        request_body_type='WorkerRunResumeRequest',
        response_types=('HTTPValidationError',),
    ),
    'get_worker_run_transitions_workers_runs__run_id__transitions_get': ApiOperation(
        operation_id='get_worker_run_transitions_workers_runs__run_id__transitions_get',
        method='GET',
        path='/workers/runs/{run_id}/transitions',
        path_params=('run_id',),
        query_params=('limit',),
        request_body_type=None,
        response_types=('HTTPValidationError',),
    ),
    'get_worker_run_usage_workers_runs__run_id__usage_get': ApiOperation(
        operation_id='get_worker_run_usage_workers_runs__run_id__usage_get',
        method='GET',
        path='/workers/runs/{run_id}/usage',
        path_params=('run_id',),
        query_params=('limit',),
        request_body_type=None,
        response_types=('HTTPValidationError',),
    ),
}

MODEL_COUNT = 130
OPERATION_COUNT = 268

__all__ = [
    "ApiOperation",
    "MODEL_COUNT",
    "OPERATION_COUNT",
    "OPERATIONS",
    'AgentEstimateRequest',
    'AgentProfileObservationRequest',
    'BootstrapAction',
    'BootstrapPlan',
    'CandidateApprovalRequest',
    'CandidateCertificationRequest',
    'CandidateGenerationRequest',
    'CandidateStageAdvanceRequest',
    'CanonicalIssueCommentRequest',
    'CanonicalIssueCreateRequest',
    'CanonicalIssueLinkRequest',
    'CanonicalIssueUpdateRequest',
    'CanonicalSprintCreateRequest',
    'CanonicalSprintUpdateRequest',
    'CapabilitySearchRequest',
    'CapacityForecast',
    'ChatCompletionRequest',
    'ChatMessage',
    'ChunkingStrategy',
    'CompanyCreateRequest',
    'CompanyManifestRequest',
    'CompanyManifestRollbackRequest',
    'CreateArtifactRequest',
    'CreateContextItemRequest',
    'CreateCredentialRequest',
    'CreateDocumentRequest',
    'CreateDocumentRevisionRequest',
    'CreateFlowInstanceRequest',
    'CreateFlowRequest',
    'CreateProjectRequest',
    'CredentialApprovalDecisionRequest',
    'CredentialApprovalRequest',
    'DashboardSectionACLRequest',
    'DecisionRequest',
    'DoclingCertificationRequest',
    'DocumentStatusRequest',
    'DocumentationSnapshotRequest',
    'EvidencePolicyRequest',
    'ExecutiveCEOPrivilegedActionRequest',
    'ExecutiveCFOModelOverrideRequest',
    'ExecutiveCTOWorkerRunRequest',
    'FlowDiffRequest',
    'FlowDryRunRequest',
    'FlowFromTemplateRequest',
    'FlowImportRequest',
    'FlowInstanceActionRequest',
    'FlowLegacyTaskMigrationRequest',
    'FlowMigrationRequest',
    'FlowNodeActionRequest',
    'FlowOverrideRequest',
    'GateName',
    'GitHubMetadataRequest',
    'HTTPValidationError',
    'HybridSearchRequest',
    'IdentityDashboardActionRequest',
    'ImportWorkersRequest',
    'ImprovementArtifact',
    'ImprovementArtifactBundle',
    'ImprovementArtifactKind',
    'ImprovementOpportunity',
    'ImprovementOutcomeKind',
    'ImprovementRisk',
    'KpiSnapshotRequest',
    'LegacyCompletionRequest',
    'ModelOverrideCreateRequest',
    'ModelOverrideDecisionRequest',
    'ModelProfileCreateRequest',
    'ModelProfileVersionRequest',
    'ModelResolutionPreviewRequest',
    'N8nEdgePolicyRequest',
    'OperatorToCeoRequest',
    'PMApplyRequest',
    'PMBindingCreateRequest',
    'PMBindingUpdateRequest',
    'PMConflictResolutionRequest',
    'PMConnectionCreateRequest',
    'PMConnectionStatusRequest',
    'PMCutoverRequest',
    'PMExternalActorMappingCreateRequest',
    'PMInboundCanaryPlanActionRequest',
    'PMInboundCanaryPlanCreateRequest',
    'PMInboundCanaryReplayRequest',
    'PMLifecyclePlanApplyRequest',
    'PMLifecyclePlanApprovalRequest',
    'PMLifecyclePlanCreateRequest',
    'PMLifecyclePlanRejectRequest',
    'PMOutboxDispositionRequest',
    'PMPlanRequest',
    'PMProjectProvisioningApplyRequest',
    'PMProjectProvisioningRequest',
    'PMReconcileRequest',
    'PMRollbackRequest',
    'PrivilegedActionRequest',
    'PrivilegedApprovalRequest',
    'ProjectContextSeedRequest',
    'ProjectEvidencePolicyRequest',
    'ProjectProvisioningPlan',
    'ProjectRepositoryActionRequest',
    'ProjectWorkspaceRequest',
    'RegisterWorkerRequest',
    'ResolveCredentialRequest',
    'RollbackRequest',
    'RolloutAdvanceRequest',
    'RolloutStartRequest',
    'RuntimeValidationRequest',
    'SCMActionRequest',
    'SLOPolicy',
    'SLOReport',
    'SLOStatus',
    'SLOTarget',
    'ScheduleRequest',
    'SearchContextRequest',
    'SelfImprovementActionRequest',
    'SelfImprovementReferenceRequest',
    'StewardCreateRequest',
    'TeamRunnerStorageRequest',
    'TraceEvidence',
    'TraceEvidenceItem',
    'TraceRetentionPolicy',
    'TransitionRequest',
    'UpdateCredentialRequest',
    'UpdateFlowRequest',
    'UpdateWorkerRequest',
    'ValidationError',
    'WorkerEvaluateRequest',
    'WorkerRunDispatchRequest',
    'WorkerRunPauseRequest',
    'WorkerRunResumeRequest',
    'WorkerStatusTransition',
    'WorkerUpgradeRequest',
]