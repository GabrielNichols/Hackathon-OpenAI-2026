from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.modules.buyer_timeline.audit import AuditEvent
from app.modules.procurement_agent.models import AgentStopReason, RFQRoundDTO
from app.modules.procurement_requests.schemas import (
    FieldConflict,
    ProcurementPlan,
    ProcurementRequestDraft,
    ProcurementRequestReady,
    ProcurementRequestStatus,
)
from app.modules.sourcing.models import SupplierCandidateDTO, SupplierEligibilityResult


class ProcurementProcess(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_id: str
    tenant_id: str
    buyer_user_id: str
    status: ProcurementRequestStatus = ProcurementRequestStatus.DRAFT
    request: ProcurementRequestDraft | ProcurementRequestReady
    evidence: dict[str, str] = Field(default_factory=dict)
    confidence_by_field: dict[str, float] = Field(default_factory=dict)
    assumptions: list[str] = Field(default_factory=list)
    ambiguities: list[str] = Field(default_factory=list)
    conflicts: list[FieldConflict] = Field(default_factory=list)
    missing_fields: list[str] = Field(default_factory=list)
    clarification_question: str | None = None
    plan: ProcurementPlan | None = None
    plan_confirmed_at: datetime | None = None
    supplier_candidates: list[SupplierCandidateDTO] = Field(default_factory=list)
    eligibility_results: list[SupplierEligibilityResult] = Field(default_factory=list)
    selected_supplier_ids: list[str] = Field(default_factory=list)
    rfq_round: RFQRoundDTO | None = None
    stop_reason: AgentStopReason | None = None
    last_agent_run_id: str | None = None
    mode: str = "demo_fake"


class EligibilityResultView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    supplier_id: str
    display_name: str
    decision: str
    checks: list[dict[str, Any]]
    evidence_refs: list[str]


class ProcurementWorkflowView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_id: str
    status: ProcurementRequestStatus
    stop_reason: AgentStopReason | None
    draft: dict[str, Any]
    evidence: dict[str, str]
    missing_fields: list[str]
    clarification_question: str | None
    plan: ProcurementPlan | None
    eligibility_results: list[EligibilityResultView]
    selected_supplier_ids: list[str]
    rfq_round_id: str | None
    timeline: list[AuditEvent]
    mode: str


class InMemoryProcurementProcessRepository:
    def __init__(self) -> None:
        self._processes: dict[str, ProcurementProcess] = {}

    async def save(self, process: ProcurementProcess) -> None:
        self._processes[process.request_id] = process.model_copy(deep=True)

    async def get(self, request_id: str) -> ProcurementProcess | None:
        process = self._processes.get(request_id)
        return process.model_copy(deep=True) if process else None


__all__ = [
    "EligibilityResultView",
    "InMemoryProcurementProcessRepository",
    "ProcurementProcess",
    "ProcurementWorkflowView",
]
