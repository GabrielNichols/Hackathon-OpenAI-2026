"""Deterministic quote calculation and validation.

The functions in this module deliberately accept Pydantic models, dataclasses,
or mappings.  That keeps the business rules independent from transport and
persistence while the shared Dev 1 contracts are integrated.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from app.shared.errors import DomainError, ErrorCode

_NO_DEFAULT = object()
_MISSING = object()
_REQUIRED_TEXT_FIELDS = (
    "respondent_name",
    "respondent_contact",
    "cancellation_terms",
)
_KNOWN_DIETARY_STATUSES = {
    "confirmed",
    "partial",
    "unknown",
    "not_available",
}


@dataclass(frozen=True, slots=True)
class QuoteValidationResult:
    """Server-owned derived values and eligibility decision for a quote."""

    total_cents: int
    price_per_person_cents: int
    eligible: bool
    risks: tuple[str, ...] = ()

    @property
    def calculated_total_cents(self) -> int:
        """Explicit alias useful at API boundaries."""

        return self.total_cents


def calculate_quote_total(
    subtotal_cents: int,
    delivery_fee_cents: int = 0,
    other_fee_cents: int = 0,
) -> int:
    """Return a total using integer cents only."""

    amounts = {
        "subtotal_cents": subtotal_cents,
        "delivery_fee_cents": delivery_fee_cents,
        "other_fee_cents": other_fee_cents,
    }
    for field, amount in amounts.items():
        _require_non_negative_integer(amount, field)
    return subtotal_cents + delivery_fee_cents + other_fee_cents


def calculate_price_per_person(total_cents: int, people_count: int) -> int:
    """Return cents/person, rounded half up without using floating point."""

    _require_non_negative_integer(total_cents, "total_cents")
    if isinstance(people_count, bool) or not isinstance(people_count, int):
        raise DomainError(
            ErrorCode.VALIDATION_ERROR,
            "people_count must be an integer",
            details={"field": "people_count"},
        )
    if people_count <= 0:
        raise DomainError(
            ErrorCode.VALIDATION_ERROR,
            "people_count must be greater than zero",
            details={"field": "people_count"},
        )
    quotient, remainder = divmod(total_cents, people_count)
    return quotient + int(remainder * 2 >= people_count)


def validate_required_quote_fields(submission: object) -> tuple[str, ...]:
    """Return missing/empty P0 fields in stable order."""

    missing: list[str] = []
    required_fields = (
        "availability_confirmed",
        "subtotal_cents",
        "delivery_fee_cents",
        "other_fee_cents",
        "total_cents",
        "included_items",
        "invoice_available",
        "vegetarian_status",
        "vegan_status",
        "gluten_free_status",
        "valid_until",
        "supplier_confirmation",
    )
    for field in required_fields:
        if _value(submission, field, _MISSING) is _MISSING:
            missing.append(field)

    for field in _REQUIRED_TEXT_FIELDS:
        value = _value(submission, field, _MISSING)
        if value is _MISSING or not isinstance(value, str) or not value.strip():
            missing.append(field)

    included_items = _value(submission, "included_items", _MISSING)
    if included_items is not _MISSING and (
        not isinstance(included_items, (list, tuple))
        or not included_items
        or any(not isinstance(item, str) or not item.strip() for item in included_items)
    ):
        missing.append("included_items")

    return tuple(dict.fromkeys(missing))


def validate_quote_expiration(valid_until: datetime, now: datetime) -> None:
    """Reject timestamps that are naive or no longer valid."""

    if not isinstance(valid_until, datetime):
        raise DomainError(
            ErrorCode.VALIDATION_ERROR,
            "valid_until must be a datetime",
            details={"field": "valid_until"},
        )
    if not isinstance(now, datetime):
        raise DomainError(
            ErrorCode.VALIDATION_ERROR,
            "now must be a datetime",
            details={"field": "now"},
        )
    if valid_until.tzinfo is None or now.tzinfo is None:
        raise DomainError(
            ErrorCode.VALIDATION_ERROR,
            "quote timestamps must include a timezone",
            details={"field": "valid_until"},
        )
    if valid_until.astimezone(UTC) <= now.astimezone(UTC):
        raise DomainError(
            ErrorCode.QUOTE_EXPIRED,
            "quote validity has expired",
            details={"valid_until": valid_until.isoformat(), "now": now.isoformat()},
        )


def validate_budget_limit(
    total_cents: int,
    requirements: object,
) -> tuple[bool, tuple[str, ...]]:
    """Evaluate the request budget without making a malformed quote invalid."""

    maximum = _value(requirements, "maximum_total_cents", None)
    if maximum is None:
        return True, ()
    _require_non_negative_integer(maximum, "maximum_total_cents")
    if total_cents <= maximum:
        return True, ()
    return False, ("BUDGET_LIMIT_EXCEEDED",)


def validate_invoice_requirement(
    submission: object,
    requirements: object,
) -> tuple[bool, tuple[str, ...]]:
    """Treat false and unknown as unmet when an invoice is mandatory."""

    mandatory = _mandatory_requirements(requirements)
    invoice_required = _value(requirements, "invoice_required", None) is True
    invoice_required = invoice_required or "invoice" in mandatory
    if not invoice_required:
        return True, ()
    if _value(submission, "invoice_available", None) is True:
        return True, ()
    return False, ("INVOICE_REQUIREMENT_NOT_MET",)


def validate_dietary_requirements(
    submission: object,
    requirements: object,
) -> tuple[bool, tuple[str, ...]]:
    """Require ``confirmed`` for every dietary category with requested people."""

    risks: list[str] = []
    categories = (
        ("vegetarian", "vegetarian_count", "vegetarian_status"),
        ("vegan", "vegan_count", "vegan_status"),
        ("gluten_free", "gluten_free_count", "gluten_free_status"),
    )
    for category, count_field, status_field in categories:
        count = _value(requirements, count_field, 0)
        _require_non_negative_integer(count, count_field)
        status = _enum_value(_value(submission, status_field, "unknown"))
        if status not in _KNOWN_DIETARY_STATUSES:
            raise DomainError(
                ErrorCode.VALIDATION_ERROR,
                f"unknown dietary status for {category}",
                details={"field": status_field, "value": status},
            )
        if count > 0 and status != "confirmed":
            risks.append(f"{category.upper()}_REQUIREMENT_NOT_CONFIRMED")

    return not risks, tuple(risks)


def validate_quote_submission(
    submission: object,
    requirements: object,
    now: datetime,
) -> QuoteValidationResult:
    """Validate one submission and return deterministic server-owned values.

    Structural errors, an inconsistent client total, and expiration are rejected
    with ``DomainError``.  A well-formed quote that exceeds commercial or policy
    requirements is retained but marked ineligible with explicit risk codes.
    """

    missing = validate_required_quote_fields(submission)
    if missing:
        raise DomainError(
            ErrorCode.VALIDATION_ERROR,
            "required quote fields are missing or empty",
            details={"fields": list(missing)},
        )

    if _value(submission, "supplier_confirmation", False) is not True:
        raise DomainError(
            ErrorCode.VALIDATION_ERROR,
            "supplier_confirmation must be explicit",
            details={"field": "supplier_confirmation"},
        )

    calculated_total = calculate_quote_total(
        _value(submission, "subtotal_cents"),
        _value(submission, "delivery_fee_cents", 0),
        _value(submission, "other_fee_cents", 0),
    )
    declared_total = _value(submission, "total_cents")
    _require_non_negative_integer(declared_total, "total_cents")
    if declared_total != calculated_total:
        raise DomainError(
            ErrorCode.QUOTE_TOTAL_MISMATCH,
            "declared quote total differs from the server calculation",
            details={
                "declared_total_cents": declared_total,
                "calculated_total_cents": calculated_total,
            },
        )

    validate_quote_expiration(_value(submission, "valid_until"), now)

    people_count = _value(requirements, "people_count")
    price_per_person = calculate_price_per_person(calculated_total, people_count)

    eligible = True
    risks: list[str] = []
    if _value(submission, "availability_confirmed", False) is not True:
        eligible = False
        risks.append("AVAILABILITY_NOT_CONFIRMED")

    for check in (
        validate_budget_limit(calculated_total, requirements),
        validate_invoice_requirement(submission, requirements),
        validate_dietary_requirements(submission, requirements),
    ):
        check_passed, check_risks = check
        eligible = eligible and check_passed
        risks.extend(check_risks)

    gluten_free_count = _value(requirements, "gluten_free_count", 0)
    warning = _value(submission, "cross_contamination_warning", None)
    if gluten_free_count > 0 and (not isinstance(warning, str) or not warning.strip()):
        risks.append("CROSS_CONTAMINATION_INFORMATION_MISSING")

    if _value(requirements, "no_single_use_plastic", None) is True:
        # The v0 quote contract has no explicit packaging confirmation.
        # Preserve uncertainty instead of inferring it from a sustainability score.
        risks.append("NO_SINGLE_USE_PLASTIC_NOT_EXPLICITLY_CONFIRMED")

    return QuoteValidationResult(
        total_cents=calculated_total,
        price_per_person_cents=price_per_person,
        eligible=eligible,
        risks=tuple(dict.fromkeys(risks)),
    )


def _value(source: object, field: str, default: Any = _NO_DEFAULT) -> Any:
    if isinstance(source, Mapping):
        if field in source:
            return source[field]
    elif hasattr(source, field):
        return getattr(source, field)
    if default is _NO_DEFAULT:
        raise DomainError(
            ErrorCode.VALIDATION_ERROR,
            f"missing field: {field}",
            details={"field": field},
        )
    return default


def _require_non_negative_integer(value: object, field: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise DomainError(
            ErrorCode.VALIDATION_ERROR,
            f"{field} must be a non-negative integer",
            details={"field": field, "value": value},
        )


def _mandatory_requirements(requirements: object) -> set[str]:
    values = _value(requirements, "mandatory_requirements", ())
    if not isinstance(values, (list, tuple, set, frozenset)):
        raise DomainError(
            ErrorCode.VALIDATION_ERROR,
            "mandatory_requirements must be a collection",
            details={"field": "mandatory_requirements"},
        )
    return {str(value).strip().lower() for value in values}


def _enum_value(value: object) -> str:
    raw_value = getattr(value, "value", value)
    return str(raw_value).strip().lower()


__all__ = [
    "QuoteValidationResult",
    "calculate_price_per_person",
    "calculate_quote_total",
    "validate_budget_limit",
    "validate_dietary_requirements",
    "validate_invoice_requirement",
    "validate_quote_expiration",
    "validate_quote_submission",
    "validate_required_quote_fields",
]
