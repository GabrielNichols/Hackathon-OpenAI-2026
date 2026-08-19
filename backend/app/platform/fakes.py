from collections.abc import Sequence

from app.contracts import (
    AuditEventDTO,
    Clock,
    CreateRFQRoundCommand,
    DeliveryBatchDTO,
    DeliveryDTO,
    IdGenerator,
    QuoteComparisonDTO,
    RFQDeliveryStatus,
    RFQRoundDTO,
    SendRFQRoundCommand,
    SupplierCandidateDTO,
    SupplierSearchCriteria,
    SupplierState,
)


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

    async def create_round(self, command: CreateRFQRoundCommand) -> RFQRoundDTO:
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


class StaticQuoteComparisonPort:
    """Minimal comparison fake for Dev 3 orchestration tests."""

    def __init__(self, comparison: QuoteComparisonDTO) -> None:
        self._comparison = comparison

    async def compare(self, procurement_request_id: str) -> QuoteComparisonDTO:
        if procurement_request_id != self._comparison.procurement_request_id:
            raise KeyError(procurement_request_id)
        return self._comparison
