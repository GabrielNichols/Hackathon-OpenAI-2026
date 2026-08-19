"""Shared RFQ command, delivery, and round DTOs."""

from __future__ import annotations

from typing import Any, Self

from pydantic import Field, model_validator

from .common import (
    ContractModel,
    ContractString,
    DeliveryChannel,
    EntityId,
    IdempotencyKey,
    ProcurementRequestId,
    RFQDeliveryStatus,
    RFQId,
    StableCode,
    SupplierId,
    UtcDateTime,
    Version,
)


class CreateRFQRoundCommand(ContractModel):
    procurement_request_id: ProcurementRequestId
    request_version: Version
    recipient_supplier_ids: list[SupplierId] = Field(min_length=1)
    response_deadline: UtcDateTime
    requirements_snapshot: dict[str, Any]
    policy_snapshot: dict[str, Any]
    idempotency_key: IdempotencyKey

    @model_validator(mode="after")
    def reject_duplicate_recipients(self) -> Self:
        if len(self.recipient_supplier_ids) != len(set(self.recipient_supplier_ids)):
            raise ValueError("recipient_supplier_ids must be unique")
        return self


class SendRFQRoundCommand(ContractModel):
    rfq_round_id: RFQId
    channel: DeliveryChannel
    idempotency_key: IdempotencyKey


class DeliveryDTO(ContractModel):
    recipient_id: EntityId
    supplier_id: SupplierId
    status: RFQDeliveryStatus
    external_id: ContractString | None = None
    delivered_at: UtcDateTime | None = None
    error_code: StableCode | None = None

    @model_validator(mode="after")
    def validate_delivery_ack(self) -> Self:
        if self.status is RFQDeliveryStatus.DELIVERED and (
            self.external_id is None or self.delivered_at is None
        ):
            raise ValueError("DELIVERED requires external_id and delivered_at")
        return self


class DeliveryBatchDTO(ContractModel):
    rfq_round_id: RFQId
    deliveries: list[DeliveryDTO] = Field(min_length=1)
    all_confirmed: bool

    @model_validator(mode="after")
    def validate_all_confirmed(self) -> Self:
        actually_all_confirmed = all(
            delivery.status is RFQDeliveryStatus.DELIVERED for delivery in self.deliveries
        )
        if self.all_confirmed is not actually_all_confirmed:
            raise ValueError("all_confirmed must reflect the individual delivery acknowledgements")
        return self


class RFQRoundDTO(ContractModel):
    rfq_round_id: RFQId
    procurement_request_id: ProcurementRequestId
    request_version: Version
    requirements_snapshot: dict[str, Any]
    policy_snapshot: dict[str, Any]
    recipient_supplier_ids: list[SupplierId] = Field(min_length=1)
    response_deadline: UtcDateTime
    created_by_agent_run_id: EntityId | None = None
    created_at: UtcDateTime
    version: Version
    deliveries: list[DeliveryDTO] = Field(default_factory=list)
