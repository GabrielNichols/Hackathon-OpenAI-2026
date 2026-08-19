"""Deterministic integer-only quote scoring.

Normalized values and final points are represented in basis points (0..10,000).
Every final score is exactly the sum of its returned component points.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from app.shared.errors import DomainError, ErrorCode

MAX_BASIS_POINTS = 10_000
_MISSING = object()


@dataclass(frozen=True, slots=True)
class ScoreComponent:
    criterion: str
    weight: int
    normalized_score_basis_points: int
    points_basis_points: int
    reason: str | None = None
    evidence_refs: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ScoreResult:
    quote_id: str
    quote_version: int
    supplier_id: str
    eligible: bool
    total_cents: int
    price_per_person_cents: int
    score_basis_points: int
    components: tuple[ScoreComponent, ...]
    disqualification_reasons: tuple[str, ...] = ()
    risks: tuple[str, ...] = ()

    @property
    def score_components(self) -> tuple[ScoreComponent, ...]:
        """Alias matching ``QuoteCandidateDTO`` at the contract boundary."""

        return self.components


def score_quotes(quotes: Iterable[object], policy: object) -> list[ScoreResult]:
    """Score and stably order quotes using the policy snapshot.

    Eligible candidates always precede ineligible ones.  Remaining ties use
    total, response time, then supplier ID, so repeated executions are stable.
    """

    quote_list = list(quotes)
    if not quote_list:
        return []
    weights = _validate_weights(_value(policy, "ranking_weights"))
    eligible_totals = [
        _money(_value(quote, "total_cents"), "total_cents")
        for quote in quote_list
        if bool(_value(quote, "eligible", True))
    ]
    comparison_totals = eligible_totals or [
        _money(_value(quote, "total_cents"), "total_cents") for quote in quote_list
    ]
    lowest_total = min(comparison_totals)

    results = [
        score_quote(
            quote,
            policy,
            lowest_total_cents=lowest_total,
            validated_weights=weights,
        )
        for quote in quote_list
    ]
    results.sort(
        key=lambda result: (
            not result.eligible,
            -result.score_basis_points,
            result.total_cents,
            _response_time_for_quote(quote_list, result.quote_id),
            result.supplier_id,
            result.quote_id,
        )
    )
    return results


def score_quote(
    quote: object,
    policy: object,
    *,
    lowest_total_cents: int | None = None,
    validated_weights: Mapping[str, int] | None = None,
) -> ScoreResult:
    """Return one explainable score with integer-only component arithmetic."""

    weights = (
        dict(validated_weights)
        if validated_weights is not None
        else _validate_weights(_value(policy, "ranking_weights"))
    )
    total_cents = _money(_value(quote, "total_cents"), "total_cents")
    price_per_person_cents = _money(
        _value(quote, "price_per_person_cents", 0),
        "price_per_person_cents",
    )
    eligible = bool(_value(quote, "eligible", True))
    price_floor = total_cents if lowest_total_cents is None else lowest_total_cents
    _money(price_floor, "lowest_total_cents")

    components: list[ScoreComponent] = []
    for criterion, weight in weights.items():
        normalized, reason = _normalized_score(
            criterion,
            quote,
            lowest_total_cents=price_floor,
        )
        # A disqualified quote keeps the evidence/normalized values but receives
        # no ranking points, making it impossible to recommend automatically.
        points = _weighted_points(weight, normalized) if eligible else 0
        components.append(
            ScoreComponent(
                criterion=criterion,
                weight=weight,
                normalized_score_basis_points=normalized,
                points_basis_points=points,
                reason=reason,
                evidence_refs=_evidence_refs(quote, criterion),
            )
        )

    score_basis_points = sum(item.points_basis_points for item in components)
    if not 0 <= score_basis_points <= MAX_BASIS_POINTS:
        raise DomainError(
            ErrorCode.VALIDATION_ERROR,
            "calculated score is outside the basis-point range",
            details={"score_basis_points": score_basis_points},
        )

    validation_errors = _string_tuple(
        _value(quote, "validation_errors", ()),
        field="validation_errors",
    )
    risks = _string_tuple(
        _value(quote, "validation_warnings", _value(quote, "risks", ())),
        field="validation_warnings",
    )
    disqualification_reasons = validation_errors
    if not eligible:
        disqualification_reasons = disqualification_reasons or risks or ("QUOTE_NOT_ELIGIBLE",)

    return ScoreResult(
        quote_id=str(_value(quote, "quote_id")),
        quote_version=_positive_integer(
            _value(quote, "quote_version", 1),
            "quote_version",
        ),
        supplier_id=str(_value(quote, "supplier_id")),
        eligible=eligible,
        total_cents=total_cents,
        price_per_person_cents=price_per_person_cents,
        score_basis_points=score_basis_points,
        components=tuple(components),
        disqualification_reasons=disqualification_reasons,
        risks=risks,
    )


def _normalized_score(
    criterion: str,
    quote: object,
    *,
    lowest_total_cents: int,
) -> tuple[int, str]:
    override = _value(quote, f"{criterion}_score_basis_points", None)
    if override is not None:
        return _basis_points(override, f"{criterion}_score_basis_points"), "explicit score"

    normalized_criterion = criterion.strip().lower()
    if normalized_criterion in {"price", "preco", "preço", "total_price"}:
        total = _money(_value(quote, "total_cents"), "total_cents")
        if total == 0:
            score = MAX_BASIS_POINTS if lowest_total_cents == 0 else 0
        else:
            score = min(
                MAX_BASIS_POINTS,
                _divide_half_up(lowest_total_cents * MAX_BASIS_POINTS, total),
            )
        return score, "relative to the lowest eligible total"

    if normalized_criterion in {"restrictions", "restricoes", "restrições"}:
        statuses = [
            _status_score(_value(quote, field, "unknown"))
            for field in (
                "vegetarian_status",
                "vegan_status",
                "gluten_free_status",
            )
        ]
        return _divide_half_up(sum(statuses), len(statuses)), "dietary status coverage"

    if normalized_criterion in {"adequacy", "adequacao", "adequação", "fit"}:
        items = _value(quote, "included_items", ())
        score = MAX_BASIS_POINTS if isinstance(items, (list, tuple)) and items else 0
        return score, "at least one included item"

    if normalized_criterion in {"logistics", "logistica", "logística"}:
        score = MAX_BASIS_POINTS if _value(quote, "availability_confirmed", False) is True else 0
        return score, "availability confirmation"

    if normalized_criterion in {"response", "response_time", "resposta"}:
        minutes = _non_negative_integer(
            _value(quote, "response_time_minutes", 0),
            "response_time_minutes",
        )
        # Linear decay over 24 hours, documented and independent of other quotes.
        score = max(0, MAX_BASIS_POINTS - _divide_half_up(minutes * MAX_BASIS_POINTS, 1_440))
        return score, "linear response-time score over 24 hours"

    if normalized_criterion in {"sustainability", "sustentabilidade"}:
        value = _bounded_integer(
            _value(quote, "sustainability_score", 0),
            0,
            5,
            "sustainability_score",
        )
        return value * 2_000, "supplier sustainability score (0..5)"

    if normalized_criterion in {"documentation", "documentacao", "documentação"}:
        score = MAX_BASIS_POINTS if _value(quote, "invoice_available", None) is True else 0
        return score, "invoice availability"

    if normalized_criterion in {"history", "historico", "histórico"}:
        value = _bounded_integer(_value(quote, "history_score", 0), 0, 5, "history_score")
        return value * 2_000, "supplier history score (0..5)"

    return 0, "criterion has no deterministic v0 evaluator"


def _weighted_points(weight: int, normalized_score: int) -> int:
    # weight is a percentage; e.g. 35 * 10,000 / 100 = 3,500 bp.
    return _divide_half_up(weight * normalized_score, 100)


def _validate_weights(value: object) -> dict[str, int]:
    if not isinstance(value, Mapping) or not value:
        raise DomainError(
            ErrorCode.VALIDATION_ERROR,
            "ranking_weights must be a non-empty mapping",
            details={"field": "ranking_weights"},
        )
    weights: dict[str, int] = {}
    for raw_criterion, raw_weight in value.items():
        criterion = str(raw_criterion).strip()
        if not criterion:
            raise DomainError(
                ErrorCode.VALIDATION_ERROR,
                "ranking criterion cannot be empty",
                details={"field": "ranking_weights"},
            )
        weights[criterion] = _bounded_integer(raw_weight, 0, 100, f"weight:{criterion}")
    if sum(weights.values()) != 100:
        raise DomainError(
            ErrorCode.VALIDATION_ERROR,
            "ranking weights must sum to 100",
            details={"sum": sum(weights.values())},
        )
    return weights


def _evidence_refs(quote: object, criterion: str) -> tuple[str, ...]:
    evidence = _value(quote, "evidence_refs", ())
    if isinstance(evidence, Mapping):
        evidence = evidence.get(criterion, ())
    return _string_tuple(evidence, field="evidence_refs")


def _response_time_for_quote(
    quotes: Iterable[object],
    quote_id: str,
) -> int:
    for quote in quotes:
        if str(_value(quote, "quote_id")) == quote_id:
            return _non_negative_integer(
                _value(quote, "response_time_minutes", 0),
                "response_time_minutes",
            )
    return 0


def _status_score(value: object) -> int:
    raw = str(getattr(value, "value", value)).strip().lower()
    return {
        "confirmed": MAX_BASIS_POINTS,
        "partial": 5_000,
        "unknown": 0,
        "not_available": 0,
    }.get(raw, 0)


def _value(source: object, field: str, default: Any = _MISSING) -> Any:
    if isinstance(source, Mapping):
        if field in source:
            return source[field]
    elif hasattr(source, field):
        return getattr(source, field)
    if default is _MISSING:
        raise DomainError(
            ErrorCode.VALIDATION_ERROR,
            f"missing score input: {field}",
            details={"field": field},
        )
    return default


def _money(value: object, field: str) -> int:
    return _non_negative_integer(value, field)


def _non_negative_integer(value: object, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise DomainError(
            ErrorCode.VALIDATION_ERROR,
            f"{field} must be a non-negative integer",
            details={"field": field, "value": value},
        )
    return value


def _positive_integer(value: object, field: str) -> int:
    result = _non_negative_integer(value, field)
    if result == 0:
        raise DomainError(
            ErrorCode.VALIDATION_ERROR,
            f"{field} must be greater than zero",
            details={"field": field},
        )
    return result


def _bounded_integer(value: object, minimum: int, maximum: int, field: str) -> int:
    result = _non_negative_integer(value, field)
    if not minimum <= result <= maximum:
        raise DomainError(
            ErrorCode.VALIDATION_ERROR,
            f"{field} must be between {minimum} and {maximum}",
            details={"field": field, "value": result},
        )
    return result


def _basis_points(value: object, field: str) -> int:
    return _bounded_integer(value, 0, MAX_BASIS_POINTS, field)


def _divide_half_up(numerator: int, denominator: int) -> int:
    if denominator <= 0:
        raise DomainError(
            ErrorCode.VALIDATION_ERROR,
            "division denominator must be greater than zero",
        )
    quotient, remainder = divmod(numerator, denominator)
    return quotient + int(remainder * 2 >= denominator)


def _string_tuple(value: object, *, field: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, (list, tuple, set, frozenset)):
        raise DomainError(
            ErrorCode.VALIDATION_ERROR,
            f"{field} must be a collection",
            details={"field": field},
        )
    return tuple(str(item) for item in value)


__all__ = [
    "MAX_BASIS_POINTS",
    "ScoreComponent",
    "ScoreResult",
    "score_quote",
    "score_quotes",
]
