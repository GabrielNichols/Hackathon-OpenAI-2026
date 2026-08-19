from __future__ import annotations

from datetime import date, datetime, time
from enum import StrEnum
from typing import Any, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class SupplierSearchCriteria(BaseModel):
    """Frozen search contract shared with the supplier directory (plan 02)."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    tenant_id: str = Field(min_length=1)
    category: str = Field(min_length=1)
    city: str = Field(min_length=1)
    district: str | None = None
    event_date: date
    delivery_time: time | None = None
    people_count: int = Field(gt=0)
    invoice_required: bool
    dietary_requirements: dict[str, int] = Field(default_factory=dict)
    mandatory_tags: list[str] = Field(default_factory=list)
    maximum_lead_time_hours: int | None = Field(default=None, ge=0)

    @field_validator("dietary_requirements")
    @classmethod
    def validate_dietary_counts(cls, value: dict[str, int]) -> dict[str, int]:
        if any(not key.strip() for key in value):
            raise ValueError("dietary requirement names cannot be blank")
        if any(count < 0 for count in value.values()):
            raise ValueError("dietary requirement counts cannot be negative")
        return value

    @field_validator("mandatory_tags")
    @classmethod
    def validate_mandatory_tags(cls, value: list[str]) -> list[str]:
        if any(not tag.strip() for tag in value):
            raise ValueError("mandatory tags cannot be blank")
        return value


class SupplierCandidateDTO(BaseModel):
    """Supplier-directory result contract shared with plan 02."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    supplier_id: str = Field(min_length=1)
    display_name: str = Field(min_length=1)
    status: str
    categories: list[str] = Field(default_factory=list)
    service_areas: list[str] = Field(default_factory=list)
    minimum_people: int | None = Field(default=None, ge=0)
    maximum_people: int | None = Field(default=None, ge=0)
    lead_time_hours: int | None = Field(default=None, ge=0)
    invoice_available: bool | None = None
    dietary_capabilities: dict[str, str] = Field(default_factory=dict)
    sustainability_tags: list[str] = Field(default_factory=list)
    last_confirmed_at: datetime | None = None
    evidence_refs: list[str] = Field(default_factory=list)
    missing_fields: list[str] = Field(default_factory=list)


class EligibilityOutcome(StrEnum):
    PASS = "pass"
    FAIL = "fail"
    UNKNOWN = "unknown"


class EligibilityDecision(StrEnum):
    ELIGIBLE = "eligible"
    EXCLUDED = "excluded"
    NEEDS_REFRESH = "needs_refresh"


class EligibilityCheck(BaseModel):
    """A deterministic, evidence-bearing decision for one criterion."""

    model_config = ConfigDict(extra="forbid")

    criterion: str
    required_value: Any | None
    actual_value: Any | None
    outcome: EligibilityOutcome
    passed: bool | None = None
    reason_code: str
    evidence: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def keep_passed_consistent_with_outcome(self) -> Self:
        expected = {
            EligibilityOutcome.PASS: True,
            EligibilityOutcome.FAIL: False,
            EligibilityOutcome.UNKNOWN: None,
        }[self.outcome]
        if self.passed is not None and self.passed is not expected:
            raise ValueError("passed must agree with outcome")
        self.passed = expected
        return self


class SupplierEligibilityResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    supplier_id: str
    decision: EligibilityDecision
    checks: list[EligibilityCheck] = Field(min_length=1)
    evidence_refs: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def decision_must_match_checks(self) -> Self:
        outcomes = {check.outcome for check in self.checks}
        if EligibilityOutcome.FAIL in outcomes:
            expected = EligibilityDecision.EXCLUDED
        elif EligibilityOutcome.UNKNOWN in outcomes:
            expected = EligibilityDecision.NEEDS_REFRESH
        else:
            expected = EligibilityDecision.ELIGIBLE
        if self.decision is not expected:
            raise ValueError("decision must be derived from eligibility check outcomes")
        return self


__all__ = [
    "EligibilityCheck",
    "EligibilityDecision",
    "EligibilityOutcome",
    "SupplierCandidateDTO",
    "SupplierEligibilityResult",
    "SupplierSearchCriteria",
]
