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
from .capability import (
    CapabilitySearchTool,
    CapabilityListWorkersTool,
    CapabilityRegisterTool,
    CapabilityDeregisterTool,
)
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
    ProjectListTool,
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
