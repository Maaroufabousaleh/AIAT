"""Gather all concrete tool instances for registration."""

from __future__ import annotations

from typing import TYPE_CHECKING

from .adapters import (
    CodeReviewTool,
    CommandRunSafeTool,
    DiagramRenderTool,
    DocumentIngestTool,
    IaCPlanTool,
    MCPInvokeTool,
    RepoReadTool,
    RepoSearchTool,
    SecurityScanTool,
    TestRunTool,
)
from .capability import (
    CapabilityDeregisterTool,
    CapabilityListWorkersTool,
    CapabilityRegisterTool,
    CapabilitySearchTool,
)
from .file import FilePatchTool, FileReadTool, FileWriteTool

try:
    from .browser import (
        BrowserClickTool,
        BrowserCloseTool,
        BrowserEvaluateTool,
        BrowserNavigateTool,
        BrowserScreenshotTool,
        BrowserTypeTool,
    )
except ModuleNotFoundError:  # pragma: no cover - optional local dependency
    BrowserNavigateTool = None
    BrowserClickTool = None
    BrowserTypeTool = None
    BrowserScreenshotTool = None
    BrowserEvaluateTool = None
    BrowserCloseTool = None
from .flow import (
    FlowAdvanceTool,
    FlowAssignTool,
    FlowInvokeTool,
    FlowListTool,
    FlowRecommendTool,
    FlowStatusTool,
)
from .identity import get_identity_tools
from .infra import (
    BlobDeleteTool,
    BlobDownloadTool,
    BlobListTool,
    BlobUploadTool,
    CICDConfigureTool,
    InfraProvisionTool,
    InfraReadySignalTool,
    MonitoringSetupTool,
    SecretsManageTool,
)
from .memory import SharedMemoryReadTool, SharedMemoryWriteTool
from .opencode_workspace import (
    OpenCodeWorkspacePytestTool,
    OpenCodeWorkspaceReadTool,
    OpenCodeWorkspaceWriteTool,
)
from .pm import IssueCommentTool, IssueGetTool, IssueLinkTool, IssueUpdateTool, PMSyncStatusTool
from .project import (
    ApprovalOverrideCSOTool,
    DepartmentTaskTool,
    DocumentCreateDraftTool,
    DocumentGetLatestTool,
    DocumentListTool,
    DocumentReviseTool,
    DocumentSubmitTool,
    HumanAwaitDecisionTool,
    HumanNotifyTool,
    ProjectCreateTool,
    ProjectListTool,
    ProjectRepositoryTool,
    ProjectStatusTool,
    ProjectTransitionTool,
    ReviewAggregateTool,
    ReviewStartSessionTool,
    ReviewSubmitResponseTool,
    ReviewSubmitVetoTool,
)
from .scm import (
    SCMBranchCreateTool,
    SCMCheckPublishTool,
    SCMCommitEvidenceTool,
    SCMInstallationDiscoverTool,
    SCMPullRequestCreateTool,
    SCMReviewCommentTool,
    SCMRunCredentialTool,
)
from .sprint_kpi import (
    EstimationAdjustTool,
    IssueCreateTool,
    IssueDecomposeTool,
    IssueListTool,
    IssueUpdateStatusTool,
    KPIComputeProjectTool,
    KPIComputeSprintTool,
    KPIQueryHistoryTool,
    KPIUpdateAgentProfileTool,
    RetrospectiveGenerateTool,
    SprintActivateTool,
    SprintCloseTool,
    SprintCreateTool,
    SprintListTool,
    VelocityReportTool,
)
from .time import TimeNowTool
from .web import WebFetchTool, WebSearchTool

if TYPE_CHECKING:
    from mas_tools_sdk.base import BaseTool


def get_all_tools() -> list[BaseTool]:
    """Instantiate and return all concrete tool implementations."""
    return [
        # WEB
        WebSearchTool(),
        WebFetchTool(),
        # FILE
        FileReadTool(),
        FileWriteTool(),
        FilePatchTool(),
        OpenCodeWorkspaceReadTool(),
        OpenCodeWorkspaceWriteTool(),
        OpenCodeWorkspacePytestTool(),
        RepoReadTool(),
        RepoSearchTool(),
        CommandRunSafeTool(),
        # BROWSER
        *(
            [
                BrowserNavigateTool(),
                BrowserClickTool(),
                BrowserTypeTool(),
                BrowserScreenshotTool(),
                BrowserEvaluateTool(),
                BrowserCloseTool(),
            ]
            if BrowserNavigateTool is not None
            else []
        ),
        # MEMORY
        SharedMemoryReadTool(),
        SharedMemoryWriteTool(),
        # PROJECT
        ProjectCreateTool(),
        ProjectRepositoryTool(),
        ProjectStatusTool(),
        ProjectTransitionTool(),
        ProjectListTool(),
        DocumentCreateDraftTool(),
        DocumentSubmitTool(),
        DocumentReviseTool(),
        DocumentGetLatestTool(),
        DocumentListTool(),
        ReviewStartSessionTool(),
        ReviewSubmitResponseTool(),
        ReviewSubmitVetoTool(),
        ReviewAggregateTool(),
        ApprovalOverrideCSOTool(),
        HumanNotifyTool(),
        HumanAwaitDecisionTool(),
        DepartmentTaskTool(),
        # FLOW
        FlowListTool(),
        FlowRecommendTool(),
        FlowInvokeTool(),
        FlowStatusTool(),
        FlowAdvanceTool(),
        FlowAssignTool(),
        # SPRINT_KPI
        SprintCreateTool(),
        SprintListTool(),
        SprintActivateTool(),
        SprintCloseTool(),
        IssueCreateTool(),
        IssueDecomposeTool(),
        IssueUpdateStatusTool(),
        IssueListTool(),
        IssueGetTool(),
        IssueUpdateTool(),
        IssueCommentTool(),
        IssueLinkTool(),
        PMSyncStatusTool(),
        # SOURCE CONTROL
        SCMInstallationDiscoverTool(),
        SCMBranchCreateTool(),
        SCMPullRequestCreateTool(),
        SCMReviewCommentTool(),
        SCMCheckPublishTool(),
        SCMCommitEvidenceTool(),
        SCMRunCredentialTool(),
        KPIComputeSprintTool(),
        KPIComputeProjectTool(),
        KPIQueryHistoryTool(),
        KPIUpdateAgentProfileTool(),
        RetrospectiveGenerateTool(),
        VelocityReportTool(),
        EstimationAdjustTool(),
        # CAPABILITY
        CapabilitySearchTool(),
        CapabilityListWorkersTool(),
        CapabilityRegisterTool(),
        CapabilityDeregisterTool(),
        # TIME
        TimeNowTool(),
        # DEFAULT OSS CAPABILITY ADAPTERS
        DocumentIngestTool(),
        SecurityScanTool(),
        TestRunTool(),
        CodeReviewTool(),
        IaCPlanTool(),
        DiagramRenderTool(),
        MCPInvokeTool(),
        # INFRA
        InfraProvisionTool(),
        CICDConfigureTool(),
        MonitoringSetupTool(),
        SecretsManageTool(),
        InfraReadySignalTool(),
        BlobUploadTool(),
        BlobDownloadTool(),
        BlobListTool(),
        BlobDeleteTool(),
        # Governed mail, external-account and opaque browser-session boundary.
        *get_identity_tools(),
    ]
