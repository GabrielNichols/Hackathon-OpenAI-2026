from datetime import UTC, date, datetime, timedelta

import pytest

from app.contracts import (
    CreateRFQRoundCommand,
    SendRFQRoundCommand,
    SupplierCandidateDTO,
    SupplierSearchCriteria,
    SupplierState,
)
from app.platform.clock import FixedClock
from app.platform.fakes import FakeRFQExecutionPort, FakeSupplierDirectory
from app.platform.ids import SequenceIdGenerator

NOW = datetime(2026, 8, 19, 15, tzinfo=UTC)


def candidate(status: SupplierState, *, invoice: bool | None = True) -> SupplierCandidateDTO:
    return SupplierCandidateDTO(
        supplier_id="sup_alpha" if status is SupplierState.ACTIVE else "sup_draft",
        display_name="Supplier Alpha",
        status=status,
        categories=["corporate_catering"],
        service_areas=["Sao Paulo"],
        minimum_people=20,
        maximum_people=200,
        lead_time_hours=24,
        invoice_available=invoice,
        dietary_capabilities={"vegan": "supported"},
        sustainability_tags=["no_single_use_plastic"],
        last_confirmed_at=NOW,
        evidence_refs=["evd_alpha"],
        missing_fields=[],
    )


@pytest.mark.asyncio
async def test_supplier_directory_returns_only_active_confirmed_suppliers() -> None:
    directory = FakeSupplierDirectory(
        [candidate(SupplierState.ACTIVE), candidate(SupplierState.DRAFT)]
    )
    result = await directory.search(
        SupplierSearchCriteria(
            tenant_id="org_demo",
            category="corporate_catering",
            city="Sao Paulo",
            district=None,
            event_date=date(2026, 8, 28),
            delivery_time=None,
            people_count=80,
            invoice_required=True,
            dietary_requirements={"vegan": 4},
            mandatory_tags=["no_single_use_plastic"],
            maximum_lead_time_hours=48,
        )
    )
    assert [item.supplier_id for item in result] == ["sup_alpha"]


@pytest.mark.asyncio
async def test_fake_rfq_round_records_real_delivery_ack() -> None:
    port = FakeRFQExecutionPort(FixedClock(NOW), SequenceIdGenerator())
    round_item = await port.create_round(
        CreateRFQRoundCommand(
            procurement_request_id="pr_demo",
            request_version=1,
            recipient_supplier_ids=["sup_alpha"],
            response_deadline=NOW + timedelta(days=1),
            requirements_snapshot={"people_count": 80},
            policy_snapshot={"maximum_total_cents": 450_000},
            idempotency_key="rfq-create-1",
        )
    )
    batch = await port.send_round(
        SendRFQRoundCommand(
            rfq_round_id=round_item.rfq_round_id,
            channel="email",
            idempotency_key="rfq-send-1",
        )
    )
    assert batch.all_confirmed
    assert batch.deliveries[0].external_id == "delivery_1"
    assert (await port.get_status(round_item.rfq_round_id)).version == 1
