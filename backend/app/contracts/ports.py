"""Stable ports connecting feature modules to the deterministic core."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol, runtime_checkable

from .common import (
    AuditEventDTO,
    AwardId,
    EntityId,
    ProcurementRequestId,
    SupplierId,
    UtcDateTime,
)
from .policy import AuthorizationDecision, AuthorizationRequest
from .quotes import (
    ApprovalDTO,
    AwardDTO,
    NegotiationCommand,
    NegotiationResultDTO,
    QuoteComparisonDTO,
    RequestApprovalCommand,
    SendAwardCommand,
)
from .rfq import (
    CreateRFQRoundCommand,
    DeliveryBatchDTO,
    RFQRoundDTO,
    SendRFQRoundCommand,
)
from .suppliers import SupplierCandidateDTO, SupplierSearchCriteria


@runtime_checkable
class Clock(Protocol):
    def now(self) -> UtcDateTime: ...


@runtime_checkable
class IdGenerator(Protocol):
    def new(self, prefix: str) -> EntityId: ...


@runtime_checkable
class AuditPort(Protocol):
    async def append(self, events: Sequence[AuditEventDTO]) -> None: ...


@runtime_checkable
class PolicyPort(Protocol):
    async def authorize(self, request: AuthorizationRequest) -> AuthorizationDecision: ...


@runtime_checkable
class SupplierDirectoryPort(Protocol):
    async def search(self, criteria: SupplierSearchCriteria) -> list[SupplierCandidateDTO]: ...

    async def get(self, supplier_id: SupplierId) -> SupplierCandidateDTO | None: ...


@runtime_checkable
class RFQExecutionPort(Protocol):
    async def create_round(self, command: CreateRFQRoundCommand) -> RFQRoundDTO: ...

    async def send_round(self, command: SendRFQRoundCommand) -> DeliveryBatchDTO: ...

    async def get_status(self, rfq_round_id: EntityId) -> RFQRoundDTO: ...


@runtime_checkable
class QuoteDecisionPort(Protocol):
    async def compare(
        self,
        procurement_request_id: ProcurementRequestId,
    ) -> QuoteComparisonDTO: ...

    async def run_negotiation(
        self,
        command: NegotiationCommand,
    ) -> NegotiationResultDTO: ...

    async def request_approval(
        self,
        command: RequestApprovalCommand,
    ) -> ApprovalDTO: ...

    async def send_award(self, command: SendAwardCommand) -> AwardDTO: ...

    async def get_award_status(self, award_id: AwardId) -> AwardDTO: ...
