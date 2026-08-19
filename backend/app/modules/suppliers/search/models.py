from __future__ import annotations

from datetime import date, datetime, time

from pydantic import BaseModel, ConfigDict, Field, field_validator

from ..application.core_compat import SupplierLifecycleStatus


class SupplierSearchCriteria(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    tenant_id: str = Field(min_length=1)
    category: str = Field(min_length=1)
    city: str = Field(min_length=1)
    district: str | None = None
    event_date: date
    delivery_time: time | None = None
    people_count: int = Field(gt=0)
    invoice_required: bool
    dietary_requirements: dict[str, int]
    mandatory_tags: list[str]
    maximum_lead_time_hours: int | None = Field(default=None, ge=0)

    @field_validator("dietary_requirements")
    @classmethod
    def dietary_counts_are_non_negative(cls, value: dict[str, int]) -> dict[str, int]:
        if any(count < 0 for count in value.values()):
            raise ValueError("dietary requirement counts cannot be negative")
        return value


class SupplierCandidateDTO(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    supplier_id: str
    display_name: str
    status: SupplierLifecycleStatus
    categories: list[str]
    service_areas: list[str]
    minimum_people: int | None = Field(default=None, ge=0)
    maximum_people: int | None = Field(default=None, ge=0)
    lead_time_hours: int | None = Field(default=None, ge=0)
    invoice_available: bool | None
    dietary_capabilities: dict[str, str]
    sustainability_tags: list[str]
    last_confirmed_at: datetime | None
    evidence_refs: list[str]
    missing_fields: list[str]


class SupplierDirectoryRecord(BaseModel):
    """Feature-owned read model; it is not the central SupplierAggregate."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    tenant_id: str
    supplier_id: str
    display_name: str
    status: SupplierLifecycleStatus
    profile_confirmed: bool
    categories: list[str]
    service_cities: list[str]
    service_districts: list[str]
    minimum_people: int | None = Field(default=None, ge=0)
    maximum_people: int | None = Field(default=None, ge=0)
    lead_time_hours: int | None = Field(default=None, ge=0)
    invoice_available: bool | None
    dietary_capabilities: dict[str, str]
    sustainability_tags: list[str]
    last_confirmed_at: datetime | None
    evidence_refs: list[str]
