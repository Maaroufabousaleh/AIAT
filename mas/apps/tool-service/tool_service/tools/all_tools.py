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
    ProjectStatusTool,
    ProjectTransitionTool,
    ReviewAggregateTool,
    ReviewStartSessionTool,
    ReviewSubmitResponseTool,
    ReviewSubmitVetoTool,
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
    SprintActivateTool,
    SprintCloseTool,
    SprintCreateTool,
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
        SprintActivateTool(),
        SprintCloseTool(),
        IssueCreateTool(),
        IssueDecomposeTool(),
        IssueUpdateStatusTool(),
        IssueListTool(),
        KPIComputeSprintTool(),
        KPIComputeProjectTool(),
        KPIQueryHistoryTool(),
        KPIUpdateAgentProfileTool(),
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
    ]
