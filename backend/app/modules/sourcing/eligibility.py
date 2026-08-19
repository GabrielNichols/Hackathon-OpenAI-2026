from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable, Mapping
from datetime import UTC, datetime, timedelta
from typing import Any

from app.modules.sourcing.models import (
    EligibilityCheck,
    EligibilityDecision,
    EligibilityOutcome,
    SupplierCandidateDTO,
    SupplierEligibilityResult,
    SupplierSearchCriteria,
)

_SUPPORTED_VALUES = frozenset(
    {
        "1",
        "available",
        "confirmed",
        "yes",
        "true",
        "sim",
        "supported",
        "suportado",
        "atendido",
    }
)
_UNSUPPORTED_VALUES = frozenset(
    {
        "0",
        "false",
        "indisponivel",
        "nao",
        "no",
        "not supported",
        "unsupported",
        "nao suportado",
    }
)


def _canonical(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value.casefold())
    without_marks = "".join(char for char in decomposed if not unicodedata.combining(char))
    return " ".join(re.sub(r"[^a-z0-9]+", " ", without_marks).split())


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _decision_for(checks: Iterable[EligibilityCheck]) -> EligibilityDecision:
    outcomes = {check.outcome for check in checks}
    if EligibilityOutcome.FAIL in outcomes:
        return EligibilityDecision.EXCLUDED
    if EligibilityOutcome.UNKNOWN in outcomes:
        return EligibilityDecision.NEEDS_REFRESH
    return EligibilityDecision.ELIGIBLE


class SupplierEligibilityEngine:
    """Pure eligibility rules; time enters explicitly through ``as_of``."""

    def __init__(self, max_profile_age: timedelta = timedelta(days=90)) -> None:
        if max_profile_age <= timedelta(0):
            raise ValueError("max_profile_age must be positive")
        self._max_profile_age = max_profile_age

    @property
    def max_profile_age(self) -> timedelta:
        return self._max_profile_age

    def evaluate(
        self,
        candidate: SupplierCandidateDTO,
        criteria: SupplierSearchCriteria,
        *,
        as_of: datetime,
    ) -> SupplierEligibilityResult:
        evidence = list(candidate.evidence_refs)
        checks: list[EligibilityCheck] = []

        checks.append(self._status_check(candidate, evidence))
        checks.append(self._category_check(candidate, criteria, evidence))
        checks.append(self._region_check(candidate, criteria, evidence))
        checks.extend(self._capacity_checks(candidate, criteria, evidence))
        checks.append(self._lead_time_check(candidate, criteria, evidence))
        checks.append(self._invoice_check(candidate, criteria, evidence))
        checks.extend(self._dietary_checks(candidate, criteria, evidence))
        checks.extend(self._mandatory_tag_checks(candidate, criteria, evidence))
        checks.append(self._freshness_check(candidate, as_of, evidence))
        checks.append(self._missing_fields_check(candidate, criteria, evidence))

        return SupplierEligibilityResult(
            supplier_id=candidate.supplier_id,
            decision=_decision_for(checks),
            checks=checks,
            evidence_refs=evidence,
        )

    def evaluate_many(
        self,
        candidates: Iterable[SupplierCandidateDTO],
        criteria: SupplierSearchCriteria,
        *,
        as_of: datetime,
    ) -> list[SupplierEligibilityResult]:
        return [self.evaluate(candidate, criteria, as_of=as_of) for candidate in candidates]

    def _check(
        self,
        *,
        criterion: str,
        required: Any | None,
        actual: Any | None,
        outcome: EligibilityOutcome,
        reason_code: str,
        evidence: list[str],
    ) -> EligibilityCheck:
        return EligibilityCheck(
            criterion=criterion,
            required_value=required,
            actual_value=actual,
            outcome=outcome,
            reason_code=reason_code,
            evidence=evidence,
        )

    def _marked_missing(self, candidate: SupplierCandidateDTO, *field_names: str) -> bool:
        missing = {_canonical(field) for field in candidate.missing_fields}
        aliases = {_canonical(field) for field in field_names}
        return any(
            missing_field == alias or missing_field.startswith(f"{alias} ")
            for missing_field in missing
            for alias in aliases
        )

    def _status_check(
        self, candidate: SupplierCandidateDTO, evidence: list[str]
    ) -> EligibilityCheck:
        if self._marked_missing(candidate, "status") or not candidate.status.strip():
            outcome, reason = EligibilityOutcome.UNKNOWN, "STATUS_UNKNOWN"
        elif _canonical(candidate.status) == "active":
            outcome, reason = EligibilityOutcome.PASS, "STATUS_ACTIVE"
        else:
            outcome, reason = EligibilityOutcome.FAIL, "SUPPLIER_NOT_ACTIVE"
        return self._check(
            criterion="status",
            required="ACTIVE",
            actual=candidate.status,
            outcome=outcome,
            reason_code=reason,
            evidence=evidence,
        )

    def _category_check(
        self,
        candidate: SupplierCandidateDTO,
        criteria: SupplierSearchCriteria,
        evidence: list[str],
    ) -> EligibilityCheck:
        actual = list(candidate.categories)
        if self._marked_missing(candidate, "categories", "category"):
            outcome, reason = EligibilityOutcome.UNKNOWN, "CATEGORY_UNKNOWN"
        elif _canonical(criteria.category) in {_canonical(value) for value in actual}:
            outcome, reason = EligibilityOutcome.PASS, "CATEGORY_MATCH"
        else:
            outcome, reason = EligibilityOutcome.FAIL, "CATEGORY_MISMATCH"
        return self._check(
            criterion="category",
            required=criteria.category,
            actual=actual,
            outcome=outcome,
            reason_code=reason,
            evidence=evidence,
        )

    def _region_check(
        self,
        candidate: SupplierCandidateDTO,
        criteria: SupplierSearchCriteria,
        evidence: list[str],
    ) -> EligibilityCheck:
        actual = list(candidate.service_areas)
        required: dict[str, str | None] = {
            "city": criteria.city,
            "district": criteria.district,
        }
        if self._marked_missing(candidate, "service_areas", "service_area", "region"):
            outcome, reason = EligibilityOutcome.UNKNOWN, "SERVICE_AREA_UNKNOWN"
        elif self._covers_region(actual, criteria.city, criteria.district):
            outcome, reason = EligibilityOutcome.PASS, "SERVICE_AREA_MATCH"
        else:
            outcome, reason = EligibilityOutcome.FAIL, "SERVICE_AREA_MISMATCH"
        return self._check(
            criterion="service_area",
            required=required,
            actual=actual,
            outcome=outcome,
            reason_code=reason,
            evidence=evidence,
        )

    def _covers_region(self, service_areas: list[str], city: str, district: str | None) -> bool:
        actual = {_canonical(area) for area in service_areas}
        city_key = _canonical(city)
        if not district:
            return city_key in actual

        district_key = _canonical(district)
        accepted = {
            city_key,  # An explicit city entry means city-wide coverage.
            district_key,
            f"{city_key} {district_key}",
            f"{district_key} {city_key}",
        }
        return bool(actual & accepted)

    def _capacity_checks(
        self,
        candidate: SupplierCandidateDTO,
        criteria: SupplierSearchCriteria,
        evidence: list[str],
    ) -> list[EligibilityCheck]:
        minimum = candidate.minimum_people
        if self._marked_missing(candidate, "minimum_people") or minimum is None:
            min_outcome, min_reason = EligibilityOutcome.UNKNOWN, "CAPACITY_UNKNOWN"
        elif criteria.people_count >= minimum:
            min_outcome, min_reason = EligibilityOutcome.PASS, "MINIMUM_PEOPLE_COMPATIBLE"
        else:
            min_outcome, min_reason = EligibilityOutcome.FAIL, "BELOW_MINIMUM_PEOPLE"

        maximum = candidate.maximum_people
        if self._marked_missing(candidate, "maximum_people") or maximum is None:
            max_outcome, max_reason = EligibilityOutcome.UNKNOWN, "CAPACITY_UNKNOWN"
        elif criteria.people_count <= maximum:
            max_outcome, max_reason = EligibilityOutcome.PASS, "CAPACITY_SUFFICIENT"
        else:
            max_outcome, max_reason = EligibilityOutcome.FAIL, "CAPACITY_EXCEEDED"

        return [
            self._check(
                criterion="minimum_people",
                required={"people_count": criteria.people_count},
                actual=minimum,
                outcome=min_outcome,
                reason_code=min_reason,
                evidence=evidence,
            ),
            self._check(
                criterion="maximum_people",
                required={"people_count": criteria.people_count},
                actual=maximum,
                outcome=max_outcome,
                reason_code=max_reason,
                evidence=evidence,
            ),
        ]

    def _lead_time_check(
        self,
        candidate: SupplierCandidateDTO,
        criteria: SupplierSearchCriteria,
        evidence: list[str],
    ) -> EligibilityCheck:
        maximum = criteria.maximum_lead_time_hours
        actual = candidate.lead_time_hours
        if maximum is None:
            outcome, reason = EligibilityOutcome.PASS, "LEAD_TIME_NOT_CONSTRAINED"
        elif self._marked_missing(candidate, "lead_time_hours") or actual is None:
            outcome, reason = EligibilityOutcome.UNKNOWN, "LEAD_TIME_UNKNOWN"
        elif actual <= maximum:
            outcome, reason = EligibilityOutcome.PASS, "LEAD_TIME_COMPATIBLE"
        else:
            outcome, reason = EligibilityOutcome.FAIL, "LEAD_TIME_EXCEEDED"
        return self._check(
            criterion="lead_time_hours",
            required=maximum,
            actual=actual,
            outcome=outcome,
            reason_code=reason,
            evidence=evidence,
        )

    def _invoice_check(
        self,
        candidate: SupplierCandidateDTO,
        criteria: SupplierSearchCriteria,
        evidence: list[str],
    ) -> EligibilityCheck:
        actual = candidate.invoice_available
        if not criteria.invoice_required:
            outcome, reason = EligibilityOutcome.PASS, "INVOICE_NOT_REQUIRED"
        elif self._marked_missing(candidate, "invoice_available") or actual is None:
            outcome, reason = EligibilityOutcome.UNKNOWN, "INVOICE_STATUS_UNKNOWN"
        elif actual is True:
            outcome, reason = EligibilityOutcome.PASS, "INVOICE_REQUIREMENT_MET"
        else:
            outcome, reason = EligibilityOutcome.FAIL, "INVOICE_UNAVAILABLE"
        return self._check(
            criterion="invoice_available",
            required=criteria.invoice_required,
            actual=actual,
            outcome=outcome,
            reason_code=reason,
            evidence=evidence,
        )

    def _dietary_checks(
        self,
        candidate: SupplierCandidateDTO,
        criteria: SupplierSearchCriteria,
        evidence: list[str],
    ) -> list[EligibilityCheck]:
        required = sorted(
            ((name, count) for name, count in criteria.dietary_requirements.items() if count > 0),
            key=lambda item: (_canonical(item[0]), item[0]),
        )
        if not required:
            return [
                self._check(
                    criterion="dietary_requirements",
                    required={},
                    actual=dict(candidate.dietary_capabilities),
                    outcome=EligibilityOutcome.PASS,
                    reason_code="DIETARY_REQUIREMENTS_NOT_APPLICABLE",
                    evidence=evidence,
                )
            ]

        capabilities: Mapping[str, str] = {
            _canonical(name): value for name, value in candidate.dietary_capabilities.items()
        }
        checks: list[EligibilityCheck] = []
        for name, count in required:
            key = _canonical(name)
            raw_value = capabilities.get(key)
            missing = self._marked_missing(
                candidate,
                "dietary_capabilities",
                name,
                f"{name}_supported",
            )
            if missing or raw_value is None:
                outcome, reason = EligibilityOutcome.UNKNOWN, "DIETARY_CAPABILITY_UNKNOWN"
            else:
                normalized = _canonical(raw_value)
                if normalized in _SUPPORTED_VALUES:
                    outcome, reason = EligibilityOutcome.PASS, "DIETARY_REQUIREMENT_MET"
                elif normalized in _UNSUPPORTED_VALUES:
                    outcome, reason = EligibilityOutcome.FAIL, "DIETARY_REQUIREMENT_UNSUPPORTED"
                else:
                    outcome, reason = EligibilityOutcome.UNKNOWN, "DIETARY_CAPABILITY_UNKNOWN"
            checks.append(
                self._check(
                    criterion=f"dietary:{name}",
                    required=count,
                    actual=raw_value,
                    outcome=outcome,
                    reason_code=reason,
                    evidence=evidence,
                )
            )
        return checks

    def _mandatory_tag_checks(
        self,
        candidate: SupplierCandidateDTO,
        criteria: SupplierSearchCriteria,
        evidence: list[str],
    ) -> list[EligibilityCheck]:
        required = sorted(set(criteria.mandatory_tags), key=lambda tag: (_canonical(tag), tag))
        if not required:
            return [
                self._check(
                    criterion="mandatory_tags",
                    required=[],
                    actual=list(candidate.sustainability_tags),
                    outcome=EligibilityOutcome.PASS,
                    reason_code="MANDATORY_TAGS_NOT_APPLICABLE",
                    evidence=evidence,
                )
            ]

        actual = {_canonical(tag) for tag in candidate.sustainability_tags}
        unknown = self._marked_missing(candidate, "sustainability_tags", "mandatory_tags")
        checks: list[EligibilityCheck] = []
        for tag in required:
            if unknown:
                outcome, reason = EligibilityOutcome.UNKNOWN, "SUSTAINABILITY_TAGS_UNKNOWN"
            elif _canonical(tag) in actual:
                outcome, reason = EligibilityOutcome.PASS, "MANDATORY_TAG_PRESENT"
            else:
                outcome, reason = EligibilityOutcome.FAIL, "MANDATORY_TAG_MISSING"
            checks.append(
                self._check(
                    criterion=f"mandatory_tag:{tag}",
                    required=tag,
                    actual=list(candidate.sustainability_tags),
                    outcome=outcome,
                    reason_code=reason,
                    evidence=evidence,
                )
            )
        return checks

    def _freshness_check(
        self,
        candidate: SupplierCandidateDTO,
        as_of: datetime,
        evidence: list[str],
    ) -> EligibilityCheck:
        actual = candidate.last_confirmed_at
        required = {"max_age_seconds": int(self._max_profile_age.total_seconds())}
        if self._marked_missing(candidate, "last_confirmed_at") or actual is None:
            outcome, reason = EligibilityOutcome.UNKNOWN, "LAST_CONFIRMATION_MISSING"
        else:
            age = _as_utc(as_of) - _as_utc(actual)
            if age < timedelta(0):
                outcome, reason = EligibilityOutcome.UNKNOWN, "LAST_CONFIRMATION_IN_FUTURE"
            elif age <= self._max_profile_age:
                outcome, reason = EligibilityOutcome.PASS, "PROFILE_FRESH"
            else:
                outcome, reason = EligibilityOutcome.UNKNOWN, "PROFILE_STALE"
        return self._check(
            criterion="freshness",
            required=required,
            actual=actual,
            outcome=outcome,
            reason_code=reason,
            evidence=evidence,
        )

    def _missing_fields_check(
        self,
        candidate: SupplierCandidateDTO,
        criteria: SupplierSearchCriteria,
        evidence: list[str],
    ) -> EligibilityCheck:
        relevant = self._relevant_missing_fields(candidate, criteria)
        if relevant:
            outcome, reason = EligibilityOutcome.UNKNOWN, "CRITICAL_FIELD_MISSING"
        else:
            outcome, reason = EligibilityOutcome.PASS, "NO_CRITICAL_FIELDS_MISSING"
        return self._check(
            criterion="missing_fields",
            required=[],
            actual=relevant,
            outcome=outcome,
            reason_code=reason,
            evidence=evidence,
        )

    def _relevant_missing_fields(
        self,
        candidate: SupplierCandidateDTO,
        criteria: SupplierSearchCriteria,
    ) -> list[str]:
        aliases = {
            "status",
            "category",
            "categories",
            "service area",
            "service areas",
            "region",
            "minimum people",
            "maximum people",
            "last confirmed at",
        }
        if criteria.maximum_lead_time_hours is not None:
            aliases.add("lead time hours")
        if criteria.invoice_required:
            aliases.add("invoice available")
        for name, count in criteria.dietary_requirements.items():
            if count > 0:
                key = _canonical(name)
                aliases.update({"dietary capabilities", key, f"{key} supported"})
        if criteria.mandatory_tags:
            aliases.update({"sustainability tags", "mandatory tags"})

        relevant: list[str] = []
        for original in candidate.missing_fields:
            field = _canonical(original)
            if any(field == alias or field.startswith(f"{alias} ") for alias in aliases):
                relevant.append(original)
        return sorted(set(relevant), key=lambda field: (_canonical(field), field))


def evaluate_supplier_eligibility(
    candidate: SupplierCandidateDTO,
    criteria: SupplierSearchCriteria,
    *,
    as_of: datetime,
    max_profile_age: timedelta = timedelta(days=90),
) -> SupplierEligibilityResult:
    """Convenience function for callers that do not retain an engine instance."""

    return SupplierEligibilityEngine(max_profile_age=max_profile_age).evaluate(
        candidate,
        criteria,
        as_of=as_of,
    )


__all__ = ["SupplierEligibilityEngine", "evaluate_supplier_eligibility"]
