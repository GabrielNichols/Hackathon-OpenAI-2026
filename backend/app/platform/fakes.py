from collections.abc import Sequence

from app.contracts import (
    ApprovalDTO,
    ApprovalStatus,
    AuditEventDTO,
    AwardDTO,
    AwardId,
    AwardStatus,
    Clock,
    CreateRFQRoundCommand,
    DeliveryBatchDTO,
    DeliveryDTO,
    IdGenerator,
    NegotiationCommand,
    NegotiationResultDTO,
    QuoteComparisonDTO,
    QuoteComparisonEntryDTO,
    RequestApprovalCommand,
    RFQDeliveryStatus,
    RFQRoundDTO,
    SendAwardCommand,
    SendRFQRoundCommand,
    SupplierCandidateDTO,
    SupplierSearchCriteria,
    SupplierState,
)
from app.platform.idempotency import InMemoryIdempotencyRegistry


class InMemoryAuditPort:
    def __init__(self) -> None:
        self.events: list[AuditEventDTO] = []

    async def append(self, events: Sequence[AuditEventDTO]) -> None:
        self.events.extend(events)


class FakeSupplierDirectory:
    def __init__(self, candidates: Sequence[SupplierCandidateDTO]) -> None:
        self._candidates = {candidate.supplier_id: candidate for candidate in candidates}

    async def search(self, criteria: SupplierSearchCriteria) -> list[SupplierCandidateDTO]:
        matches: list[SupplierCandidateDTO] = []
        for candidate in self._candidates.values():
            if candidate.status is not SupplierState.ACTIVE:
                continue
            if criteria.category not in candidate.categories:
                continue
            if criteria.city not in candidate.service_areas:
                continue
            if (
                candidate.minimum_people is not None
                and criteria.people_count < candidate.minimum_people
            ):
                continue
            if (
                candidate.maximum_people is not None
                and criteria.people_count > candidate.maximum_people
            ):
                continue
            if criteria.invoice_required and candidate.invoice_available is not True:
                continue
            if not set(criteria.mandatory_tags).issubset(candidate.sustainability_tags):
                continue
            matches.append(candidate)
        return matches

    async def get(self, supplier_id: str) -> SupplierCandidateDTO | None:
        candidate = self._candidates.get(supplier_id)
        if candidate is None or candidate.status is not SupplierState.ACTIVE:
            return None
        return candidate


class FakeRFQExecutionPort:
    def __init__(self, clock: Clock, ids: IdGenerator) -> None:
        self._clock = clock
        self._ids = ids
        self._rounds: dict[str, RFQRoundDTO] = {}
        self._idempotency = InMemoryIdempotencyRegistry()

    async def create_round(self, command: CreateRFQRoundCommand) -> RFQRoundDTO:
        return await self._idempotency.execute(
            tenant_id="fake",
            operation="rfq.create_round",
            key=command.idempotency_key,
            payload=command,
            handler=lambda: self._create_round(command),
        )

    def _create_round(self, command: CreateRFQRoundCommand) -> RFQRoundDTO:
        item = RFQRoundDTO(
            rfq_round_id=self._ids.new("rfq"),
            procurement_request_id=command.procurement_request_id,
            request_version=command.request_version,
            requirements_snapshot=command.requirements_snapshot,
            policy_snapshot=command.policy_snapshot,
            recipient_supplier_ids=command.recipient_supplier_ids,
            response_deadline=command.response_deadline,
            created_at=self._clock.now(),
            version=0,
        )
        self._rounds[item.rfq_round_id] = item
        return item

    async def send_round(self, command: SendRFQRoundCommand) -> DeliveryBatchDTO:
        return await self._idempotency.execute(
            tenant_id="fake",
            operation="rfq.send_round",
            key=command.idempotency_key,
            payload=command,
            handler=lambda: self._send_round(command),
        )

    def _send_round(self, command: SendRFQRoundCommand) -> DeliveryBatchDTO:
        item = self._rounds[command.rfq_round_id]
        deliveries = [
            DeliveryDTO(
                recipient_id=self._ids.new("rcp"),
                supplier_id=supplier_id,
                status=RFQDeliveryStatus.DELIVERED,
                external_id=self._ids.new("delivery"),
                delivered_at=self._clock.now(),
            )
            for supplier_id in item.recipient_supplier_ids
        ]
        updated = item.model_copy(update={"deliveries": deliveries, "version": item.version + 1})
        self._rounds[item.rfq_round_id] = updated
        return DeliveryBatchDTO(
            rfq_round_id=item.rfq_round_id,
            deliveries=deliveries,
            all_confirmed=True,
        )

    async def get_status(self, rfq_round_id: str) -> RFQRoundDTO:
        return self._rounds[rfq_round_id]


class StaticQuoteDecisionPort:
    """Deterministic implementation of the complete quote-decision port."""

    def __init__(self, comparison: QuoteComparisonDTO) -> None:
        self._comparison = comparison
        self._approvals: dict[str, ApprovalDTO] = {}
        self._awards: dict[str, AwardDTO] = {}

    def _entry(self, quote_id: str, quote_version: int) -> QuoteComparisonEntryDTO:
        for entry in self._comparison.entries:
            if entry.quote_id == quote_id and entry.quote_version == quote_version:
                return entry
        raise KeyError((quote_id, quote_version))

    async def compare(self, procurement_request_id: str) -> QuoteComparisonDTO:
        if procurement_request_id != self._comparison.procurement_request_id:
            raise KeyError(procurement_request_id)
        return self._comparison

    async def run_negotiation(
        self,
        command: NegotiationCommand,
    ) -> NegotiationResultDTO:
        self._entry(command.quote_id, command.quote_version)
        return NegotiationResultDTO(
            negotiation_round_id="neg_static",
            quote_id=command.quote_id,
            quote_version_before=command.quote_version,
            quote_version_after=None,
            status="NO_CHANGE",
            created_at=self._comparison.generated_at,
        )

    async def request_approval(
        self,
        command: RequestApprovalCommand,
    ) -> ApprovalDTO:
        if command.procurement_request_id != self._comparison.procurement_request_id:
            raise KeyError(command.procurement_request_id)
        self._entry(command.quote_id, command.quote_version)
        approval = ApprovalDTO(
            approval_id="apr_static",
            procurement_request_id=command.procurement_request_id,
            quote_id=command.quote_id,
            quote_version=command.quote_version,
            approver_user_id=command.approver_user_id,
            status=ApprovalStatus.REQUESTED,
            requested_at=self._comparison.generated_at,
            version=0,
        )
        self._approvals[approval.approval_id] = approval
        return approval

    async def send_award(self, command: SendAwardCommand) -> AwardDTO:
        approval = self._approvals.get(command.approval_id)
        if approval is None:
            raise KeyError(command.approval_id)
        if (
            approval.procurement_request_id != command.procurement_request_id
            or approval.quote_id != command.approved_quote_id
            or approval.quote_version != command.approved_quote_version
        ):
            raise ValueError("award command does not match the approval snapshot")
        entry = self._entry(command.approved_quote_id, command.approved_quote_version)
        if entry.supplier_id != command.supplier_id:
            raise ValueError("award supplier does not match the approved quote")
        award = AwardDTO(
            award_id="awd_static",
            procurement_request_id=command.procurement_request_id,
            supplier_id=command.supplier_id,
            approved_quote_id=command.approved_quote_id,
            approved_quote_version=command.approved_quote_version,
            approved_total_cents=entry.total_cents,
            terms_snapshot={},
            approval_id=command.approval_id,
            status=AwardStatus.CREATED,
            created_at=self._comparison.generated_at,
            version=0,
        )
        self._awards[award.award_id] = award
        return award

    async def get_award_status(self, award_id: AwardId) -> AwardDTO:
        return self._awards[award_id]


StaticQuoteComparisonPort = StaticQuoteDecisionPort
