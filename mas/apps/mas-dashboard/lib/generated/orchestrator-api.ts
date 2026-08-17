/**
 * GENERATED FILE — do not edit by hand.
 * Source: schemas/http/orchestrator.openapi.json
 * Regenerate: uv run python scripts/generate_typescript_api.py --write
 */

export type AgentEstimateRequest = {
  raw_estimate_hours: number;
};

export type AgentProfileObservationRequest = {
  actual_hours?: number;
  alpha?: number;
  estimated_hours?: number;
  role?: (string | null);
  tasks_completed?: number;
  team_id?: (string | null);
};

export type BootstrapAction = {
  action: string;
  current?: ({
  [key: string]: unknown;
} | null);
  desired?: {
  [key: string]: unknown;
};
  destructive?: boolean;
  manual?: boolean;
  reason?: (string | null);
  resource: string;
};

export type BootstrapPlan = {
  actions?: Array<BootstrapAction>;
  blockers?: Array<string>;
  checks?: Array<string>;
  connection_id: string;
  generated_at?: string;
  plan_id?: string;
  provider_kind: string;
  rollback_actions?: Array<string>;
};

export type CandidateApprovalRequest = {
  decided_by: string;
  evidence?: {
  [key: string]: unknown;
};
  reason: string;
};

export type CandidateCertificationRequest = {
  checks?: {
  [key: string]: boolean;
};
  conformance?: {
  [key: string]: unknown;
};
};

export type CandidateGenerationRequest = {
  adapter_entrypoint?: (string | null);
  adapter_version: string;
  diff?: {
  [key: string]: unknown;
};
  implementation_ref?: (string | null);
  migration_notes?: Array<string>;
  semantic_version: string;
  upstream_compatibility_range: string;
};

export type CandidateStageAdvanceRequest = {
  actor: string;
  evidence?: {
  [key: string]: unknown;
};
  target_status: string;
};

export type CanonicalIssueCommentRequest = {
  actor_id?: string;
  approval_id?: (string | null);
  body: string;
  body_blob_ref?: (string | null);
  evidence_id?: (string | null);
  run_id?: (string | null);
};

export type CanonicalIssueCreateRequest = {
  assigned_agent?: (string | null);
  assigned_team?: (string | null);
  description?: (string | null);
  estimated_hours?: (number | null);
  issue_type?: string;
  priority?: string;
  sprint_id?: (string | null);
  story_points?: (number | null);
  title?: string;
};

export type CanonicalIssueLinkRequest = {
  link_type: string;
  metadata?: {
  [key: string]: unknown;
};
  target_id: string;
  target_type: string;
};

export type CanonicalIssueUpdateRequest = {
  actual_hours?: (number | null);
  assigned_agent?: (string | null);
  assigned_team?: (string | null);
  description?: (string | null);
  estimated_hours?: (number | null);
  expected_revision?: (number | null);
  priority?: (string | null);
  status?: (string | null);
  story_points?: (number | null);
  title?: (string | null);
};

export type CanonicalSprintCreateRequest = {
  estimated_hours?: (number | null);
  goal?: (string | null);
  milestone?: (string | null);
  planned_story_points?: (number | null);
  sprint_number?: number;
};

export type CanonicalSprintUpdateRequest = {
  actual_hours?: (number | null);
  completed_story_points?: (number | null);
  estimated_hours?: (number | null);
  expected_revision?: (number | null);
  goal?: (string | null);
  milestone?: (string | null);
  planned_story_points?: (number | null);
  status?: (string | null);
};

export type CapabilitySearchRequest = {
  min_sandbox_tier?: number;
  name?: (string | null);
  role?: (string | null);
};

export type CapacityForecast = {
  active_project_count?: number;
  average_daily_cost_usd?: number;
  average_daily_tokens?: number;
  basis?: "project_usage_events";
  budget_limit_usd?: (number | null);
  budget_source?: ("company_budgets" | "not_configured" | "caller");
  confidence?: ("high" | "medium" | "low" | "none");
  forecast_days: number;
  generated_at?: (string | null);
  notices?: Array<{
  [key: string]: string;
}>;
  observed_cost_usd?: number;
  observed_event_count?: number;
  observed_span_days?: number;
  observed_total_tokens?: number;
  projected_budget_headroom_usd?: (number | null);
  projected_cost_usd?: number;
  projected_tokens?: number;
  schema_version?: string;
  status: ("clear" | "attention" | "insufficient_data");
  window_days: number;
};

export type ChatCompletionRequest = {
  max_tokens?: (number | null);
  messages: Array<ChatMessage>;
  model?: string;
  response_format?: ({
  [key: string]: unknown;
} | null);
  stop?: (string | Array<string> | null);
  stream?: boolean;
  temperature?: (number | null);
  tool_choice?: (string | {
  [key: string]: unknown;
} | null);
  tools?: (Array<{
  [key: string]: unknown;
}> | null);
  top_p?: (number | null);
  user?: (string | null);
};

export type ChatMessage = {
  content?: (string | Array<{
  [key: string]: unknown;
}> | null);
  name?: (string | null);
  role: string;
  tool_call_id?: (string | null);
  tool_calls?: (Array<{
  [key: string]: unknown;
}> | null);
};

export type ChunkingStrategy = ("fixed_size" | "semantic" | "sliding_window");

export type CompanyCreateRequest = {
  created_by?: string;
  description?: string;
  name: string;
  slug: string;
};

export type CompanyManifestRequest = {
  manifest: {
  [key: string]: unknown;
};
  source?: string;
};

export type CompanyManifestRollbackRequest = {
  manifest_version: number;
  reason: string;
};

export type CreateArtifactRequest = {
  agent_id?: string;
  metadata?: {
  [key: string]: unknown;
};
  path: string;
  sha256?: (string | null);
  size_bytes?: (number | null);
};

export type CreateContextItemRequest = {
  blob_bucket?: (string | null);
  blob_key?: (string | null);
  blob_sha256?: (string | null);
  chunk_overlap?: number;
  chunk_size?: number;
  chunking_strategy?: ChunkingStrategy;
  content_text?: (string | null);
  description?: (string | null);
  generate_embeddings?: boolean;
  item_type: string;
  metadata?: ({
  [key: string]: unknown;
} | null);
  mime_type?: (string | null);
  name: string;
  size_bytes?: (number | null);
  tags?: (Array<string> | null);
  url?: (string | null);
};

export type CreateCredentialRequest = {
  created_by?: string;
  description?: string;
  name: string;
  policy?: {
  [key: string]: unknown;
};
  secret_type?: string;
  value: string;
};

export type CreateDocumentRequest = {
  blob_bucket?: (string | null);
  blob_key?: (string | null);
  blob_sha256?: (string | null);
  created_by?: string;
  doc_type: string;
};

export type CreateDocumentRevisionRequest = {
  blob_bucket?: (string | null);
  blob_key?: (string | null);
  blob_sha256?: (string | null);
  created_by?: string;
};

export type CreateFlowInstanceRequest = {
  department_id?: (string | null);
  flow_id: string;
  project_id: string;
  task_id?: (string | null);
};

export type CreateFlowRequest = {
  created_by?: string;
  definition_json: {
  [key: string]: unknown;
};
  description?: (string | null);
  is_active?: boolean;
  name: string;
  version_from_flow_id?: (string | null);
};

export type CreateProjectRequest = {
  company_id?: string;
  config?: ({
  [key: string]: unknown;
} | null);
  description?: (string | null);
  flow_id?: (string | null);
  human_requester?: (string | null);
  initial_context?: Array<ProjectContextSeedRequest>;
  name: string;
  workspace?: (ProjectWorkspaceRequest | null);
};

export type CredentialApprovalDecisionRequest = {
  approved: boolean;
  decided_by?: string;
  reason?: string;
};

export type CredentialApprovalRequest = {
  context: string;
  requested_by?: string;
  requester: string;
  ttl_seconds?: number;
};

export type DashboardSectionACLRequest = {
  principals?: Array<string>;
};

export type DecisionRequest = {
  comments?: (string | null);
  decided_by?: string;
  decision: string;
  edits?: ({
  [key: string]: unknown;
} | null);
};

export type DoclingCertificationRequest = {
  artifact_path?: (string | null);
  content_text?: (string | null);
  mime_type?: string;
  project_id?: (string | null);
  source_name?: string;
};

export type DocumentStatusRequest = {
  status: string;
};

export type DocumentationSnapshotRequest = {
  content_ref?: (string | null);
  content_sha256: string;
  extracted_interfaces?: {
  [key: string]: unknown;
};
  security_findings?: Array<string>;
  untrusted?: boolean;
  uri: string;
  version: string;
};

export type EvidencePolicyRequest = {
  milestone?: (string | null);
  policy_id: string;
  policy_version?: string;
  requirements?: {
  [key: string]: unknown;
};
  scope?: ("project" | "milestone");
};

export type ExecutiveCEOPrivilegedActionRequest = {
  action: string;
  payload?: {
  [key: string]: unknown;
};
  requested_by?: string;
};

export type ExecutiveCFOModelOverrideRequest = {
  project_id: string;
  reason: string;
  requested_by?: string;
  requested_profile_id: string;
  scope?: {
  [key: string]: unknown;
};
};

export type ExecutiveCTOWorkerRunRequest = {
  dispatch: WorkerRunDispatchRequest;
  requested_by?: string;
};

export type FlowDiffRequest = {
  from_flow_id: string;
  to_flow_id: string;
};

export type FlowDryRunRequest = {
  definition_json: {
  [key: string]: unknown;
};
  project_id?: (string | null);
};

export type FlowFromTemplateRequest = {
  created_by?: string;
  description?: (string | null);
  is_active?: boolean;
  name?: (string | null);
  template_id: string;
};

export type FlowImportRequest = {
  created_by?: string;
  definition_json: {
  [key: string]: unknown;
};
  description?: (string | null);
  is_active?: boolean;
  name: string;
  version_from_flow_id?: (string | null);
};

export type FlowInstanceActionRequest = {
  action: string;
  node_id?: (string | null);
};

export type FlowLegacyTaskMigrationRequest = {
  actor_id?: string;
  description?: (string | null);
  dry_run?: boolean;
  is_active?: boolean;
  model_profile_bindings?: {
  [key: string]: string;
};
  name?: (string | null);
  worker_bindings?: {
  [key: string]: string;
};
};

export type FlowMigrationRequest = {
  active_node_mapping?: {
  [key: string]: string;
};
  actor_id?: string;
  allow_graph_rewrite?: boolean;
  flow_id: string;
  preserve_context?: boolean;
};

export type FlowNodeActionRequest = {
  action: string;
  approved?: (boolean | null);
  decision?: (string | null);
  error?: (string | null);
  node_id: string;
  output?: ({
  [key: string]: unknown;
} | null);
  worker_run_id?: (string | null);
};

export type FlowOverrideRequest = {
  actor_id?: string;
  actor_role?: string;
  reason?: (string | null);
  target_node_id: string;
};

export type GateName = ("coding" | "testing" | "review" | "security" | "migration" | "rollback" | "human_approval");

export type GitHubMetadataRequest = {
  credential_approval_id?: (string | null);
  credential_name?: (string | null);
  dry_run?: boolean;
  repo_url: string;
  requester?: string;
};

export type HTTPValidationError = {
  detail?: Array<ValidationError>;
};

export type HybridSearchRequest = {
  filters?: ({
  [key: string]: unknown;
} | null);
  limit?: number;
  query: string;
  query_vector?: (Array<number> | null);
  use_semantic?: boolean;
};

export type IdentityDashboardActionRequest = {
  action: ("approval.approve" | "approval.reject" | "identity.suspend" | "identity.archive" | "external.rotate_credentials" | "external.suspend" | "external.close" | "session.revoke");
  id?: (string | null);
  reason?: string;
  service?: (string | null);
  service_category?: string;
  worker_id?: (string | null);
};

export type ImportWorkersRequest = {
  dry_run?: boolean;
  workers_dir?: string;
};

export type ImprovementArtifact = {
  artifact_id?: string;
  candidate_version: string;
  canonical_artifact_id?: (string | null);
  immutable?: true;
  kind: ImprovementArtifactKind;
  metadata?: {
  [key: string]: string;
};
  sha256: string;
  size_bytes: number;
  source_revision: string;
  target_version?: (string | null);
  uri: string;
};

export type ImprovementArtifactBundle = {
  artifacts: Array<ImprovementArtifact>;
  bundle_id?: string;
  candidate_version: string;
  generated_by: string;
  generated_by_kind: ("human" | "agent" | "system");
  manifest_sha256?: string;
  metadata?: {
  [key: string]: string;
};
  schema_version?: string;
};

export type ImprovementArtifactKind = ("change" | "provenance" | "sbom" | "migration" | "rollback");

export type ImprovementOpportunity = {
  budget_usd: (number | string);
  company_id?: (string | null);
  created_by: string;
  created_by_kind: ("human" | "agent" | "system");
  description: string;
  evidence_policy: string;
  licence_metadata?: {
  [key: string]: unknown;
};
  opportunity_id?: string;
  owner: string;
  owner_kind?: ("human" | "agent" | "system");
  risk: ImprovementRisk;
  source: string;
  title: string;
};

export type ImprovementOutcomeKind = ("success" | "failure" | "rolled_back" | "cancelled");

export type ImprovementRisk = ("low" | "medium" | "high" | "critical");

export type KpiSnapshotRequest = {
  budget_adherence?: (number | null);
  defect_rate?: (number | null);
  estimation_accuracy?: (number | null);
  infra_lead_time_seconds?: (number | null);
  raw_data?: ({
  [key: string]: unknown;
} | null);
  resource_utilization?: (number | null);
  review_pass_rate?: (number | null);
  rework_rate?: (number | null);
  scope: string;
  sprint_id?: (string | null);
  task_completion_rate?: (number | null);
  velocity?: (number | null);
};

export type LegacyCompletionRequest = {
  max_tokens?: (number | null);
  model?: string;
  prompt: (string | Array<string>);
  stream?: boolean;
  temperature?: (number | null);
};

export type ModelOverrideCreateRequest = {
  project_id: string;
  reason: string;
  requested_by: string;
  requested_profile_id: string;
  scope?: {
  [key: string]: unknown;
};
};

export type ModelOverrideDecisionRequest = {
  decided_by: string;
  decision: ("APPROVED" | "REJECTED");
  evidence?: {
  [key: string]: unknown;
};
  expires_at?: (string | null);
  reason: string;
};

export type ModelProfileCreateRequest = {
  approved_provider_ids?: Array<string>;
  fallback_profile_ids?: Array<string>;
  profile_id: string;
  purpose: string;
  required_capabilities?: Array<string>;
  status?: string;
};

export type ModelProfileVersionRequest = {
  capabilities?: Array<string>;
  context_window?: number;
  cost_per_1k_input_usd?: number;
  cost_per_1k_output_usd?: number;
  effective_from?: (string | null);
  effective_until?: (string | null);
  embedding?: boolean;
  exact_model_id: string;
  latency_target_ms?: (number | null);
  local?: boolean;
  max_concurrency?: (number | null);
  max_cost_usd?: (number | null);
  max_output_tokens?: number;
  max_tokens_per_request?: (number | null);
  privacy_class?: string;
  provider_id: string;
  provider_settings?: {
  [key: string]: unknown;
};
  reasoning?: boolean;
  regions?: Array<string>;
  status?: string;
  streaming?: boolean;
  structured_output?: boolean;
  tool_calling?: boolean;
  version: string;
  vision?: boolean;
};

export type ModelResolutionPreviewRequest = {
  adapter_required_capabilities?: Array<string>;
  budget_usd?: (number | null);
  expected_output_tokens?: number;
  layers?: Array<{
  [key: string]: unknown;
}>;
  prompt_tokens?: number;
  requested_profile_id?: (string | null);
  requested_raw_model_id?: (string | null);
  steward_required_capabilities?: Array<string>;
  task_required_capabilities?: Array<string>;
  task_type: string;
  worker_required_capabilities?: Array<string>;
};

export type N8nEdgePolicyRequest = {
  allow_control_plane?: boolean;
  credential_name?: (string | null);
  owner_department?: string;
  webhook_url: string;
};

export type OperatorToCeoRequest = {
  async_mode?: boolean;
  context_confirmation_token?: (string | null);
  context_worker_id?: (string | null);
  message: string;
  request_id?: (string | null);
};

export type PMApplyRequest = {
  confirm?: boolean;
  plan: BootstrapPlan;
  plan_digest: string;
};

export type PMBindingCreateRequest = {
  connection_id: string;
  direction?: ("outbound" | "inbound" | "both");
  external_project_id?: (string | null);
  external_project_key?: (string | null);
  external_repository?: (string | null);
  mapping_profile?: string;
  status?: ("DISABLED" | "SHADOW" | "READ_ONLY" | "ACTIVE" | "DRAINING");
};

export type PMBindingUpdateRequest = {
  direction?: (("outbound" | "inbound" | "both") | null);
  external_project_id?: (string | null);
  external_project_key?: (string | null);
  external_repository?: (string | null);
  mapping_profile?: (string | null);
  status?: (("DISABLED" | "SHADOW" | "READ_ONLY" | "ACTIVE" | "DRAINING") | null);
};

export type PMConflictResolutionRequest = {
  resolution?: {
  [key: string]: unknown;
};
  status?: ("RESOLVED" | "IGNORED" | "REOPENED");
};

export type PMConnectionCreateRequest = {
  base_url: string;
  capability_profile?: string;
  config?: {
  [key: string]: unknown;
};
  created_by?: string;
  credential_ref: string;
  display_name: string;
  provider_kind: string;
};

export type PMConnectionStatusRequest = {
  status: ("DISABLED" | "SHADOW" | "READ_ONLY" | "ACTIVE" | "DRAINING");
};

export type PMCutoverRequest = {
  binding_id: string;
  confirm?: boolean;
  project_id: string;
};

export type PMExternalActorMappingCreateRequest = {
  authorized_scopes?: Array<"issue.priority">;
  inbox_event_ids: Array<string>;
  reason?: string;
};

export type PMInboundCanaryPlanActionRequest = {
  confirm?: boolean;
  digest: string;
  reason?: (string | null);
};

export type PMInboundCanaryPlanCreateRequest = {
  actor_mapping_id: string;
  binding_id: string;
  canonical_issue_id: string;
  external_issue_id: string;
  mapping_id: string;
  target_priority?: (("low" | "medium" | "high" | "urgent" | "critical" | "normal") | null);
  ttl_seconds?: number;
};

export type PMInboundCanaryReplayRequest = {
  confirm?: boolean;
  digest: string;
  inbox_id: string;
  reason: string;
};

export type PMLifecyclePlanApplyRequest = {
  confirm?: boolean;
  plan_digest: string;
};

export type PMLifecyclePlanApprovalRequest = {
  plan_digest: string;
  reason?: (string | null);
};

export type PMLifecyclePlanCreateRequest = {
  binding_id?: (string | null);
  connection_id: string;
  desired_binding_status?: (("DISABLED" | "SHADOW" | "READ_ONLY" | "ACTIVE" | "DRAINING") | null);
  desired_connection_status?: (("DISABLED" | "SHADOW" | "READ_ONLY" | "ACTIVE" | "DRAINING") | null);
  target_type?: ("pm_connection" | "pm_binding");
  ttl_seconds?: number;
};

export type PMLifecyclePlanRejectRequest = {
  plan_digest: string;
  reason?: (string | null);
};

export type PMOutboxDispositionRequest = {
  disposition: ("RESOLVED" | "SUPERSEDED");
  provider_state?: {
  [key: string]: unknown;
};
  reason: string;
};

export type PMPlanRequest = {
  desired?: {
  [key: string]: unknown;
};
};

export type PMProjectProvisioningApplyRequest = {
  confirm?: boolean;
  plan: ProjectProvisioningPlan;
  plan_digest: string;
};

export type PMProjectProvisioningRequest = {
  connection_id: string;
  external_project_id?: (string | null);
  mapping_profile?: string;
};

export type PMReconcileRequest = {
  binding_id?: (string | null);
  cursor?: (string | null);
  limit?: number;
  mode?: ("audit" | "repair_proposal");
};

export type PMRollbackRequest = {
  binding_id: string;
  confirm?: boolean;
  project_id: string;
};

export type PrivilegedActionRequest = {
  action: string;
  actor_id?: string;
  actor_role?: string;
  payload?: {
  [key: string]: unknown;
};
};

export type PrivilegedApprovalRequest = {
  approved: boolean;
  decided_by: string;
  reason?: string;
};

export type ProjectContextSeedRequest = {
  blob_bucket?: (string | null);
  blob_key?: (string | null);
  blob_sha256?: (string | null);
  content_text?: (string | null);
  description?: (string | null);
  item_type?: string;
  metadata?: ({
  [key: string]: unknown;
} | null);
  mime_type?: (string | null);
  name?: string;
  size_bytes?: (number | null);
  tags?: Array<string>;
  url?: (string | null);
};

export type ProjectEvidencePolicyRequest = {
  milestone?: (string | null);
  policy_id: string;
  policy_version?: string;
  requirements?: {
  [key: string]: unknown;
};
  scope?: ("project" | "milestone");
};

export type ProjectProvisioningPlan = {
  actions?: Array<BootstrapAction>;
  blockers?: Array<string>;
  checks?: Array<string>;
  connection_id: string;
  external_project_id?: (string | null);
  external_project_key?: (string | null);
  generated_at?: string;
  manual_actions?: Array<string>;
  mapping_profile?: string;
  plan_id?: string;
  project_id: string;
  provider_kind: string;
  rollback_actions?: Array<string>;
};

export type ProjectRepositoryActionRequest = {
  message?: (string | null);
  operation?: ("status" | "sync" | "commit" | "push");
};

export type ProjectWorkspaceRequest = {
  branch?: (string | null);
  mode?: ("init" | "clone" | "none");
  remote_name?: string;
  repository_url?: (string | null);
};

export type RegisterWorkerRequest = {
  adapter_config?: {
  [key: string]: unknown;
};
  adapter_type: string;
  capability_ids?: Array<string>;
  capability_names?: Array<string>;
  identity_mailbox_class?: string;
  model_mode?: string;
  model_profile_id?: (string | null);
  name: string;
  required_tools?: Array<string>;
  role?: (string | null);
  sandbox_profile?: string;
  source_repo?: (string | null);
  team_id?: (string | null);
  update_policy?: ("manual" | "auto-patch" | "auto-minor" | "auto-all");
  version_pin?: (string | null);
};

export type ResolveCredentialRequest = {
  context?: string;
  requester?: string;
};

export type RollbackRequest = {
  reason: string;
};

export type RolloutAdvanceRequest = {
  comparison_metrics?: {
  [key: string]: number;
};
  sample_count?: (number | null);
  target_status: string;
};

export type RolloutStartRequest = {
  actor: string;
  eligible_task_classes?: Array<string>;
};

export type RuntimeValidationRequest = {
  dry_run?: boolean;
  runtime_config?: {
  [key: string]: unknown;
};
  runtime_tier: string;
};

export type SCMActionRequest = {
  payload?: {
  [key: string]: unknown;
};
};

export type SLOPolicy = {
  policy_version?: string;
  schema_version?: string;
  source?: ("aiat_default" | "company_manifest");
  targets?: Array<SLOTarget>;
};

export type SLOReport = {
  generated_at?: (string | null);
  notices?: Array<{
  [key: string]: string;
}>;
  observed_service_count?: number;
  policy: SLOPolicy;
  schema_version?: string;
  status: ("healthy" | "attention" | "no_data");
  statuses?: Array<SLOStatus>;
};

export type SLOStatus = {
  error_budget_remaining?: (number | null);
  good_count?: number;
  latency_p95_ms?: (number | null);
  max_latency_ms?: (number | null);
  name: string;
  objective: number;
  observed_success_rate?: (number | null);
  sample_count?: number;
  service: string;
  source?: string;
  status: ("healthy" | "attention" | "no_data");
  window: string;
};

export type SLOTarget = {
  max_latency_ms?: (number | null);
  minimum_samples?: number;
  name: string;
  objective: number;
  service: ("orchestrator_api" | "queue_age" | "worker_startup" | "worker_run" | "tool_latency" | "model_routing" | "pm_scm_sync" | "mail_delivery" | "recovery");
  source?: ("aiat_default" | "company_manifest");
  window?: ("rolling_24h" | "rolling_7d" | "rolling_30d");
};

export type ScheduleRequest = {
  auto_resume?: boolean;
  auto_shutdown?: boolean;
  days?: Array<string>;
  enabled?: boolean;
  end_hour?: number;
  start_hour?: number;
  timezone?: (string | null);
};

export type SearchContextRequest = {
  limit?: number;
  query: string;
};

export type SelfImprovementActionRequest = {
  action: ("record_gate" | "start_shadow" | "record_observation" | "start_canary" | "request_promotion" | "approve_promotion" | "rollback" | "record_outcome" | "record_artifacts" | "record_artifact_readback");
  actual_sha256?: (string | null);
  actual_size_bytes?: (number | null);
  artifact_bundle?: (ImprovementArtifactBundle | null);
  artifact_id?: (string | null);
  candidate_version?: (string | null);
  canonical_artifact_id?: (string | null);
  cost_usd?: (number | string | null);
  detail?: (string | null);
  evidence_refs?: Array<string>;
  gate?: (GateName | null);
  incident_count?: (number | null);
  irreversible_side_effects?: number;
  kpi_learning?: {
  [key: string]: number;
};
  outcome?: (ImprovementOutcomeKind | null);
  outcome_id?: (string | null);
  passed?: (boolean | null);
  readback_source?: (string | null);
  reason?: (string | null);
  regression_fraction?: (number | null);
  rollback_performed?: (boolean | null);
  sample_count?: (number | null);
  stage?: (("shadow" | "canary") | null);
};

export type SelfImprovementReferenceRequest = {
  kind: ("issue" | "worker_run" | "artifact" | "artifact_readback" | "budget_reservation" | "branch" | "sbom" | "deployment" | "evidence" | "repository");
  reference: string;
};

export type StewardCreateRequest = {
  adapter_version?: (string | null);
  commit_sha?: (string | null);
  dependency_lock_hash?: (string | null);
  exact_release?: (string | null);
  license_id?: (string | null);
  monitoring_cadence?: string;
  oci_image_digest?: (string | null);
  package_version?: (string | null);
  protocol_api_version?: (string | null);
  redistribution_status?: string;
  security_scan_status?: string;
  source_provider?: string;
  source_repo?: (string | null);
  transport_type?: string;
};

export type TeamRunnerStorageRequest = {
  operation: ("storage_health" | "checkpoint_save" | "checkpoint_load" | "checkpoint_latest" | "checkpoint_delete" | "usage_record" | "document_get" | "document_create" | "document_update_status" | "review_create" | "review_get" | "review_update" | "review_comment_add" | "review_comments_get" | "review_list");
  payload?: {
  [key: string]: unknown;
};
};

export type TraceEvidence = {
  coverage?: {
  [key: string]: string;
};
  first_observed_at?: (string | null);
  generated_at?: (string | null);
  item_count?: number;
  items?: Array<TraceEvidenceItem>;
  last_observed_at?: (string | null);
  notices?: Array<{
  [key: string]: string;
}>;
  project_ids?: Array<string>;
  retention?: TraceRetentionPolicy;
  schema_version?: string;
  source_counts?: {
  [key: string]: number;
};
  status: ("observed" | "not_found");
  trace_id: string;
};

export type TraceEvidenceItem = {
  agent_id?: (string | null);
  artifact_id?: (string | null);
  completion_tokens?: (number | null);
  connection_id?: (string | null);
  cost_usd?: (number | null);
  duration_ms?: (number | null);
  event_type?: (string | null);
  exact_model_id?: (string | null);
  id: string;
  kind: string;
  model?: (string | null);
  occurred_at?: (string | null);
  operation?: (string | null);
  parent_span_id?: (string | null);
  project_id?: (string | null);
  prompt_tokens?: (number | null);
  provider_id?: (string | null);
  request_method?: (string | null);
  route?: (string | null);
  sampled?: (boolean | null);
  service?: (string | null);
  sha256?: (string | null);
  size_bytes?: (number | null);
  source: ("api_requests" | "task_log" | "project_usage_events" | "worker_run_transitions" | "worker_usage_records" | "worker_artifacts" | "pm_inbox_events" | "integration_evidence" | "native_spans");
  span_id?: (string | null);
  status?: (string | null);
  status_code?: (number | null);
  team_id?: (string | null);
  tool_name?: (string | null);
  total_tokens?: (number | null);
  worker_run_id?: (string | null);
};

export type TraceIncident = {
  affected_sources?: Array<string>;
  coverage_status: ("complete" | "partial" | "empty");
  finding_count?: number;
  findings?: Array<TraceIncidentFinding>;
  first_observed_at?: (string | null);
  generated_at?: (string | null);
  item_count?: number;
  last_observed_at?: (string | null);
  notice_codes?: Array<string>;
  project_ids?: Array<string>;
  schema_version?: string;
  severity: ("info" | "warning" | "critical");
  source_counts?: {
  [key: string]: number;
};
  status: ("clear" | "attention" | "not_found");
  trace_id: string;
};

export type TraceIncidentFinding = {
  id: string;
  kind: string;
  occurred_at?: (string | null);
  operation?: (string | null);
  project_id?: (string | null);
  service?: (string | null);
  source: string;
  status?: (string | null);
  status_code?: (number | null);
  worker_run_id?: (string | null);
};

export type TraceRetentionPolicy = {
  retention_days?: number;
  sample_rate?: number;
  schema_version?: string;
  source?: ("company_manifest" | "default");
  terminal_mode?: ("archive" | "delete");
};

export type TransitionRequest = {
  actor_id: string;
  context?: ({
  [key: string]: unknown;
} | null);
  event: string;
};

export type UpdateCredentialRequest = {
  description?: (string | null);
  policy?: ({
  [key: string]: unknown;
} | null);
  value?: (string | null);
};

export type UpdateFlowRequest = {
  definition_json?: ({
  [key: string]: unknown;
} | null);
  description?: (string | null);
  is_active?: (boolean | null);
  name?: (string | null);
};

export type UpdateWorkerRequest = {
  adapter_config?: ({
  [key: string]: unknown;
} | null);
  adapter_entrypoint?: (string | null);
  adapter_module?: (string | null);
  adapter_type?: (string | null);
  capability_ids?: (Array<string> | null);
  isolation_mode?: (string | null);
  model_mode?: (string | null);
  model_profile_id?: (string | null);
  sandbox_profile?: (string | null);
  source_repo?: (string | null);
  team_id?: (string | null);
  update_policy?: (("manual" | "auto-patch" | "auto-minor" | "auto-all") | null);
  version?: (string | null);
  version_pin?: (string | null);
  wrapper_config?: ({
  [key: string]: unknown;
} | null);
};

export type ValidationError = {
  ctx?: Record<string, unknown>;
  input?: unknown;
  loc: Array<(string | number)>;
  msg: string;
  type: string;
};

export type WorkerEvaluateRequest = {
  checks?: (Array<string> | null);
  source_repo?: (string | null);
};

export type WorkerRunDispatchRequest = {
  adapter_required_model_capabilities?: Array<string>;
  budget?: {
  [key: string]: number;
};
  budget_usd?: (number | null);
  capability_requirements?: Array<{
  [key: string]: unknown;
}>;
  checkpoint_policy?: {
  [key: string]: unknown;
};
  dispatch_mode?: (("queued" | "inline") | null);
  expected_output_tokens?: number;
  flow_id?: (string | null);
  flow_instance_id?: (string | null);
  flow_node_execution_id?: (number | null);
  idempotency_key: string;
  lease_seconds?: number;
  model_override_approval_id?: (string | null);
  model_override_request_id?: (string | null);
  model_policy_layers?: Array<{
  [key: string]: unknown;
}>;
  permission_requirements?: Array<string>;
  project_id?: (string | null);
  prompt_tokens?: number;
  queue_priority?: number;
  requested_model_profile?: ({
  [key: string]: unknown;
} | null);
  resolved_model_profile?: ({
  [key: string]: unknown;
} | null);
  retry_policy?: {
  [key: string]: unknown;
};
  runtime_extensions?: {
  [key: string]: unknown;
};
  steward_required_model_capabilities?: Array<string>;
  task_input?: {
  [key: string]: unknown;
};
  task_required_model_capabilities?: Array<string>;
  task_type: string;
  timeout_seconds?: (number | null);
  tool_grants?: Array<string>;
  worker_id: string;
  worker_required_model_capabilities?: Array<string>;
  workspace_mode?: string;
};

export type WorkerRunPauseRequest = {
  reason?: string;
  requested_by?: string;
};

export type WorkerRunResumeRequest = {
  checkpoint_id?: (string | null);
  requested_by?: string;
};

export type WorkerStatusTransition = {
  action: string;
  new_role?: (string | null);
  new_status?: (string | null);
};

export type WorkerUpgradeRequest = {
  run_compat_tests?: boolean;
  source_revision?: (string | null);
};

export type OrchestratorApiOperations = {
  "get_agent_profile_agent_profiles__agent_id__get": {
    method: "GET";
    path: "/agent-profiles/{agent_id}";
    parameters: {
      "path:agent_id": string;
    };
    responses: {
      "200": {
  [key: string]: unknown;
};
      "422": HTTPValidationError;
    };
  };
  "estimate_with_agent_profile_agent_profiles__agent_id__estimate_post": {
    method: "POST";
    path: "/agent-profiles/{agent_id}/estimate";
    parameters: {
      "path:agent_id": string;
    };
    requestBody: AgentEstimateRequest;
    responses: {
      "200": {
  [key: string]: unknown;
};
      "422": HTTPValidationError;
    };
  };
  "observe_agent_profile_agent_profiles__agent_id__observations_post": {
    method: "POST";
    path: "/agent-profiles/{agent_id}/observations";
    parameters: {
      "path:agent_id": string;
    };
    requestBody: AgentProfileObservationRequest;
    responses: {
      "201": {
  [key: string]: unknown;
};
      "422": HTTPValidationError;
    };
  };
  "get_artifact_evidence_artifacts__artifact_id__get": {
    method: "GET";
    path: "/artifacts/{artifact_id}";
    parameters: {
      "path:artifact_id": number;
    };
    responses: {
      "200": {
  [key: string]: unknown;
};
      "422": HTTPValidationError;
    };
  };
  "list_capabilities_capabilities_get": {
    method: "GET";
    path: "/capabilities";
    parameters: {
      "query:risk_level"?: (string | null);
    };
    responses: {
      "200": Array<{
  [key: string]: unknown;
}>;
      "422": HTTPValidationError;
    };
  };
  "search_capabilities_capabilities_search_post": {
    method: "POST";
    path: "/capabilities/search";
    requestBody: CapabilitySearchRequest;
    responses: {
      "200": Array<{
  [key: string]: unknown;
}>;
      "422": HTTPValidationError;
    };
  };
  "list_capability_workers_capabilities_workers_get": {
    method: "GET";
    path: "/capabilities/workers";
    parameters: {
      "query:team_id"?: (string | null);
      "query:status"?: (string | null);
    };
    responses: {
      "200": Array<{
  [key: string]: unknown;
}>;
      "422": HTTPValidationError;
    };
  };
  "register_worker_capabilities_workers_post": {
    method: "POST";
    path: "/capabilities/workers";
    requestBody: RegisterWorkerRequest;
    responses: {
      "201": {
  [key: string]: unknown;
};
      "422": HTTPValidationError;
    };
  };
  "import_workers_capabilities_workers_import_post": {
    method: "POST";
    path: "/capabilities/workers/import";
    requestBody: ImportWorkersRequest;
    responses: {
      "200": {
  [key: string]: unknown;
};
      "422": HTTPValidationError;
    };
  };
  "deregister_worker_capabilities_workers__worker_id__delete": {
    method: "DELETE";
    path: "/capabilities/workers/{worker_id}";
    parameters: {
      "path:worker_id": string;
      "query:permanent"?: boolean;
    };
    responses: {
      "200": {
  [key: string]: string;
};
      "422": HTTPValidationError;
    };
  };
  "update_worker_capabilities_workers__worker_id__put": {
    method: "PUT";
    path: "/capabilities/workers/{worker_id}";
    parameters: {
      "path:worker_id": string;
    };
    requestBody: UpdateWorkerRequest;
    responses: {
      "200": {
  [key: string]: unknown;
};
      "422": HTTPValidationError;
    };
  };
  "evaluate_worker_capabilities_workers__worker_id__evaluate_post": {
    method: "POST";
    path: "/capabilities/workers/{worker_id}/evaluate";
    parameters: {
      "path:worker_id": string;
    };
    requestBody: WorkerEvaluateRequest;
    responses: {
      "200": {
  [key: string]: unknown;
};
      "422": HTTPValidationError;
    };
  };
  "get_worker_evaluations_capabilities_workers__worker_id__evaluations_get": {
    method: "GET";
    path: "/capabilities/workers/{worker_id}/evaluations";
    parameters: {
      "path:worker_id": string;
      "query:limit"?: number;
    };
    responses: {
      "200": Array<{
  [key: string]: unknown;
}>;
      "422": HTTPValidationError;
    };
  };
  "get_worker_health_capabilities_workers__worker_id__health_get": {
    method: "GET";
    path: "/capabilities/workers/{worker_id}/health";
    parameters: {
      "path:worker_id": string;
    };
    responses: {
      "200": {
  [key: string]: unknown;
};
      "422": HTTPValidationError;
    };
  };
  "transition_worker_status_capabilities_workers__worker_id__status_patch": {
    method: "PATCH";
    path: "/capabilities/workers/{worker_id}/status";
    parameters: {
      "path:worker_id": string;
    };
    requestBody: WorkerStatusTransition;
    responses: {
      "200": {
  [key: string]: unknown;
};
      "422": HTTPValidationError;
    };
  };
  "get_worker_steward_capabilities_workers__worker_id__steward_get": {
    method: "GET";
    path: "/capabilities/workers/{worker_id}/steward";
    parameters: {
      "path:worker_id": string;
    };
    responses: {
      "200": {
  [key: string]: unknown;
};
      "422": HTTPValidationError;
    };
  };
  "create_worker_steward_capabilities_workers__worker_id__steward_post": {
    method: "POST";
    path: "/capabilities/workers/{worker_id}/steward";
    parameters: {
      "path:worker_id": string;
    };
    requestBody: StewardCreateRequest;
    responses: {
      "201": {
  [key: string]: unknown;
};
      "422": HTTPValidationError;
    };
  };
  "list_steward_candidates_capabilities_workers__worker_id__steward_candidates_get": {
    method: "GET";
    path: "/capabilities/workers/{worker_id}/steward/candidates";
    parameters: {
      "path:worker_id": string;
    };
    responses: {
      "200": Array<{
  [key: string]: unknown;
}>;
      "422": HTTPValidationError;
    };
  };
  "generate_steward_candidate_capabilities_workers__worker_id__steward_candidates_post": {
    method: "POST";
    path: "/capabilities/workers/{worker_id}/steward/candidates";
    parameters: {
      "path:worker_id": string;
    };
    requestBody: CandidateGenerationRequest;
    responses: {
      "201": {
  [key: string]: unknown;
};
      "422": HTTPValidationError;
    };
  };
  "approve_steward_candidate_capabilities_workers__worker_id__steward_candidates__candidate_id__approve_post": {
    method: "POST";
    path: "/capabilities/workers/{worker_id}/steward/candidates/{candidate_id}/approve";
    parameters: {
      "path:worker_id": string;
      "path:candidate_id": string;
    };
    requestBody: CandidateApprovalRequest;
    responses: {
      "200": {
  [key: string]: unknown;
};
      "422": HTTPValidationError;
    };
  };
  "certify_steward_candidate_capabilities_workers__worker_id__steward_candidates__candidate_id__certify_post": {
    method: "POST";
    path: "/capabilities/workers/{worker_id}/steward/candidates/{candidate_id}/certify";
    parameters: {
      "path:worker_id": string;
      "path:candidate_id": string;
    };
    requestBody: CandidateCertificationRequest;
    responses: {
      "200": {
  [key: string]: unknown;
};
      "422": HTTPValidationError;
    };
  };
  "advance_steward_candidate_stage_capabilities_workers__worker_id__steward_candidates__candidate_id__stage_post": {
    method: "POST";
    path: "/capabilities/workers/{worker_id}/steward/candidates/{candidate_id}/stage";
    parameters: {
      "path:worker_id": string;
      "path:candidate_id": string;
    };
    requestBody: CandidateStageAdvanceRequest;
    responses: {
      "200": {
  [key: string]: unknown;
};
      "422": HTTPValidationError;
    };
  };
  "record_steward_capabilities_capabilities_workers__worker_id__steward_capabilities_post": {
    method: "POST";
    path: "/capabilities/workers/{worker_id}/steward/capabilities";
    parameters: {
      "path:worker_id": string;
    };
    requestBody: {
  [key: string]: unknown;
};
    responses: {
      "201": {
  [key: string]: unknown;
};
      "422": HTTPValidationError;
    };
  };
  "add_steward_documentation_capabilities_workers__worker_id__steward_documentation_post": {
    method: "POST";
    path: "/capabilities/workers/{worker_id}/steward/documentation";
    parameters: {
      "path:worker_id": string;
    };
    requestBody: DocumentationSnapshotRequest;
    responses: {
      "201": {
  [key: string]: unknown;
};
      "422": HTTPValidationError;
    };
  };
  "run_worker_steward_monitor_capabilities_workers__worker_id__steward_monitor_post": {
    method: "POST";
    path: "/capabilities/workers/{worker_id}/steward/monitor";
    parameters: {
      "path:worker_id": string;
    };
    responses: {
      "200": Array<{
  [key: string]: unknown;
}>;
      "422": HTTPValidationError;
    };
  };
  "list_worker_steward_monitoring_capabilities_workers__worker_id__steward_monitoring_get": {
    method: "GET";
    path: "/capabilities/workers/{worker_id}/steward/monitoring";
    parameters: {
      "path:worker_id": string;
    };
    responses: {
      "200": Array<{
  [key: string]: unknown;
}>;
      "422": HTTPValidationError;
    };
  };
  "start_steward_rollout_capabilities_workers__worker_id__steward_rollouts_post": {
    method: "POST";
    path: "/capabilities/workers/{worker_id}/steward/rollouts";
    parameters: {
      "path:worker_id": string;
    };
    requestBody: RolloutStartRequest;
    responses: {
      "201": {
  [key: string]: unknown;
};
      "422": HTTPValidationError;
    };
  };
  "advance_steward_rollout_capabilities_workers__worker_id__steward_rollouts__rollout_id__advance_post": {
    method: "POST";
    path: "/capabilities/workers/{worker_id}/steward/rollouts/{rollout_id}/advance";
    parameters: {
      "path:worker_id": string;
      "path:rollout_id": string;
    };
    requestBody: RolloutAdvanceRequest;
    responses: {
      "200": {
  [key: string]: unknown;
};
      "422": HTTPValidationError;
    };
  };
  "rollback_steward_rollout_capabilities_workers__worker_id__steward_rollouts__rollout_id__rollback_post": {
    method: "POST";
    path: "/capabilities/workers/{worker_id}/steward/rollouts/{rollout_id}/rollback";
    parameters: {
      "path:worker_id": string;
      "path:rollout_id": string;
    };
    requestBody: RollbackRequest;
    responses: {
      "200": {
  [key: string]: unknown;
};
      "422": HTTPValidationError;
    };
  };
  "upgrade_worker_capabilities_workers__worker_id__upgrade_post": {
    method: "POST";
    path: "/capabilities/workers/{worker_id}/upgrade";
    parameters: {
      "path:worker_id": string;
    };
    requestBody: WorkerUpgradeRequest;
    responses: {
      "200": {
  [key: string]: unknown;
};
      "422": HTTPValidationError;
    };
  };
  "get_worker_upstream_capabilities_workers__worker_id__upstream_get": {
    method: "GET";
    path: "/capabilities/workers/{worker_id}/upstream";
    parameters: {
      "path:worker_id": string;
    };
    responses: {
      "200": {
  [key: string]: unknown;
};
      "422": HTTPValidationError;
    };
  };
  "operator_send_to_ceo_ceo_message_post": {
    method: "POST";
    path: "/ceo/message";
    parameters: {
      "header:x-api-key"?: (string | null);
      "header:authorization"?: (string | null);
    };
    requestBody: OperatorToCeoRequest;
    responses: {
      "200": {
  [key: string]: unknown;
};
      "422": HTTPValidationError;
    };
  };
  "request_privileged_action_ceo_privileged_action_post": {
    method: "POST";
    path: "/ceo/privileged-action";
    requestBody: PrivilegedActionRequest;
    responses: {
      "200": {
  [key: string]: unknown;
};
      "422": HTTPValidationError;
    };
  };
  "approve_privileged_action_ceo_privileged_action__record_id__approve_post": {
    method: "POST";
    path: "/ceo/privileged-action/{record_id}/approve";
    parameters: {
      "path:record_id": string;
    };
    requestBody: PrivilegedApprovalRequest;
    responses: {
      "200": {
  [key: string]: unknown;
};
      "422": HTTPValidationError;
    };
  };
  "privileged_actions_audit_ceo_privileged_actions_audit_get": {
    method: "GET";
    path: "/ceo/privileged-actions/audit";
    parameters: {
      "query:limit"?: number;
    };
    responses: {
      "200": Array<{
  [key: string]: unknown;
}>;
      "422": HTTPValidationError;
    };
  };
  "list_pending_privileged_actions_ceo_privileged_actions_pending_get": {
    method: "GET";
    path: "/ceo/privileged-actions/pending";
    responses: {
      "200": Array<{
  [key: string]: unknown;
}>;
    };
  };
  "list_companies_companies_get": {
    method: "GET";
    path: "/companies";
    responses: {
      "200": Array<{
  [key: string]: unknown;
}>;
    };
  };
  "create_company_companies_post": {
    method: "POST";
    path: "/companies";
    requestBody: CompanyCreateRequest;
    responses: {
      "201": {
  [key: string]: unknown;
};
      "422": HTTPValidationError;
    };
  };
  "get_company_companies__company_id__get": {
    method: "GET";
    path: "/companies/{company_id}";
    parameters: {
      "path:company_id": string;
    };
    responses: {
      "200": {
  [key: string]: unknown;
};
      "422": HTTPValidationError;
    };
  };
  "list_company_assignments_companies__company_id__assignments_get": {
    method: "GET";
    path: "/companies/{company_id}/assignments";
    parameters: {
      "path:company_id": string;
    };
    responses: {
      "200": Array<{
  [key: string]: unknown;
}>;
      "422": HTTPValidationError;
    };
  };
  "list_company_budget_reservations_companies__company_id__budget_reservations_get": {
    method: "GET";
    path: "/companies/{company_id}/budget-reservations";
    parameters: {
      "path:company_id": string;
      "query:run_id"?: (string | null);
      "query:limit"?: number;
    };
    responses: {
      "200": Array<{
  [key: string]: unknown;
}>;
      "422": HTTPValidationError;
    };
  };
  "list_company_budgets_companies__company_id__budgets_get": {
    method: "GET";
    path: "/companies/{company_id}/budgets";
    parameters: {
      "path:company_id": string;
    };
    responses: {
      "200": Array<{
  [key: string]: unknown;
}>;
      "422": HTTPValidationError;
    };
  };
  "get_company_budget_companies__company_id__budgets__budget_key__get": {
    method: "GET";
    path: "/companies/{company_id}/budgets/{budget_key}";
    parameters: {
      "path:company_id": string;
      "path:budget_key": string;
    };
    responses: {
      "200": {
  [key: string]: unknown;
};
      "422": HTTPValidationError;
    };
  };
  "list_company_departments_companies__company_id__departments_get": {
    method: "GET";
    path: "/companies/{company_id}/departments";
    parameters: {
      "path:company_id": string;
    };
    responses: {
      "200": Array<{
  [key: string]: unknown;
}>;
      "422": HTTPValidationError;
    };
  };
  "set_company_evidence_policy_companies__company_id__evidence_policy_put": {
    method: "PUT";
    path: "/companies/{company_id}/evidence-policy";
    parameters: {
      "path:company_id": string;
    };
    requestBody: ProjectEvidencePolicyRequest;
    responses: {
      "200": {
  [key: string]: unknown;
};
      "422": HTTPValidationError;
    };
  };
  "apply_company_manifest_companies__company_id__manifest_apply_post": {
    method: "POST";
    path: "/companies/{company_id}/manifest/apply";
    parameters: {
      "path:company_id": string;
    };
    requestBody: CompanyManifestRequest;
    responses: {
      "200": {
  [key: string]: unknown;
};
      "422": HTTPValidationError;
    };
  };
  "company_manifest_history_companies__company_id__manifest_history_get": {
    method: "GET";
    path: "/companies/{company_id}/manifest/history";
    parameters: {
      "path:company_id": string;
      "query:limit"?: number;
    };
    responses: {
      "200": Array<{
  [key: string]: unknown;
}>;
      "422": HTTPValidationError;
    };
  };
  "rollback_company_manifest_companies__company_id__manifest_rollback_post": {
    method: "POST";
    path: "/companies/{company_id}/manifest/rollback";
    parameters: {
      "path:company_id": string;
    };
    requestBody: CompanyManifestRollbackRequest;
    responses: {
      "200": {
  [key: string]: unknown;
};
      "422": HTTPValidationError;
    };
  };
  "validate_company_manifest_companies__company_id__manifest_validate_post": {
    method: "POST";
    path: "/companies/{company_id}/manifest/validate";
    parameters: {
      "path:company_id": string;
    };
    requestBody: CompanyManifestRequest;
    responses: {
      "200": {
  [key: string]: unknown;
};
      "422": HTTPValidationError;
    };
  };
  "list_credentials_credentials_get": {
    method: "GET";
    path: "/credentials";
    responses: {
      "200": Array<{
  [key: string]: unknown;
}>;
    };
  };
  "create_credential_credentials_post": {
    method: "POST";
    path: "/credentials";
    requestBody: CreateCredentialRequest;
    responses: {
      "201": {
  [key: string]: unknown;
};
      "422": HTTPValidationError;
    };
  };
  "full_audit_log_credentials_audit_get": {
    method: "GET";
    path: "/credentials-audit";
    parameters: {
      "query:limit"?: number;
    };
    responses: {
      "200": Array<{
  [key: string]: unknown;
}>;
      "422": HTTPValidationError;
    };
  };
  "decide_credential_approval_credentials_approval_requests__approval_id__decision_post": {
    method: "POST";
    path: "/credentials/approval-requests/{approval_id}/decision";
    parameters: {
      "path:approval_id": string;
    };
    requestBody: CredentialApprovalDecisionRequest;
    responses: {
      "200": {
  [key: string]: unknown;
};
      "422": HTTPValidationError;
    };
  };
  "delete_credential_credentials__name__delete": {
    method: "DELETE";
    path: "/credentials/{name}";
    parameters: {
      "path:name": string;
    };
    responses: {
      "204": unknown;
      "422": HTTPValidationError;
    };
  };
  "get_credential_credentials__name__get": {
    method: "GET";
    path: "/credentials/{name}";
    parameters: {
      "path:name": string;
    };
    responses: {
      "200": {
  [key: string]: unknown;
};
      "422": HTTPValidationError;
    };
  };
  "update_credential_credentials__name__patch": {
    method: "PATCH";
    path: "/credentials/{name}";
    parameters: {
      "path:name": string;
    };
    requestBody: UpdateCredentialRequest;
    responses: {
      "200": {
  [key: string]: unknown;
};
      "422": HTTPValidationError;
    };
  };
  "request_credential_approval_credentials__name__approval_requests_post": {
    method: "POST";
    path: "/credentials/{name}/approval-requests";
    parameters: {
      "path:name": string;
    };
    requestBody: CredentialApprovalRequest;
    responses: {
      "201": {
  [key: string]: unknown;
};
      "422": HTTPValidationError;
    };
  };
  "credential_audit_log_credentials__name__audit_get": {
    method: "GET";
    path: "/credentials/{name}/audit";
    parameters: {
      "path:name": string;
      "query:limit"?: number;
    };
    responses: {
      "200": Array<{
  [key: string]: unknown;
}>;
      "422": HTTPValidationError;
    };
  };
  "resolve_credential_credentials__name__resolve_post": {
    method: "POST";
    path: "/credentials/{name}/resolve";
    parameters: {
      "path:name": string;
    };
    requestBody: ResolveCredentialRequest;
    responses: {
      "200": {
  [key: string]: unknown;
};
      "422": HTTPValidationError;
    };
  };
  "dashboard_access_dashboard_access_get": {
    method: "GET";
    path: "/dashboard/access";
    responses: {
      "200": {
  [key: string]: unknown;
};
    };
  };
  "dashboard_section_access_dashboard_sections__section__get": {
    method: "GET";
    path: "/dashboard/sections/{section}";
    parameters: {
      "path:section": string;
    };
    responses: {
      "200": {
  [key: string]: unknown;
};
      "422": HTTPValidationError;
    };
  };
  "update_dashboard_section_acl_dashboard_sections__section__acl_put": {
    method: "PUT";
    path: "/dashboard/sections/{section}/acl";
    parameters: {
      "path:section": string;
    };
    requestBody: DashboardSectionACLRequest;
    responses: {
      "200": {
  [key: string]: unknown;
};
      "422": HTTPValidationError;
    };
  };
  "list_dead_letters_dead_letters_get": {
    method: "GET";
    path: "/dead-letters";
    parameters: {
      "query:project_id"?: (string | null);
      "query:recipient_team"?: (string | null);
      "query:limit"?: number;
    };
    responses: {
      "200": Array<{
  [key: string]: unknown;
}>;
      "422": HTTPValidationError;
    };
  };
  "get_dead_letter_dead_letters__letter_id__get": {
    method: "GET";
    path: "/dead-letters/{letter_id}";
    parameters: {
      "path:letter_id": number;
    };
    responses: {
      "200": {
  [key: string]: unknown;
};
      "422": HTTPValidationError;
    };
  };
  "replay_dead_letter_dead_letters__letter_id__replay_post": {
    method: "POST";
    path: "/dead-letters/{letter_id}/replay";
    parameters: {
      "path:letter_id": number;
    };
    responses: {
      "200": {
  [key: string]: unknown;
};
      "422": HTTPValidationError;
    };
  };
  "evaluate_firecracker_integration_evaluations_firecracker_get": {
    method: "GET";
    path: "/evaluations/firecracker";
    responses: {
      "200": {
  [key: string]: unknown;
};
    };
  };
  "evaluate_garage_integration_evaluations_garage_get": {
    method: "GET";
    path: "/evaluations/garage";
    responses: {
      "200": {
  [key: string]: unknown;
};
    };
  };
  "evaluate_temporal_integration_evaluations_temporal_get": {
    method: "GET";
    path: "/evaluations/temporal";
    responses: {
      "200": {
  [key: string]: unknown;
};
    };
  };
  "evaluate_vault_integration_evaluations_vault_get": {
    method: "GET";
    path: "/evaluations/vault";
    responses: {
      "200": {
  [key: string]: unknown;
};
    };
  };
  "evaluate_zitadel_integration_evaluations_zitadel_get": {
    method: "GET";
    path: "/evaluations/zitadel";
    responses: {
      "200": {
  [key: string]: unknown;
};
    };
  };
  "list_evidence_policies_evidence_policies_get": {
    method: "GET";
    path: "/evidence-policies";
    responses: {
      "200": {
  [key: string]: unknown;
};
    };
  };
  "executive_ceo_privileged_action_executive_actions_ceo_privileged_actions_post": {
    method: "POST";
    path: "/executive/actions/ceo/privileged-actions";
    requestBody: ExecutiveCEOPrivilegedActionRequest;
    responses: {
      "200": {
  [key: string]: unknown;
};
      "422": HTTPValidationError;
    };
  };
  "executive_cfo_model_override_executive_actions_cfo_model_overrides_post": {
    method: "POST";
    path: "/executive/actions/cfo/model-overrides";
    requestBody: ExecutiveCFOModelOverrideRequest;
    responses: {
      "201": {
  [key: string]: unknown;
};
      "422": HTTPValidationError;
    };
  };
  "executive_cto_worker_run_executive_actions_cto_worker_runs_post": {
    method: "POST";
    path: "/executive/actions/cto/worker-runs";
    requestBody: ExecutiveCTOWorkerRunRequest;
    responses: {
      "202": {
  [key: string]: unknown;
};
      "422": HTTPValidationError;
    };
  };
  "executive_reconciliation_executive_reconciliation_get": {
    method: "GET";
    path: "/executive/reconciliation";
    parameters: {
      "query:company_id"?: (string | null);
    };
    responses: {
      "200": {
  [key: string]: unknown;
};
      "422": HTTPValidationError;
    };
  };
  "executive_role_view_executive_views__role__get": {
    method: "GET";
    path: "/executive/views/{role}";
    parameters: {
      "path:role": ("cfo" | "cto" | "ceo");
      "query:company_id"?: (string | null);
    };
    responses: {
      "200": {
  [key: string]: unknown;
};
      "422": HTTPValidationError;
    };
  };
  "list_flow_templates_flow_templates_get": {
    method: "GET";
    path: "/flow-templates";
    responses: {
      "200": {
  [key: string]: unknown;
};
    };
  };
  "list_flows_flows_get": {
    method: "GET";
    path: "/flows";
    parameters: {
      "query:is_active"?: (boolean | null);
      "query:limit"?: number;
      "query:offset"?: number;
    };
    responses: {
      "200": Array<{
  [key: string]: unknown;
}>;
      "422": HTTPValidationError;
    };
  };
  "create_flow_flows_post": {
    method: "POST";
    path: "/flows";
    requestBody: CreateFlowRequest;
    responses: {
      "201": {
  [key: string]: unknown;
};
      "422": HTTPValidationError;
    };
  };
  "diff_flows_flows_diff_post": {
    method: "POST";
    path: "/flows/diff";
    requestBody: FlowDiffRequest;
    responses: {
      "200": {
  [key: string]: unknown;
};
      "422": HTTPValidationError;
    };
  };
  "dry_run_flow_flows_dry_run_post": {
    method: "POST";
    path: "/flows/dry-run";
    requestBody: FlowDryRunRequest;
    responses: {
      "200": {
  [key: string]: unknown;
};
      "422": HTTPValidationError;
    };
  };
  "create_flow_from_template_flows_from_template_post": {
    method: "POST";
    path: "/flows/from-template";
    requestBody: FlowFromTemplateRequest;
    responses: {
      "201": {
  [key: string]: unknown;
};
      "422": HTTPValidationError;
    };
  };
  "import_flow_flows_import_post": {
    method: "POST";
    path: "/flows/import";
    requestBody: FlowImportRequest;
    responses: {
      "201": {
  [key: string]: unknown;
};
      "422": HTTPValidationError;
    };
  };
  "list_flow_instances_early_flows_instances_get": {
    method: "GET";
    path: "/flows/instances";
    parameters: {
      "query:flow_id"?: (string | null);
      "query:project_id"?: (string | null);
      "query:status"?: (string | null);
      "query:limit"?: number;
      "query:offset"?: number;
    };
    responses: {
      "200": Array<{
  [key: string]: unknown;
}>;
      "422": HTTPValidationError;
    };
  };
  "create_flow_instance_flows_instances_post": {
    method: "POST";
    path: "/flows/instances";
    requestBody: CreateFlowInstanceRequest;
    responses: {
      "200": {
  [key: string]: unknown;
};
      "422": HTTPValidationError;
    };
  };
  "list_active_flow_instances_early_flows_instances_active_get": {
    method: "GET";
    path: "/flows/instances/active";
    responses: {
      "200": Array<{
  [key: string]: unknown;
}>;
    };
  };
  "get_flow_instance_flows_instances__instance_id__get": {
    method: "GET";
    path: "/flows/instances/{instance_id}";
    parameters: {
      "path:instance_id": string;
    };
    responses: {
      "200": {
  [key: string]: unknown;
};
      "422": HTTPValidationError;
    };
  };
  "flow_instance_action_flows_instances__instance_id__action_post": {
    method: "POST";
    path: "/flows/instances/{instance_id}/action";
    parameters: {
      "path:instance_id": string;
    };
    requestBody: FlowInstanceActionRequest;
    responses: {
      "200": {
  [key: string]: unknown;
};
      "422": HTTPValidationError;
    };
  };
  "update_flow_instance_context_flows_instances__instance_id__context_post": {
    method: "POST";
    path: "/flows/instances/{instance_id}/context";
    parameters: {
      "path:instance_id": string;
    };
    requestBody: {
  [key: string]: unknown;
};
    responses: {
      "200": {
  [key: string]: unknown;
};
      "422": HTTPValidationError;
    };
  };
  "escalate_flow_instance_flows_instances__instance_id__escalate_post": {
    method: "POST";
    path: "/flows/instances/{instance_id}/escalate";
    parameters: {
      "path:instance_id": string;
    };
    requestBody: {
  [key: string]: unknown;
};
    responses: {
      "200": {
  [key: string]: unknown;
};
      "422": HTTPValidationError;
    };
  };
  "list_flow_node_executions_flows_instances__instance_id__executions_get": {
    method: "GET";
    path: "/flows/instances/{instance_id}/executions";
    parameters: {
      "path:instance_id": string;
      "query:limit"?: number;
      "query:offset"?: number;
    };
    responses: {
      "200": Array<{
  [key: string]: unknown;
}>;
      "422": HTTPValidationError;
    };
  };
  "migrate_flow_instance_flows_instances__instance_id__migrate_post": {
    method: "POST";
    path: "/flows/instances/{instance_id}/migrate";
    parameters: {
      "path:instance_id": string;
    };
    requestBody: FlowMigrationRequest;
    responses: {
      "200": {
  [key: string]: unknown;
};
      "422": HTTPValidationError;
    };
  };
  "flow_node_action_flows_instances__instance_id__node_action_post": {
    method: "POST";
    path: "/flows/instances/{instance_id}/node-action";
    parameters: {
      "path:instance_id": string;
    };
    requestBody: FlowNodeActionRequest;
    responses: {
      "200": {
  [key: string]: unknown;
};
      "422": HTTPValidationError;
    };
  };
  "override_flow_instance_flows_instances__instance_id__override_post": {
    method: "POST";
    path: "/flows/instances/{instance_id}/override";
    parameters: {
      "path:instance_id": string;
    };
    requestBody: FlowOverrideRequest;
    responses: {
      "200": {
  [key: string]: unknown;
};
      "422": HTTPValidationError;
    };
  };
  "retry_flow_instance_flows_instances__instance_id__retry_post": {
    method: "POST";
    path: "/flows/instances/{instance_id}/retry";
    parameters: {
      "path:instance_id": string;
    };
    responses: {
      "200": {
  [key: string]: unknown;
};
      "422": HTTPValidationError;
    };
  };
  "switch_flow_instance_flows_instances__instance_id__switch_post": {
    method: "POST";
    path: "/flows/instances/{instance_id}/switch";
    parameters: {
      "path:instance_id": string;
    };
    requestBody: {
  [key: string]: unknown;
};
    responses: {
      "200": {
  [key: string]: unknown;
};
      "422": HTTPValidationError;
    };
  };
  "list_flow_node_schemas_flows_node_schemas_get": {
    method: "GET";
    path: "/flows/node-schemas";
    responses: {
      "200": {
  [key: string]: unknown;
};
    };
  };
  "delete_flow_flows__flow_id__delete": {
    method: "DELETE";
    path: "/flows/{flow_id}";
    parameters: {
      "path:flow_id": string;
    };
    responses: {
      "200": {
  [key: string]: string;
};
      "422": HTTPValidationError;
    };
  };
  "get_flow_flows__flow_id__get": {
    method: "GET";
    path: "/flows/{flow_id}";
    parameters: {
      "path:flow_id": string;
    };
    responses: {
      "200": {
  [key: string]: unknown;
};
      "422": HTTPValidationError;
    };
  };
  "update_flow_flows__flow_id__put": {
    method: "PUT";
    path: "/flows/{flow_id}";
    parameters: {
      "path:flow_id": string;
    };
    requestBody: UpdateFlowRequest;
    responses: {
      "200": {
  [key: string]: unknown;
};
      "422": HTTPValidationError;
    };
  };
  "deprecate_flow_flows__flow_id__deprecate_post": {
    method: "POST";
    path: "/flows/{flow_id}/deprecate";
    parameters: {
      "path:flow_id": string;
    };
    responses: {
      "200": {
  [key: string]: unknown;
};
      "422": HTTPValidationError;
    };
  };
  "export_flow_flows__flow_id__export_get": {
    method: "GET";
    path: "/flows/{flow_id}/export";
    parameters: {
      "path:flow_id": string;
    };
    responses: {
      "200": {
  [key: string]: unknown;
};
      "422": HTTPValidationError;
    };
  };
  "migrate_legacy_flow_tasks_flows__flow_id__migrate_legacy_tasks_post": {
    method: "POST";
    path: "/flows/{flow_id}/migrate-legacy-tasks";
    parameters: {
      "path:flow_id": string;
    };
    requestBody: FlowLegacyTaskMigrationRequest;
    responses: {
      "200": {
  [key: string]: unknown;
};
      "422": HTTPValidationError;
    };
  };
  "publish_flow_flows__flow_id__publish_post": {
    method: "POST";
    path: "/flows/{flow_id}/publish";
    parameters: {
      "path:flow_id": string;
    };
    responses: {
      "200": {
  [key: string]: unknown;
};
      "422": HTTPValidationError;
    };
  };
  "health_health_get": {
    method: "GET";
    path: "/health";
    responses: {
      "200": {
  [key: string]: string;
};
    };
  };
  "identity_dashboard_action_identity_dashboard_action_post": {
    method: "POST";
    path: "/identity/dashboard/action";
    parameters: {
      "header:x-api-key"?: (string | null);
      "header:authorization"?: (string | null);
    };
    requestBody: IdentityDashboardActionRequest;
    responses: {
      "200": {
  [key: string]: unknown;
};
      "422": HTTPValidationError;
    };
  };
  "identity_dashboard_resource_identity_dashboard__resource__get": {
    method: "GET";
    path: "/identity/dashboard/{resource}";
    parameters: {
      "path:resource": string;
      "header:x-api-key"?: (string | null);
      "header:authorization"?: (string | null);
    };
    responses: {
      "200": {
  [key: string]: unknown;
};
      "422": HTTPValidationError;
    };
  };
  "list_integration_conflicts_integrations_conflicts_get": {
    method: "GET";
    path: "/integrations/conflicts";
    parameters: {
      "query:connection_id"?: (string | null);
      "query:limit"?: number;
    };
    responses: {
      "200": Array<{
  [key: string]: unknown;
}>;
      "422": HTTPValidationError;
    };
  };
  "resolve_integration_conflict_integrations_conflicts__conflict_id__resolve_post": {
    method: "POST";
    path: "/integrations/conflicts/{conflict_id}/resolve";
    parameters: {
      "path:conflict_id": string;
    };
    requestBody: PMConflictResolutionRequest;
    responses: {
      "200": {
  [key: string]: unknown;
};
      "422": HTTPValidationError;
    };
  };
  "list_integration_connections_integrations_connections_get": {
    method: "GET";
    path: "/integrations/connections";
    parameters: {
      "query:status"?: (string | null);
    };
    responses: {
      "200": Array<{
  [key: string]: unknown;
}>;
      "422": HTTPValidationError;
    };
  };
  "create_integration_connection_integrations_connections_post": {
    method: "POST";
    path: "/integrations/connections";
    requestBody: PMConnectionCreateRequest;
    responses: {
      "201": {
  [key: string]: unknown;
};
      "422": HTTPValidationError;
    };
  };
  "apply_integration_bootstrap_integrations_connections__connection_id__apply_post": {
    method: "POST";
    path: "/integrations/connections/{connection_id}/apply";
    parameters: {
      "path:connection_id": string;
    };
    requestBody: PMApplyRequest;
    responses: {
      "200": {
  [key: string]: unknown;
};
      "422": HTTPValidationError;
    };
  };
  "integration_connection_capabilities_integrations_connections__connection_id__capabilities_get": {
    method: "GET";
    path: "/integrations/connections/{connection_id}/capabilities";
    parameters: {
      "path:connection_id": string;
    };
    responses: {
      "200": {
  [key: string]: unknown;
};
      "422": HTTPValidationError;
    };
  };
  "doctor_integration_connection_integrations_connections__connection_id__doctor_get": {
    method: "GET";
    path: "/integrations/connections/{connection_id}/doctor";
    parameters: {
      "path:connection_id": string;
    };
    responses: {
      "200": {
  [key: string]: unknown;
};
      "422": HTTPValidationError;
    };
  };
  "create_external_actor_mapping_integrations_connections__connection_id__external_actor_mappings_post": {
    method: "POST";
    path: "/integrations/connections/{connection_id}/external-actor-mappings";
    parameters: {
      "path:connection_id": string;
    };
    requestBody: PMExternalActorMappingCreateRequest;
    responses: {
      "201": {
  [key: string]: unknown;
};
      "422": HTTPValidationError;
    };
  };
  "revoke_external_actor_mapping_integrations_connections__connection_id__external_actor_mappings__mapping_id__revoke_post": {
    method: "POST";
    path: "/integrations/connections/{connection_id}/external-actor-mappings/{mapping_id}/revoke";
    parameters: {
      "path:connection_id": string;
      "path:mapping_id": string;
      "query:reason": string;
    };
    responses: {
      "200": {
  [key: string]: unknown;
};
      "422": HTTPValidationError;
    };
  };
  "integration_connection_health_integrations_connections__connection_id__health_get": {
    method: "GET";
    path: "/integrations/connections/{connection_id}/health";
    parameters: {
      "path:connection_id": string;
    };
    responses: {
      "200": {
  [key: string]: unknown;
};
      "422": HTTPValidationError;
    };
  };
  "create_inbound_priority_canary_plan_integrations_connections__connection_id__inbound_canaries_post": {
    method: "POST";
    path: "/integrations/connections/{connection_id}/inbound-canaries";
    parameters: {
      "path:connection_id": string;
    };
    requestBody: PMInboundCanaryPlanCreateRequest;
    responses: {
      "201": {
  [key: string]: unknown;
};
      "422": HTTPValidationError;
    };
  };
  "plan_integration_bootstrap_integrations_connections__connection_id__plan_post": {
    method: "POST";
    path: "/integrations/connections/{connection_id}/plan";
    parameters: {
      "path:connection_id": string;
    };
    requestBody: PMPlanRequest;
    responses: {
      "200": {
  [key: string]: unknown;
};
      "422": HTTPValidationError;
    };
  };
  "reconcile_integration_connection_integrations_connections__connection_id__reconcile_post": {
    method: "POST";
    path: "/integrations/connections/{connection_id}/reconcile";
    parameters: {
      "path:connection_id": string;
    };
    requestBody: PMReconcileRequest;
    responses: {
      "200": {
  [key: string]: unknown;
};
      "422": HTTPValidationError;
    };
  };
  "create_source_control_branch_integrations_connections__connection_id__source_control_branches_post": {
    method: "POST";
    path: "/integrations/connections/{connection_id}/source-control/branches";
    parameters: {
      "path:connection_id": string;
    };
    requestBody: SCMActionRequest;
    responses: {
      "201": {
  [key: string]: unknown;
};
      "422": HTTPValidationError;
    };
  };
  "publish_source_control_check_integrations_connections__connection_id__source_control_checks_post": {
    method: "POST";
    path: "/integrations/connections/{connection_id}/source-control/checks";
    parameters: {
      "path:connection_id": string;
    };
    requestBody: SCMActionRequest;
    responses: {
      "201": {
  [key: string]: unknown;
};
      "422": HTTPValidationError;
    };
  };
  "capture_source_control_commit_integrations_connections__connection_id__source_control_commits_post": {
    method: "POST";
    path: "/integrations/connections/{connection_id}/source-control/commits";
    parameters: {
      "path:connection_id": string;
    };
    requestBody: SCMActionRequest;
    responses: {
      "201": {
  [key: string]: unknown;
};
      "422": HTTPValidationError;
    };
  };
  "list_source_control_evidence_integrations_connections__connection_id__source_control_evidence_get": {
    method: "GET";
    path: "/integrations/connections/{connection_id}/source-control/evidence";
    parameters: {
      "path:connection_id": string;
      "query:evidence_type"?: (string | null);
      "query:limit"?: number;
    };
    responses: {
      "200": Array<{
  [key: string]: unknown;
}>;
      "422": HTTPValidationError;
    };
  };
  "discover_source_control_installation_integrations_connections__connection_id__source_control_installation_post": {
    method: "POST";
    path: "/integrations/connections/{connection_id}/source-control/installation";
    parameters: {
      "path:connection_id": string;
    };
    responses: {
      "200": {
  [key: string]: unknown;
};
      "422": HTTPValidationError;
    };
  };
  "project_source_control_pull_request_integrations_connections__connection_id__source_control_pull_requests_post": {
    method: "POST";
    path: "/integrations/connections/{connection_id}/source-control/pull-requests";
    parameters: {
      "path:connection_id": string;
    };
    requestBody: SCMActionRequest;
    responses: {
      "201": {
  [key: string]: unknown;
};
      "422": HTTPValidationError;
    };
  };
  "publish_source_control_review_comment_integrations_connections__connection_id__source_control_review_comments_post": {
    method: "POST";
    path: "/integrations/connections/{connection_id}/source-control/review-comments";
    parameters: {
      "path:connection_id": string;
    };
    requestBody: SCMActionRequest;
    responses: {
      "201": {
  [key: string]: unknown;
};
      "422": HTTPValidationError;
    };
  };
  "mint_source_control_run_credential_integrations_connections__connection_id__source_control_run_credentials_post": {
    method: "POST";
    path: "/integrations/connections/{connection_id}/source-control/run-credentials";
    parameters: {
      "path:connection_id": string;
    };
    requestBody: SCMActionRequest;
    responses: {
      "200": {
  [key: string]: unknown;
};
      "422": HTTPValidationError;
    };
  };
  "update_integration_connection_status_integrations_connections__connection_id__status_patch": {
    method: "PATCH";
    path: "/integrations/connections/{connection_id}/status";
    parameters: {
      "path:connection_id": string;
    };
    requestBody: PMConnectionStatusRequest;
    responses: {
      "200": {
  [key: string]: unknown;
};
      "422": HTTPValidationError;
    };
  };
  "list_integration_cutovers_integrations_cutovers_get": {
    method: "GET";
    path: "/integrations/cutovers";
    parameters: {
      "query:project_id"?: (string | null);
      "query:limit"?: number;
    };
    responses: {
      "200": Array<{
  [key: string]: unknown;
}>;
      "422": HTTPValidationError;
    };
  };
  "cutover_integration_binding_integrations_cutovers_post": {
    method: "POST";
    path: "/integrations/cutovers";
    requestBody: PMCutoverRequest;
    responses: {
      "200": {
  [key: string]: unknown;
};
      "422": HTTPValidationError;
    };
  };
  "get_delta_integration_readiness_integrations_delta_readiness_get": {
    method: "GET";
    path: "/integrations/delta-readiness";
    responses: {
      "200": {
  [key: string]: unknown;
};
    };
  };
  "check_docling_certification_integrations_docling_certification_check_post": {
    method: "POST";
    path: "/integrations/docling/certification-check";
    requestBody: DoclingCertificationRequest;
    responses: {
      "200": {
  [key: string]: unknown;
};
      "422": HTTPValidationError;
    };
  };
  "github_repository_metadata_integrations_github_repository_metadata_post": {
    method: "POST";
    path: "/integrations/github/repository-metadata";
    requestBody: GitHubMetadataRequest;
    responses: {
      "200": {
  [key: string]: unknown;
};
      "422": HTTPValidationError;
    };
  };
  "get_inbound_canary_plan_integrations_inbound_canaries__plan_id__get": {
    method: "GET";
    path: "/integrations/inbound-canaries/{plan_id}";
    parameters: {
      "path:plan_id": string;
    };
    responses: {
      "200": {
  [key: string]: unknown;
};
      "422": HTTPValidationError;
    };
  };
  "approve_inbound_canary_plan_integrations_inbound_canaries__plan_id__approve_post": {
    method: "POST";
    path: "/integrations/inbound-canaries/{plan_id}/approve";
    parameters: {
      "path:plan_id": string;
    };
    requestBody: PMInboundCanaryPlanActionRequest;
    responses: {
      "200": {
  [key: string]: unknown;
};
      "422": HTTPValidationError;
    };
  };
  "arm_inbound_canary_plan_integrations_inbound_canaries__plan_id__arm_post": {
    method: "POST";
    path: "/integrations/inbound-canaries/{plan_id}/arm";
    parameters: {
      "path:plan_id": string;
    };
    requestBody: PMInboundCanaryPlanActionRequest;
    responses: {
      "200": {
  [key: string]: unknown;
};
      "422": HTTPValidationError;
    };
  };
  "record_inbound_canary_audit_evidence_integrations_inbound_canaries__plan_id__audit_evidence_post": {
    method: "POST";
    path: "/integrations/inbound-canaries/{plan_id}/audit-evidence";
    parameters: {
      "path:plan_id": string;
    };
    requestBody: PMInboundCanaryPlanActionRequest;
    responses: {
      "200": {
  [key: string]: unknown;
};
      "422": HTTPValidationError;
    };
  };
  "disarm_inbound_canary_plan_integrations_inbound_canaries__plan_id__disarm_post": {
    method: "POST";
    path: "/integrations/inbound-canaries/{plan_id}/disarm";
    parameters: {
      "path:plan_id": string;
    };
    requestBody: PMInboundCanaryPlanActionRequest;
    responses: {
      "200": {
  [key: string]: unknown;
};
      "422": HTTPValidationError;
    };
  };
  "expire_inbound_canary_plan_integrations_inbound_canaries__plan_id__expire_post": {
    method: "POST";
    path: "/integrations/inbound-canaries/{plan_id}/expire";
    parameters: {
      "path:plan_id": string;
    };
    requestBody: PMInboundCanaryPlanActionRequest;
    responses: {
      "200": {
  [key: string]: unknown;
};
      "422": HTTPValidationError;
    };
  };
  "replay_verified_inbound_canary_event_integrations_inbound_canaries__plan_id__replay_verified_event_post": {
    method: "POST";
    path: "/integrations/inbound-canaries/{plan_id}/replay-verified-event";
    parameters: {
      "path:plan_id": string;
    };
    requestBody: PMInboundCanaryReplayRequest;
    responses: {
      "200": {
  [key: string]: unknown;
};
      "422": HTTPValidationError;
    };
  };
  "list_pm_lifecycle_plans_integrations_lifecycle_plans_get": {
    method: "GET";
    path: "/integrations/lifecycle-plans";
    parameters: {
      "query:connection_id"?: (string | null);
      "query:target_id"?: (string | null);
      "query:status"?: (string | null);
      "query:limit"?: number;
    };
    responses: {
      "200": Array<{
  [key: string]: unknown;
}>;
      "422": HTTPValidationError;
    };
  };
  "create_pm_lifecycle_plan_integrations_lifecycle_plans_post": {
    method: "POST";
    path: "/integrations/lifecycle-plans";
    requestBody: PMLifecyclePlanCreateRequest;
    responses: {
      "201": {
  [key: string]: unknown;
};
      "422": HTTPValidationError;
    };
  };
  "get_pm_lifecycle_plan_integrations_lifecycle_plans__plan_id__get": {
    method: "GET";
    path: "/integrations/lifecycle-plans/{plan_id}";
    parameters: {
      "path:plan_id": string;
    };
    responses: {
      "200": {
  [key: string]: unknown;
};
      "422": HTTPValidationError;
    };
  };
  "apply_pm_lifecycle_plan_integrations_lifecycle_plans__plan_id__apply_post": {
    method: "POST";
    path: "/integrations/lifecycle-plans/{plan_id}/apply";
    parameters: {
      "path:plan_id": string;
    };
    requestBody: PMLifecyclePlanApplyRequest;
    responses: {
      "200": {
  [key: string]: unknown;
};
      "422": HTTPValidationError;
    };
  };
  "approve_pm_lifecycle_plan_integrations_lifecycle_plans__plan_id__approve_post": {
    method: "POST";
    path: "/integrations/lifecycle-plans/{plan_id}/approve";
    parameters: {
      "path:plan_id": string;
    };
    requestBody: PMLifecyclePlanApprovalRequest;
    responses: {
      "200": {
  [key: string]: unknown;
};
      "422": HTTPValidationError;
    };
  };
  "get_pm_lifecycle_audit_integrations_lifecycle_plans__plan_id__audit_get": {
    method: "GET";
    path: "/integrations/lifecycle-plans/{plan_id}/audit";
    parameters: {
      "path:plan_id": string;
    };
    responses: {
      "200": Array<{
  [key: string]: unknown;
}>;
      "422": HTTPValidationError;
    };
  };
  "reject_pm_lifecycle_plan_integrations_lifecycle_plans__plan_id__reject_post": {
    method: "POST";
    path: "/integrations/lifecycle-plans/{plan_id}/reject";
    parameters: {
      "path:plan_id": string;
    };
    requestBody: PMLifecyclePlanRejectRequest;
    responses: {
      "200": {
  [key: string]: unknown;
};
      "422": HTTPValidationError;
    };
  };
  "n8n_edge_policy_integrations_n8n_edge_policy_post": {
    method: "POST";
    path: "/integrations/n8n/edge-policy";
    requestBody: N8nEdgePolicyRequest;
    responses: {
      "200": {
  [key: string]: unknown;
};
      "422": HTTPValidationError;
    };
  };
  "list_integration_outbox_integrations_outbox_get": {
    method: "GET";
    path: "/integrations/outbox";
    parameters: {
      "query:connection_id"?: (string | null);
      "query:limit"?: number;
    };
    responses: {
      "200": Array<{
  [key: string]: unknown;
}>;
      "422": HTTPValidationError;
    };
  };
  "drain_integration_outbox_integrations_outbox_drain_post": {
    method: "POST";
    path: "/integrations/outbox/drain";
    parameters: {
      "query:limit"?: number;
    };
    responses: {
      "200": {
  [key: string]: unknown;
};
      "422": HTTPValidationError;
    };
  };
  "dispose_integration_outbox_integrations_outbox__outbox_id__disposition_post": {
    method: "POST";
    path: "/integrations/outbox/{outbox_id}/disposition";
    parameters: {
      "path:outbox_id": string;
    };
    requestBody: PMOutboxDispositionRequest;
    responses: {
      "200": {
  [key: string]: unknown;
};
      "422": HTTPValidationError;
    };
  };
  "list_integration_reconciliation_runs_integrations_reconciliation_runs_get": {
    method: "GET";
    path: "/integrations/reconciliation-runs";
    parameters: {
      "query:connection_id"?: (string | null);
      "query:limit"?: number;
    };
    responses: {
      "200": Array<{
  [key: string]: unknown;
}>;
      "422": HTTPValidationError;
    };
  };
  "rollback_integration_binding_integrations_rollbacks_post": {
    method: "POST";
    path: "/integrations/rollbacks";
    requestBody: PMRollbackRequest;
    responses: {
      "200": {
  [key: string]: unknown;
};
      "422": HTTPValidationError;
    };
  };
  "receive_integration_webhook_integrations_webhooks__connection_id__post": {
    method: "POST";
    path: "/integrations/webhooks/{connection_id}";
    parameters: {
      "path:connection_id": string;
    };
    responses: {
      "202": {
  [key: string]: unknown;
};
      "422": HTTPValidationError;
    };
  };
  "team_runner_storage_internal_team_runners__team_id__storage_post": {
    method: "POST";
    path: "/internal/team-runners/{team_id}/storage";
    parameters: {
      "path:team_id": string;
    };
    requestBody: TeamRunnerStorageRequest;
    responses: {
      "200": unknown;
      "422": HTTPValidationError;
    };
  };
  "prometheus_metrics_metrics_get": {
    method: "GET";
    path: "/metrics";
    responses: {
      "200": unknown;
    };
  };
  "create_model_override_model_overrides_post": {
    method: "POST";
    path: "/model-overrides";
    requestBody: ModelOverrideCreateRequest;
    responses: {
      "201": {
  [key: string]: unknown;
};
      "422": HTTPValidationError;
    };
  };
  "decide_model_override_model_overrides__override_id__decision_post": {
    method: "POST";
    path: "/model-overrides/{override_id}/decision";
    parameters: {
      "path:override_id": string;
    };
    requestBody: ModelOverrideDecisionRequest;
    responses: {
      "200": {
  [key: string]: unknown;
};
      "422": HTTPValidationError;
    };
  };
  "list_model_profiles_model_profiles_get": {
    method: "GET";
    path: "/model-profiles";
    responses: {
      "200": Array<{
  [key: string]: unknown;
}>;
    };
  };
  "create_model_profile_model_profiles_post": {
    method: "POST";
    path: "/model-profiles";
    requestBody: ModelProfileCreateRequest;
    responses: {
      "201": {
  [key: string]: unknown;
};
      "422": HTTPValidationError;
    };
  };
  "model_profile_catalogue_model_profiles_catalogue_get": {
    method: "GET";
    path: "/model-profiles/catalogue";
    responses: {
      "200": {
  [key: string]: unknown;
};
    };
  };
  "preview_model_resolution_model_profiles_resolve_preview_post": {
    method: "POST";
    path: "/model-profiles/resolve-preview";
    requestBody: ModelResolutionPreviewRequest;
    responses: {
      "200": {
  [key: string]: unknown;
};
      "422": HTTPValidationError;
    };
  };
  "add_model_profile_version_model_profiles__profile_id__versions_post": {
    method: "POST";
    path: "/model-profiles/{profile_id}/versions";
    parameters: {
      "path:profile_id": string;
    };
    requestBody: ModelProfileVersionRequest;
    responses: {
      "201": {
  [key: string]: unknown;
};
      "422": HTTPValidationError;
    };
  };
  "get_capacity_forecast_observability_capacity_forecast_get": {
    method: "GET";
    path: "/observability/capacity/forecast";
    parameters: {
      "query:company_id"?: (string | null);
      "query:window_days"?: number;
      "query:forecast_days"?: number;
    };
    responses: {
      "200": CapacityForecast;
      "422": HTTPValidationError;
    };
  };
  "get_trace_incident_observability_incidents__trace_id__get": {
    method: "GET";
    path: "/observability/incidents/{trace_id}";
    parameters: {
      "path:trace_id": string;
      "query:limit"?: number;
    };
    responses: {
      "200": TraceIncident;
      "422": HTTPValidationError;
    };
  };
  "get_trace_retention_plan_observability_retention_plan_get": {
    method: "GET";
    path: "/observability/retention/plan";
    parameters: {
      "query:trace_id"?: (string | null);
      "query:limit"?: number;
    };
    responses: {
      "200": {
  [key: string]: unknown;
};
      "422": HTTPValidationError;
    };
  };
  "get_operational_slo_report_observability_slo_get": {
    method: "GET";
    path: "/observability/slo";
    parameters: {
      "query:company_id"?: (string | null);
      "query:window_days"?: number;
    };
    responses: {
      "200": SLOReport;
      "422": HTTPValidationError;
    };
  };
  "get_trace_evidence_observability_traces__trace_id__get": {
    method: "GET";
    path: "/observability/traces/{trace_id}";
    parameters: {
      "path:trace_id": string;
      "query:limit"?: number;
    };
    responses: {
      "200": TraceEvidence;
      "422": HTTPValidationError;
    };
  };
  "list_projects_projects_get": {
    method: "GET";
    path: "/projects";
    parameters: {
      "query:state"?: (string | null);
      "query:limit"?: number;
      "query:offset"?: number;
    };
    responses: {
      "200": Array<{
  [key: string]: unknown;
}>;
      "422": HTTPValidationError;
    };
  };
  "create_project_projects_post": {
    method: "POST";
    path: "/projects";
    requestBody: CreateProjectRequest;
    responses: {
      "201": {
  [key: string]: unknown;
};
      "422": HTTPValidationError;
    };
  };
  "create_self_improvement_project_projects_self_improvement_post": {
    method: "POST";
    path: "/projects/self-improvement";
    requestBody: ImprovementOpportunity;
    responses: {
      "201": {
  [key: string]: unknown;
};
      "422": HTTPValidationError;
    };
  };
  "delete_project_projects__project_id__delete": {
    method: "DELETE";
    path: "/projects/{project_id}";
    parameters: {
      "path:project_id": string;
    };
    responses: {
      "200": {
  [key: string]: string;
};
      "422": HTTPValidationError;
    };
  };
  "get_project_projects__project_id__get": {
    method: "GET";
    path: "/projects/{project_id}";
    parameters: {
      "path:project_id": string;
    };
    responses: {
      "200": {
  [key: string]: unknown;
};
      "422": HTTPValidationError;
    };
  };
  "allowed_transitions_projects__project_id__allowed_transitions_get": {
    method: "GET";
    path: "/projects/{project_id}/allowed-transitions";
    parameters: {
      "path:project_id": string;
    };
    responses: {
      "200": {
  [key: string]: unknown;
};
      "422": HTTPValidationError;
    };
  };
  "archive_project_projects__project_id__archive_post": {
    method: "POST";
    path: "/projects/{project_id}/archive";
    parameters: {
      "path:project_id": string;
    };
    responses: {
      "200": {
  [key: string]: unknown;
};
      "422": HTTPValidationError;
    };
  };
  "list_project_artifacts_projects__project_id__artifacts_get": {
    method: "GET";
    path: "/projects/{project_id}/artifacts";
    parameters: {
      "path:project_id": string;
      "query:limit"?: number;
    };
    responses: {
      "200": Array<{
  [key: string]: unknown;
}>;
      "422": HTTPValidationError;
    };
  };
  "create_project_artifact_projects__project_id__artifacts_post": {
    method: "POST";
    path: "/projects/{project_id}/artifacts";
    parameters: {
      "path:project_id": string;
    };
    requestBody: CreateArtifactRequest;
    responses: {
      "201": {
  [key: string]: unknown;
};
      "422": HTTPValidationError;
    };
  };
  "delete_project_artifact_projects__project_id__artifacts__artifact_id__delete": {
    method: "DELETE";
    path: "/projects/{project_id}/artifacts/{artifact_id}";
    parameters: {
      "path:project_id": string;
      "path:artifact_id": number;
    };
    responses: {
      "200": {
  [key: string]: string;
};
      "422": HTTPValidationError;
    };
  };
  "get_project_audit_timeline_projects__project_id__audit_timeline_get": {
    method: "GET";
    path: "/projects/{project_id}/audit-timeline";
    parameters: {
      "path:project_id": string;
      "query:limit"?: number;
    };
    responses: {
      "200": Array<{
  [key: string]: unknown;
}>;
      "422": HTTPValidationError;
    };
  };
  "list_project_context_projects__project_id__context_get": {
    method: "GET";
    path: "/projects/{project_id}/context";
    parameters: {
      "path:project_id": string;
      "query:item_type"?: (string | null);
      "query:tags"?: (string | null);
      "query:include_revisions"?: boolean;
    };
    responses: {
      "200": Array<{
  [key: string]: unknown;
}>;
      "422": HTTPValidationError;
    };
  };
  "create_project_context_item_projects__project_id__context_post": {
    method: "POST";
    path: "/projects/{project_id}/context";
    parameters: {
      "path:project_id": string;
    };
    requestBody: CreateContextItemRequest;
    responses: {
      "201": {
  [key: string]: unknown;
};
      "422": HTTPValidationError;
    };
  };
  "create_context_chunk_projects__project_id__context_chunks_post": {
    method: "POST";
    path: "/projects/{project_id}/context/chunks";
    parameters: {
      "path:project_id": string;
    };
    requestBody: CreateContextItemRequest;
    responses: {
      "200": {
  [key: string]: unknown;
};
      "422": HTTPValidationError;
    };
  };
  "hybrid_search_context_projects__project_id__context_hybrid_search_post": {
    method: "POST";
    path: "/projects/{project_id}/context/hybrid-search";
    parameters: {
      "path:project_id": string;
    };
    requestBody: HybridSearchRequest;
    responses: {
      "200": {
  [key: string]: unknown;
};
      "422": HTTPValidationError;
    };
  };
  "search_project_context_projects__project_id__context_search_post": {
    method: "POST";
    path: "/projects/{project_id}/context/search";
    parameters: {
      "path:project_id": string;
    };
    requestBody: SearchContextRequest;
    responses: {
      "200": Array<{
  [key: string]: unknown;
}>;
      "422": HTTPValidationError;
    };
  };
  "delete_project_context_item_projects__project_id__context__item_id__delete": {
    method: "DELETE";
    path: "/projects/{project_id}/context/{item_id}";
    parameters: {
      "path:project_id": string;
      "path:item_id": string;
    };
    responses: {
      "200": {
  [key: string]: unknown;
};
      "422": HTTPValidationError;
    };
  };
  "get_project_context_item_projects__project_id__context__item_id__get": {
    method: "GET";
    path: "/projects/{project_id}/context/{item_id}";
    parameters: {
      "path:project_id": string;
      "path:item_id": string;
    };
    responses: {
      "200": {
  [key: string]: unknown;
};
      "422": HTTPValidationError;
    };
  };
  "submit_decision_projects__project_id__decisions_post": {
    method: "POST";
    path: "/projects/{project_id}/decisions";
    parameters: {
      "path:project_id": string;
    };
    requestBody: DecisionRequest;
    responses: {
      "200": {
  [key: string]: unknown;
};
      "422": HTTPValidationError;
    };
  };
  "list_documents_projects__project_id__documents_get": {
    method: "GET";
    path: "/projects/{project_id}/documents";
    parameters: {
      "path:project_id": string;
      "query:doc_type"?: (string | null);
    };
    responses: {
      "200": Array<{
  [key: string]: unknown;
}>;
      "422": HTTPValidationError;
    };
  };
  "create_project_document_projects__project_id__documents_post": {
    method: "POST";
    path: "/projects/{project_id}/documents";
    parameters: {
      "path:project_id": string;
    };
    requestBody: CreateDocumentRequest;
    responses: {
      "201": {
  [key: string]: unknown;
};
      "422": HTTPValidationError;
    };
  };
  "get_document_projects__project_id__documents__doc_id__get": {
    method: "GET";
    path: "/projects/{project_id}/documents/{doc_id}";
    parameters: {
      "path:project_id": string;
      "path:doc_id": string;
    };
    responses: {
      "200": {
  [key: string]: unknown;
};
      "422": HTTPValidationError;
    };
  };
  "download_project_document_projects__project_id__documents__doc_id__download_get": {
    method: "GET";
    path: "/projects/{project_id}/documents/{doc_id}/download";
    parameters: {
      "path:project_id": string;
      "path:doc_id": string;
    };
    responses: {
      "200": unknown;
      "422": HTTPValidationError;
    };
  };
  "preview_project_document_projects__project_id__documents__doc_id__preview_get": {
    method: "GET";
    path: "/projects/{project_id}/documents/{doc_id}/preview";
    parameters: {
      "path:project_id": string;
      "path:doc_id": string;
    };
    responses: {
      "200": unknown;
      "422": HTTPValidationError;
    };
  };
  "create_project_document_revision_projects__project_id__documents__doc_id__revisions_post": {
    method: "POST";
    path: "/projects/{project_id}/documents/{doc_id}/revisions";
    parameters: {
      "path:project_id": string;
      "path:doc_id": string;
    };
    requestBody: CreateDocumentRevisionRequest;
    responses: {
      "201": {
  [key: string]: unknown;
};
      "422": HTTPValidationError;
    };
  };
  "update_project_document_status_projects__project_id__documents__doc_id__status_patch": {
    method: "PATCH";
    path: "/projects/{project_id}/documents/{doc_id}/status";
    parameters: {
      "path:project_id": string;
      "path:doc_id": string;
    };
    requestBody: DocumentStatusRequest;
    responses: {
      "200": {
  [key: string]: unknown;
};
      "422": HTTPValidationError;
    };
  };
  "get_project_evidence_projects__project_id__evidence_get": {
    method: "GET";
    path: "/projects/{project_id}/evidence";
    parameters: {
      "path:project_id": string;
    };
    responses: {
      "200": {
  [key: string]: unknown;
};
      "422": HTTPValidationError;
    };
  };
  "set_project_evidence_policy_projects__project_id__evidence_policy_put": {
    method: "PUT";
    path: "/projects/{project_id}/evidence-policy";
    parameters: {
      "path:project_id": string;
    };
    requestBody: ProjectEvidencePolicyRequest;
    responses: {
      "200": {
  [key: string]: unknown;
};
      "422": HTTPValidationError;
    };
  };
  "get_project_evidence_package_projects__project_id__evidence_package_get": {
    method: "GET";
    path: "/projects/{project_id}/evidence/package";
    parameters: {
      "path:project_id": string;
    };
    responses: {
      "200": {
  [key: string]: unknown;
};
      "422": HTTPValidationError;
    };
  };
  "persist_project_evidence_package_projects__project_id__evidence_package_post": {
    method: "POST";
    path: "/projects/{project_id}/evidence/package";
    parameters: {
      "path:project_id": string;
    };
    responses: {
      "200": {
  [key: string]: unknown;
};
      "422": HTTPValidationError;
    };
  };
  "validate_project_evidence_projects__project_id__evidence_validate_post": {
    method: "POST";
    path: "/projects/{project_id}/evidence/validate";
    parameters: {
      "path:project_id": string;
    };
    requestBody: (EvidencePolicyRequest | null);
    responses: {
      "200": {
  [key: string]: unknown;
};
      "422": HTTPValidationError;
    };
  };
  "get_feasibility_projects__project_id__feasibility_get": {
    method: "GET";
    path: "/projects/{project_id}/feasibility";
    parameters: {
      "path:project_id": string;
    };
    responses: {
      "200": {
  [key: string]: unknown;
};
      "422": HTTPValidationError;
    };
  };
  "get_project_flow_instance_projects__project_id__flow_instance_get": {
    method: "GET";
    path: "/projects/{project_id}/flow-instance";
    parameters: {
      "path:project_id": string;
    };
    responses: {
      "200": {
  [key: string]: unknown;
};
      "422": HTTPValidationError;
    };
  };
  "list_project_issues_projects__project_id__issues_get": {
    method: "GET";
    path: "/projects/{project_id}/issues";
    parameters: {
      "path:project_id": string;
      "query:sprint_id"?: (string | null);
      "query:status"?: (string | null);
      "query:assigned_team"?: (string | null);
      "query:limit"?: number;
    };
    responses: {
      "200": Array<{
  [key: string]: unknown;
}>;
      "422": HTTPValidationError;
    };
  };
  "create_canonical_issue_projects__project_id__issues_post": {
    method: "POST";
    path: "/projects/{project_id}/issues";
    parameters: {
      "path:project_id": string;
    };
    requestBody: CanonicalIssueCreateRequest;
    responses: {
      "201": {
  [key: string]: unknown;
};
      "422": HTTPValidationError;
    };
  };
  "get_canonical_issue_projects__project_id__issues__issue_id__get": {
    method: "GET";
    path: "/projects/{project_id}/issues/{issue_id}";
    parameters: {
      "path:project_id": string;
      "path:issue_id": string;
    };
    responses: {
      "200": {
  [key: string]: unknown;
};
      "422": HTTPValidationError;
    };
  };
  "update_canonical_issue_projects__project_id__issues__issue_id__patch": {
    method: "PATCH";
    path: "/projects/{project_id}/issues/{issue_id}";
    parameters: {
      "path:project_id": string;
      "path:issue_id": string;
    };
    requestBody: CanonicalIssueUpdateRequest;
    responses: {
      "200": {
  [key: string]: unknown;
};
      "422": HTTPValidationError;
    };
  };
  "list_canonical_issue_comments_projects__project_id__issues__issue_id__comments_get": {
    method: "GET";
    path: "/projects/{project_id}/issues/{issue_id}/comments";
    parameters: {
      "path:project_id": string;
      "path:issue_id": string;
    };
    responses: {
      "200": Array<{
  [key: string]: unknown;
}>;
      "422": HTTPValidationError;
    };
  };
  "comment_on_canonical_issue_projects__project_id__issues__issue_id__comments_post": {
    method: "POST";
    path: "/projects/{project_id}/issues/{issue_id}/comments";
    parameters: {
      "path:project_id": string;
      "path:issue_id": string;
    };
    requestBody: CanonicalIssueCommentRequest;
    responses: {
      "201": {
  [key: string]: unknown;
};
      "422": HTTPValidationError;
    };
  };
  "list_canonical_issue_links_projects__project_id__issues__issue_id__links_get": {
    method: "GET";
    path: "/projects/{project_id}/issues/{issue_id}/links";
    parameters: {
      "path:project_id": string;
      "path:issue_id": string;
    };
    responses: {
      "200": Array<{
  [key: string]: unknown;
}>;
      "422": HTTPValidationError;
    };
  };
  "link_canonical_issue_projects__project_id__issues__issue_id__links_post": {
    method: "POST";
    path: "/projects/{project_id}/issues/{issue_id}/links";
    parameters: {
      "path:project_id": string;
      "path:issue_id": string;
    };
    requestBody: CanonicalIssueLinkRequest;
    responses: {
      "201": {
  [key: string]: unknown;
};
      "422": HTTPValidationError;
    };
  };
  "list_project_kpi_projects__project_id__kpi_get": {
    method: "GET";
    path: "/projects/{project_id}/kpi";
    parameters: {
      "path:project_id": string;
      "query:scope"?: (string | null);
    };
    responses: {
      "200": Array<{
  [key: string]: unknown;
}>;
      "422": HTTPValidationError;
    };
  };
  "save_project_kpi_projects__project_id__kpi_post": {
    method: "POST";
    path: "/projects/{project_id}/kpi";
    parameters: {
      "path:project_id": string;
    };
    requestBody: KpiSnapshotRequest;
    responses: {
      "201": {
  [key: string]: unknown;
};
      "422": HTTPValidationError;
    };
  };
  "get_project_overview_projects__project_id__overview_get": {
    method: "GET";
    path: "/projects/{project_id}/overview";
    parameters: {
      "path:project_id": string;
    };
    responses: {
      "200": {
  [key: string]: unknown;
};
      "422": HTTPValidationError;
    };
  };
  "get_pending_decisions_projects__project_id__pending_decisions_get": {
    method: "GET";
    path: "/projects/{project_id}/pending-decisions";
    parameters: {
      "path:project_id": string;
    };
    responses: {
      "200": Array<{
  [key: string]: unknown;
}>;
      "422": HTTPValidationError;
    };
  };
  "list_project_pm_bindings_projects__project_id__pm_bindings_get": {
    method: "GET";
    path: "/projects/{project_id}/pm-bindings";
    parameters: {
      "path:project_id": string;
    };
    responses: {
      "200": Array<{
  [key: string]: unknown;
}>;
      "422": HTTPValidationError;
    };
  };
  "create_project_pm_binding_projects__project_id__pm_bindings_post": {
    method: "POST";
    path: "/projects/{project_id}/pm-bindings";
    parameters: {
      "path:project_id": string;
    };
    requestBody: PMBindingCreateRequest;
    responses: {
      "201": {
  [key: string]: unknown;
};
      "422": HTTPValidationError;
    };
  };
  "update_project_pm_binding_projects__project_id__pm_bindings__binding_id__patch": {
    method: "PATCH";
    path: "/projects/{project_id}/pm-bindings/{binding_id}";
    parameters: {
      "path:project_id": string;
      "path:binding_id": string;
    };
    requestBody: PMBindingUpdateRequest;
    responses: {
      "200": {
  [key: string]: unknown;
};
      "422": HTTPValidationError;
    };
  };
  "apply_project_pm_provisioning_projects__project_id__pm_provisioning_apply_post": {
    method: "POST";
    path: "/projects/{project_id}/pm-provisioning/apply";
    parameters: {
      "path:project_id": string;
    };
    requestBody: PMProjectProvisioningApplyRequest;
    responses: {
      "200": {
  [key: string]: unknown;
};
      "422": HTTPValidationError;
    };
  };
  "plan_project_pm_provisioning_projects__project_id__pm_provisioning_plan_post": {
    method: "POST";
    path: "/projects/{project_id}/pm-provisioning/plan";
    parameters: {
      "path:project_id": string;
    };
    requestBody: PMProjectProvisioningRequest;
    responses: {
      "200": {
  [key: string]: unknown;
};
      "422": HTTPValidationError;
    };
  };
  "get_project_repository_projects__project_id__repository_get": {
    method: "GET";
    path: "/projects/{project_id}/repository";
    parameters: {
      "path:project_id": string;
    };
    responses: {
      "200": {
  [key: string]: unknown;
};
      "422": HTTPValidationError;
    };
  };
  "manage_project_repository_projects__project_id__repository_post": {
    method: "POST";
    path: "/projects/{project_id}/repository";
    parameters: {
      "path:project_id": string;
    };
    requestBody: ProjectRepositoryActionRequest;
    responses: {
      "200": {
  [key: string]: unknown;
};
      "422": HTTPValidationError;
    };
  };
  "retry_project_projects__project_id__retry_post": {
    method: "POST";
    path: "/projects/{project_id}/retry";
    parameters: {
      "path:project_id": string;
    };
    responses: {
      "200": {
  [key: string]: unknown;
};
      "422": HTTPValidationError;
    };
  };
  "list_project_review_sessions_projects__project_id__review_sessions_get": {
    method: "GET";
    path: "/projects/{project_id}/review-sessions";
    parameters: {
      "path:project_id": string;
      "query:limit"?: number;
    };
    responses: {
      "200": Array<{
  [key: string]: unknown;
}>;
      "422": HTTPValidationError;
    };
  };
  "get_project_review_session_projects__project_id__review_sessions__session_id__get": {
    method: "GET";
    path: "/projects/{project_id}/review-sessions/{session_id}";
    parameters: {
      "path:project_id": string;
      "path:session_id": string;
    };
    responses: {
      "200": {
  [key: string]: unknown;
};
      "422": HTTPValidationError;
    };
  };
  "get_self_improvement_lifecycle_projects__project_id__self_improvement_get": {
    method: "GET";
    path: "/projects/{project_id}/self-improvement";
    parameters: {
      "path:project_id": string;
    };
    responses: {
      "200": {
  [key: string]: unknown;
};
      "422": HTTPValidationError;
    };
  };
  "apply_self_improvement_action_projects__project_id__self_improvement_actions_post": {
    method: "POST";
    path: "/projects/{project_id}/self-improvement/actions";
    parameters: {
      "path:project_id": string;
    };
    requestBody: SelfImprovementActionRequest;
    responses: {
      "200": {
  [key: string]: unknown;
};
      "422": HTTPValidationError;
    };
  };
  "link_self_improvement_reference_projects__project_id__self_improvement_references_post": {
    method: "POST";
    path: "/projects/{project_id}/self-improvement/references";
    parameters: {
      "path:project_id": string;
    };
    requestBody: SelfImprovementReferenceRequest;
    responses: {
      "200": {
  [key: string]: unknown;
};
      "422": HTTPValidationError;
    };
  };
  "get_sprints_projects__project_id__sprints_get": {
    method: "GET";
    path: "/projects/{project_id}/sprints";
    parameters: {
      "path:project_id": string;
    };
    responses: {
      "200": Array<{
  [key: string]: unknown;
}>;
      "422": HTTPValidationError;
    };
  };
  "create_canonical_sprint_projects__project_id__sprints_post": {
    method: "POST";
    path: "/projects/{project_id}/sprints";
    parameters: {
      "path:project_id": string;
    };
    requestBody: CanonicalSprintCreateRequest;
    responses: {
      "201": {
  [key: string]: unknown;
};
      "422": HTTPValidationError;
    };
  };
  "get_canonical_sprint_projects__project_id__sprints__sprint_id__get": {
    method: "GET";
    path: "/projects/{project_id}/sprints/{sprint_id}";
    parameters: {
      "path:project_id": string;
      "path:sprint_id": string;
    };
    responses: {
      "200": {
  [key: string]: unknown;
};
      "422": HTTPValidationError;
    };
  };
  "update_canonical_sprint_projects__project_id__sprints__sprint_id__patch": {
    method: "PATCH";
    path: "/projects/{project_id}/sprints/{sprint_id}";
    parameters: {
      "path:project_id": string;
      "path:sprint_id": string;
    };
    requestBody: CanonicalSprintUpdateRequest;
    responses: {
      "200": {
  [key: string]: unknown;
};
      "422": HTTPValidationError;
    };
  };
  "get_state_history_projects__project_id__state_history_get": {
    method: "GET";
    path: "/projects/{project_id}/state-history";
    parameters: {
      "path:project_id": string;
      "query:limit"?: number;
    };
    responses: {
      "200": Array<{
  [key: string]: unknown;
}>;
      "422": HTTPValidationError;
    };
  };
  "transition_project_projects__project_id__transition_post": {
    method: "POST";
    path: "/projects/{project_id}/transition";
    parameters: {
      "path:project_id": string;
    };
    requestBody: TransitionRequest;
    responses: {
      "200": {
  [key: string]: unknown;
};
      "422": HTTPValidationError;
    };
  };
  "list_project_usage_events_projects__project_id__usage_events_get": {
    method: "GET";
    path: "/projects/{project_id}/usage/events";
    parameters: {
      "path:project_id": string;
      "query:limit"?: number;
      "query:offset"?: number;
    };
    responses: {
      "200": Array<{
  [key: string]: unknown;
}>;
      "422": HTTPValidationError;
    };
  };
  "get_project_workspace_projects__project_id__workspace_get": {
    method: "GET";
    path: "/projects/{project_id}/workspace";
    parameters: {
      "path:project_id": string;
    };
    responses: {
      "200": {
  [key: string]: unknown;
};
      "422": HTTPValidationError;
    };
  };
  "list_available_runtimes_runtimes_get": {
    method: "GET";
    path: "/runtimes";
    responses: {
      "200": {
  [key: string]: unknown;
};
    };
  };
  "benchmark_runtime_runtimes_benchmark_post": {
    method: "POST";
    path: "/runtimes/benchmark";
    requestBody: RuntimeValidationRequest;
    responses: {
      "200": {
  [key: string]: unknown;
};
      "422": HTTPValidationError;
    };
  };
  "validate_runtime_runtimes_validate_post": {
    method: "POST";
    path: "/runtimes/validate";
    requestBody: RuntimeValidationRequest;
    responses: {
      "200": {
  [key: string]: unknown;
};
      "422": HTTPValidationError;
    };
  };
  "list_stewards_stewards_get": {
    method: "GET";
    path: "/stewards";
    parameters: {
      "query:status"?: (string | null);
      "query:limit"?: number;
    };
    responses: {
      "200": Array<{
  [key: string]: unknown;
}>;
      "422": HTTPValidationError;
    };
  };
  "get_company_overview_system_company_get": {
    method: "GET";
    path: "/system/company";
    responses: {
      "200": {
  [key: string]: unknown;
};
    };
  };
  "system_diagnostics_system_diagnostics_get": {
    method: "GET";
    path: "/system/diagnostics";
    responses: {
      "200": {
  [key: string]: unknown;
};
    };
  };
  "stream_container_logs_system_logs__container__get": {
    method: "GET";
    path: "/system/logs/{container}";
    parameters: {
      "path:container": string;
      "query:tail"?: number;
      "query:follow"?: boolean;
    };
    responses: {
      "200": unknown;
      "422": HTTPValidationError;
    };
  };
  "get_org_graph_system_org_graph_get": {
    method: "GET";
    path: "/system/org-graph";
    responses: {
      "200": {
  [key: string]: unknown;
};
    };
  };
  "system_resume_system_resume_post": {
    method: "POST";
    path: "/system/resume";
    responses: {
      "200": {
  [key: string]: unknown;
};
    };
  };
  "update_schedule_system_schedule_put": {
    method: "PUT";
    path: "/system/schedule";
    requestBody: ScheduleRequest;
    responses: {
      "200": {
  [key: string]: string;
};
      "422": HTTPValidationError;
    };
  };
  "seed_default_company_system_seed_default_company_post": {
    method: "POST";
    path: "/system/seed-default-company";
    responses: {
      "200": {
  [key: string]: unknown;
};
    };
  };
  "system_shutdown_system_shutdown_post": {
    method: "POST";
    path: "/system/shutdown";
    responses: {
      "200": {
  [key: string]: unknown;
};
    };
  };
  "shutdown_ack_system_shutdown_ack_post": {
    method: "POST";
    path: "/system/shutdown-ack";
    requestBody: {
  [key: string]: unknown;
};
    responses: {
      "200": {
  [key: string]: string;
};
      "422": HTTPValidationError;
    };
  };
  "shutdown_nack_system_shutdown_nack_post": {
    method: "POST";
    path: "/system/shutdown-nack";
    requestBody: {
  [key: string]: unknown;
};
    responses: {
      "200": {
  [key: string]: string;
};
      "422": HTTPValidationError;
    };
  };
  "system_status_system_status_get": {
    method: "GET";
    path: "/system/status";
    responses: {
      "200": {
  [key: string]: unknown;
};
    };
  };
  "create_task_tasks_post": {
    method: "POST";
    path: "/tasks";
    requestBody: {
  [key: string]: unknown;
};
    responses: {
      "200": {
  [key: string]: unknown;
};
      "422": HTTPValidationError;
    };
  };
  "get_task_tasks__task_id__get": {
    method: "GET";
    path: "/tasks/{task_id}";
    parameters: {
      "path:task_id": string;
    };
    responses: {
      "200": {
  [key: string]: unknown;
};
      "422": HTTPValidationError;
    };
  };
  "list_teams_teams_get": {
    method: "GET";
    path: "/teams";
    responses: {
      "200": Array<{
  [key: string]: string;
}>;
    };
  };
  "get_usage_event_evidence_usage_events__event_id__get": {
    method: "GET";
    path: "/usage/events/{event_id}";
    parameters: {
      "path:event_id": string;
    };
    responses: {
      "200": {
  [key: string]: unknown;
};
      "422": HTTPValidationError;
    };
  };
  "chat_completions_v1_chat_completions_post": {
    method: "POST";
    path: "/v1/chat/completions";
    parameters: {
      "header:authorization"?: (string | null);
    };
    requestBody: ChatCompletionRequest;
    responses: {
      "200": unknown;
      "422": HTTPValidationError;
    };
  };
  "legacy_completions_v1_completions_post": {
    method: "POST";
    path: "/v1/completions";
    parameters: {
      "header:authorization"?: (string | null);
    };
    requestBody: LegacyCompletionRequest;
    responses: {
      "200": {
  [key: string]: unknown;
};
      "422": HTTPValidationError;
    };
  };
  "list_models_v1_models_get": {
    method: "GET";
    path: "/v1/models";
    parameters: {
      "header:authorization"?: (string | null);
    };
    responses: {
      "200": {
  [key: string]: unknown;
};
      "422": HTTPValidationError;
    };
  };
  "worker_contract_version_worker_contract_version_get": {
    method: "GET";
    path: "/worker-contract/version";
    responses: {
      "200": {
  [key: string]: unknown;
};
    };
  };
  "list_worker_runs_api_workers_runs_get": {
    method: "GET";
    path: "/workers/runs";
    parameters: {
      "query:project_id"?: (string | null);
      "query:worker_id"?: (string | null);
      "query:flow_instance_id"?: (string | null);
      "query:state"?: (string | null);
      "query:limit"?: number;
      "query:offset"?: number;
    };
    responses: {
      "200": Array<{
  [key: string]: unknown;
}>;
      "422": HTTPValidationError;
    };
  };
  "dispatch_worker_run_workers_runs_post": {
    method: "POST";
    path: "/workers/runs";
    requestBody: WorkerRunDispatchRequest;
    responses: {
      "202": {
  [key: string]: unknown;
};
      "422": HTTPValidationError;
    };
  };
  "recover_expired_worker_runs_workers_runs_recover_expired_post": {
    method: "POST";
    path: "/workers/runs/recover-expired";
    parameters: {
      "query:limit"?: number;
    };
    responses: {
      "200": {
  [key: string]: unknown;
};
      "422": HTTPValidationError;
    };
  };
  "get_worker_run_api_workers_runs__run_id__get": {
    method: "GET";
    path: "/workers/runs/{run_id}";
    parameters: {
      "path:run_id": string;
    };
    responses: {
      "200": {
  [key: string]: unknown;
};
      "422": HTTPValidationError;
    };
  };
  "get_worker_run_artifacts_workers_runs__run_id__artifacts_get": {
    method: "GET";
    path: "/workers/runs/{run_id}/artifacts";
    parameters: {
      "path:run_id": string;
      "query:limit"?: number;
    };
    responses: {
      "200": Array<{
  [key: string]: unknown;
}>;
      "422": HTTPValidationError;
    };
  };
  "cancel_worker_run_workers_runs__run_id__cancel_post": {
    method: "POST";
    path: "/workers/runs/{run_id}/cancel";
    parameters: {
      "path:run_id": string;
    };
    requestBody: {
  [key: string]: unknown;
};
    responses: {
      "200": {
  [key: string]: unknown;
};
      "422": HTTPValidationError;
    };
  };
  "get_worker_run_checkpoints_workers_runs__run_id__checkpoints_get": {
    method: "GET";
    path: "/workers/runs/{run_id}/checkpoints";
    parameters: {
      "path:run_id": string;
      "query:limit"?: number;
    };
    responses: {
      "200": Array<{
  [key: string]: unknown;
}>;
      "422": HTTPValidationError;
    };
  };
  "get_worker_run_events_workers_runs__run_id__events_get": {
    method: "GET";
    path: "/workers/runs/{run_id}/events";
    parameters: {
      "path:run_id": string;
      "query:limit"?: number;
      "query:offset"?: number;
    };
    responses: {
      "200": Array<{
  [key: string]: unknown;
}>;
      "422": HTTPValidationError;
    };
  };
  "heartbeat_worker_run_workers_runs__run_id__heartbeat_post": {
    method: "POST";
    path: "/workers/runs/{run_id}/heartbeat";
    parameters: {
      "path:run_id": string;
    };
    requestBody: {
  [key: string]: unknown;
};
    responses: {
      "200": {
  [key: string]: unknown;
};
      "422": HTTPValidationError;
    };
  };
  "pause_worker_run_workers_runs__run_id__pause_post": {
    method: "POST";
    path: "/workers/runs/{run_id}/pause";
    parameters: {
      "path:run_id": string;
    };
    requestBody: WorkerRunPauseRequest;
    responses: {
      "200": {
  [key: string]: unknown;
};
      "422": HTTPValidationError;
    };
  };
  "resume_worker_run_workers_runs__run_id__resume_post": {
    method: "POST";
    path: "/workers/runs/{run_id}/resume";
    parameters: {
      "path:run_id": string;
    };
    requestBody: WorkerRunResumeRequest;
    responses: {
      "200": {
  [key: string]: unknown;
};
      "422": HTTPValidationError;
    };
  };
  "get_worker_run_transitions_workers_runs__run_id__transitions_get": {
    method: "GET";
    path: "/workers/runs/{run_id}/transitions";
    parameters: {
      "path:run_id": string;
      "query:limit"?: number;
    };
    responses: {
      "200": Array<{
  [key: string]: unknown;
}>;
      "422": HTTPValidationError;
    };
  };
  "get_worker_run_usage_workers_runs__run_id__usage_get": {
    method: "GET";
    path: "/workers/runs/{run_id}/usage";
    parameters: {
      "path:run_id": string;
      "query:limit"?: number;
    };
    responses: {
      "200": Array<{
  [key: string]: unknown;
}>;
      "422": HTTPValidationError;
    };
  };
};

export type OrchestratorOperationId = keyof OrchestratorApiOperations;
export type OrchestratorApiPath = OrchestratorApiOperations[OrchestratorOperationId]["path"];
