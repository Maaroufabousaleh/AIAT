"""Gather all concrete tool instances for registration."""

from __future__ import annotations

from mas_tools_sdk.base import BaseTool

from .file import FileReadTool, FileWriteTool
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
        # MEMORY
        SharedMemoryReadTool(),
        SharedMemoryWriteTool(),
        # PROJECT
        ProjectCreateTool(),
        ProjectStatusTool(),
        ProjectTransitionTool(),
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
        # SPRINT_KPI
        SprintCreateTool(),
        SprintActivateTool(),
        SprintCloseTool(),
        IssueCreateTool(),
        IssueDecomposeTool(),
        IssueUpdateStatusTool(),
        KPIComputeSprintTool(),
        KPIComputeProjectTool(),
        KPIQueryHistoryTool(),
        KPIUpdateAgentProfileTool(),
        VelocityReportTool(),
        EstimationAdjustTool(),
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
