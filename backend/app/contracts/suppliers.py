"""Shared supplier directory input and output contracts."""

from __future__ import annotations

from datetime import date, time

from pydantic import Field, model_validator
from typing_extensions import Self

from .common import (
    ContractModel,
    ContractString,
    EntityId,
    NonNegativeCount,
    PositiveCount,
    SupplierId,
    SupplierState,
    UtcDateTime,
)


class SupplierSearchCriteria(ContractModel):
    tenant_id: EntityId
    category: ContractString
    city: ContractString
    district: ContractString | None
    event_date: date
    delivery_time: time | None
    people_count: PositiveCount
    invoice_required: bool
    dietary_requirements: dict[str, NonNegativeCount]
    mandatory_tags: list[str]
    maximum_lead_time_hours: NonNegativeCount | None


class SupplierCandidateDTO(ContractModel):
    supplier_id: SupplierId
    display_name: ContractString
    status: SupplierState
    categories: list[str]
    service_areas: list[str]
    minimum_people: NonNegativeCount | None = None
    maximum_people: NonNegativeCount | None = None
    lead_time_hours: NonNegativeCount | None = None
    invoice_available: bool | None = None
    dietary_capabilities: dict[str, str]
    sustainability_tags: list[str]
    last_confirmed_at: UtcDateTime | None = None
    evidence_refs: list[EntityId] = Field(default_factory=list)
    missing_fields: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_people_range(self) -> Self:
        if (
            self.minimum_people is not None
            and self.maximum_people is not None
            and self.minimum_people > self.maximum_people
        ):
            raise ValueError("minimum_people cannot exceed maximum_people")
        return self
