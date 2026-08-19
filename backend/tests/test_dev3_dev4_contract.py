from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime, timedelta
from typing import Literal

import pytest
from app.bootstrap import create_execution_service
from app.modules.rfq.dev3_adapter import Dev3RFQExecutionAdapter
from app.shared.errors import DomainError, ErrorCode
from pydantic import BaseModel, ConfigDict

NOW = datetime(2026, 8, 19, 15, 0, tzinfo=UTC)


class Dev3RFQRoundDTO(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rfq_round_id: str
    procurement_request_id: str
    status: Literal["DRAFT"]
    recipient_supplier_ids: list[str]
    created_at: datetime
    version: int


def dev3_command() -> dict:
    return {
        "procurement_request_id": "pr_dev3_handoff",
        "request_version": 2,
        "plan_version": 3,
        "sourcing_run_id": "sourcing_run_1",
        "recipient_supplier_ids": ["supplier_beta", "supplier_alpha"],
        "response_deadline": NOW + timedelta(hours=3),
        "requirements_snapshot": {
            "request_id": "pr_dev3_handoff",
            "status": "READY",
            "version": 2,
            "category": "corporate_catering",
            "description": "Coffee break corporativo para 80 pessoas",
            "event_date": "2026-08-22",
            "delivery_time": "08:30",
            "location_city": "Sao Paulo",
            "location_district": "Vila Olimpia",
            "people_count": 80,
            "maximum_total_cents": 450_000,
            "currency": "BRL",
            "vegetarian_count": 12,
            "vegan_count": 4,
            "gluten_free_count": 3,
            "invoice_required": True,
            "no_single_use_plastic": True,
            "response_deadline": NOW + timedelta(hours=3),
            "approver_user_id": "buyer_gabriel",
        },
        "policy_snapshot": {
            "policy_id": "procurement_default_v1",
            "version": 7,
            "default_location_city": "Sao Paulo",
            "negotiation_enabled": False,
            "maximum_negotiation_rounds": 2,
            "maximum_follow_ups": 2,
            "target_total_cents": 400_000,
            "allowed_negotiation_topics": ["total_price", "delivery_fee"],
            "ranking_weights": {
                "total_price": 50,
                "mandatory_requirements": 35,
                "response_time": 15,
            },
        },
        "context": {
            "tenant_id": "org_demo",
            "actor_type": "agent",
            "actor_id": "agent_dev3",
            "correlation_id": "cor_dev3_1",
            "causation_id": "event_ready_1",
            "agent_run_id": "agent_run_1",
            "idempotency_key": "dev3:create-rfq:1",
        },
    }


@pytest.mark.asyncio
async def test_dev3_command_maps_to_dev4_and_back_to_strict_dev3_dto():
    service = create_execution_service(now=NOW)
    adapter = Dev3RFQExecutionAdapter(service)

    result = Dev3RFQRoundDTO.model_validate(await adapter.create_round(dev3_command()))

    assert result.recipient_supplier_ids == ["supplier_alpha", "supplier_beta"]
    record = service.store.rounds[result.rfq_round_id]
    assert record["tenant_id"] == "org_demo"
    assert record["requirements"].timezone == "America/Sao_Paulo"
    assert record["requirements"].mandatory_requirements == [
        "dietary_restrictions",
        "invoice",
        "no_single_use_plastic",
    ]
    assert record["policy"].ranking_weights == {
        "price": 50,
        "restrictions": 35,
        "response": 15,
    }
    assert record["policy"].maximum_negotiation_rounds == 0


@pytest.mark.asyncio
async def test_dev3_retry_can_change_only_tracing_fields():
    service = create_execution_service(now=NOW)
    adapter = Dev3RFQExecutionAdapter(service)
    first_command = dev3_command()
    retry_command = deepcopy(first_command)
    retry_command["sourcing_run_id"] = "sourcing_run_2"
    retry_command["context"]["correlation_id"] = "cor_dev3_2"
    retry_command["context"]["causation_id"] = "event_ready_2"
    retry_command["context"]["agent_run_id"] = "agent_run_2"

    first = await adapter.create_round(first_command)
    retry = await adapter.create_round(retry_command)

    assert retry["rfq_round_id"] == first["rfq_round_id"]
    assert len(service.store.rounds) == 1


@pytest.mark.asyncio
async def test_dev3_retry_rejects_changed_business_payload():
    service = create_execution_service(now=NOW)
    adapter = Dev3RFQExecutionAdapter(service)
    first_command = dev3_command()
    conflicting_command = deepcopy(first_command)
    conflicting_command["recipient_supplier_ids"] = ["supplier_alpha"]

    await adapter.create_round(first_command)
    with pytest.raises(DomainError) as captured:
        await adapter.create_round(conflicting_command)

    assert captured.value.code == ErrorCode.IDEMPOTENCY_CONFLICT


@pytest.mark.asyncio
async def test_dev3_handoff_does_not_invent_an_actor():
    service = create_execution_service(now=NOW)
    adapter = Dev3RFQExecutionAdapter(service)
    command = dev3_command()
    command["context"]["actor_id"] = None

    with pytest.raises(DomainError) as captured:
        await adapter.create_round(command)

    assert captured.value.code == ErrorCode.VALIDATION_ERROR
    assert service.store.rounds == {}
