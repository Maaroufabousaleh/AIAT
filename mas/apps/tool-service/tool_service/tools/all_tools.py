"""Gather all concrete tool instances for registration."""

from __future__ import annotations

from mas_tools_sdk.base import BaseTool

from .capability import (
    CapabilityDeregisterTool,
    CapabilityListWorkersTool,
    CapabilityRegisterTool,
    CapabilitySearchTool,
)
from .file import FileReadTool, FileWriteTool

try:
    from .browser import (
        BrowserNavigateTool,
        BrowserClickTool,
        BrowserTypeTool,
        BrowserScreenshotTool,
        BrowserEvaluateTool,
        BrowserCloseTool,
    )
except ModuleNotFoundError:  # pragma: no cover - optional local dependency
    BrowserNavigateTool = None
    BrowserClickTool = None
    BrowserTypeTool = None
    BrowserScreenshotTool = None
    BrowserEvaluateTool = None
    BrowserCloseTool = None
from .infra import (
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
from .flow import (
    FlowAdvanceTool,
    FlowAssignTool,
    FlowInvokeTool,
    FlowListTool,
    FlowRecommendTool,
    FlowStatusTool,
)
from .sprint_kpi import (
    EstimationAdjustTool,
    IssueCreateTool,
    IssueDecomposeTool,
    IssueUpdateStatusTool,
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


def get_all_tools() -> list[BaseTool]:
    """Instantiate and return all concrete tool implementations."""
    return [
        # WEB
        WebSearchTool(),
        WebFetchTool(),
        # FILE
        FileReadTool(),
        FileWriteTool(),
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
        KPIComputeSprintTool(),
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
        # INFRA
        InfraProvisionTool(),
        CICDConfigureTool(),
        MonitoringSetupTool(),
        SecretsManageTool(),
        InfraReadySignalTool(),
        BlobUploadTool(),
        BlobDownloadTool(),
        BlobListTool(),
    ]
