from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone

import pytest
from pydantic import TypeAdapter, ValidationError

from app.contracts import (
    ActorType,
    AuditEventDTO,
    AwardId,
    ErrorCode,
    ErrorDetailDTO,
    ErrorEnvelopeDTO,
    MoneyCents,
    ProcurementRequestId,
    QuoteId,
    SupplierId,
    Version,
)


def _audit_event(**overrides: object) -> AuditEventDTO:
    values: dict[str, object] = {
        "event_id": "evt_001",
        "event_type": "PROCUREMENT_READY",
        "aggregate_type": "procurement_request",
        "aggregate_id": "pr_demo",
        "actor_type": ActorType.AGENT,
        "actor_id": "agent_demo",
        "occurred_at": datetime(2026, 8, 19, 15, 0, tzinfo=UTC),
        "previous_state": "DRAFT",
        "new_state": "READY",
        "correlation_id": "cor_001",
        "causation_id": "evt_000",
        "agent_run_id": "run_001",
        "idempotency_key": "idem-ready-001",
        "payload": {"request_version": 1},
    }
    values.update(overrides)
    return AuditEventDTO.model_validate(values)


@pytest.mark.parametrize("invalid", [-1, 1.5, True, float("nan"), float("inf")])
def test_money_cents_rejects_negative_bool_float_nan_and_infinity(invalid: object) -> None:
    with pytest.raises(ValidationError):
        TypeAdapter(MoneyCents).validate_python(invalid)


@pytest.mark.parametrize("valid", [0, 1, 450_000])
def test_money_cents_accepts_non_negative_integers(valid: int) -> None:
    assert TypeAdapter(MoneyCents).validate_python(valid) == valid


@pytest.mark.parametrize("invalid", [-1, 1.0, True])
def test_version_rejects_negative_float_and_bool(invalid: object) -> None:
    with pytest.raises(ValidationError):
        TypeAdapter(Version).validate_python(invalid)


def test_entity_specific_ids_enforce_the_contract_prefixes() -> None:
    assert TypeAdapter(SupplierId).validate_python("sup_alpha") == "sup_alpha"
    assert TypeAdapter(ProcurementRequestId).validate_python("pr_demo") == "pr_demo"
    assert TypeAdapter(QuoteId).validate_python("quo_alpha_v1") == "quo_alpha_v1"
    assert TypeAdapter(AwardId).validate_python("awd_001") == "awd_001"

    with pytest.raises(ValidationError):
        TypeAdapter(SupplierId).validate_python("supplier_alpha")
    with pytest.raises(ValidationError):
        TypeAdapter(QuoteId).validate_python("quote_alpha")


def test_error_envelope_serializes_to_the_stable_shape() -> None:
    envelope = ErrorEnvelopeDTO(
        error=ErrorDetailDTO(
            code=ErrorCode.INVALID_STATE_TRANSITION,
            message="Quote cannot move from REQUESTED to VALID",
            details={},
            correlation_id="cor_123",
        )
    )

    assert envelope.model_dump(mode="json") == {
        "error": {
            "code": "INVALID_STATE_TRANSITION",
            "message": "Quote cannot move from REQUESTED to VALID",
            "details": {},
            "correlation_id": "cor_123",
        }
    }


def test_contract_models_reject_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        ErrorDetailDTO.model_validate(
            {
                "code": "NOT_FOUND",
                "message": "Not found",
                "details": {},
                "correlation_id": "cor_123",
                "unexpected": "must not be silently ignored",
            }
        )


def test_audit_event_json_round_trip_preserves_all_fields() -> None:
    event = _audit_event()

    assert AuditEventDTO.model_validate_json(event.model_dump_json()) == event
    assert event.model_dump(mode="json")["event_type"] == "PROCUREMENT_READY"


def test_audit_event_normalizes_aware_timestamp_to_utc() -> None:
    sao_paulo = timezone(timedelta(hours=-3))
    event = _audit_event(occurred_at=datetime(2026, 8, 19, 12, 0, tzinfo=sao_paulo))

    assert event.occurred_at == datetime(2026, 8, 19, 15, 0, tzinfo=UTC)
    assert event.occurred_at.tzinfo is UTC


def test_audit_event_rejects_naive_timestamp() -> None:
    with pytest.raises(ValidationError):
        _audit_event(occurred_at=datetime(2026, 8, 19, 15, 0))


@pytest.mark.parametrize("event_type", ["procurement_ready", "RFQ-SENT", "", " RFQ_SENT"])
def test_audit_event_type_must_be_upper_snake_case(event_type: str) -> None:
    with pytest.raises(ValidationError):
        _audit_event(event_type=event_type)


def test_audit_payload_defaults_are_not_shared() -> None:
    first = _audit_event(payload={})
    second = _audit_event(payload={})

    first.payload["one"] = 1

    assert second.payload == {}
