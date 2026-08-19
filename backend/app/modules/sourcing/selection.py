from __future__ import annotations

from collections.abc import Iterable

from app.modules.sourcing.models import EligibilityDecision, SupplierEligibilityResult


def select_rfq_recipients(
    results: Iterable[SupplierEligibilityResult],
    limit: int,
) -> list[str]:
    """Select only unanimously eligible IDs, independent of directory result order."""

    if limit < 0:
        raise ValueError("limit cannot be negative")

    decisions_by_supplier: dict[str, set[EligibilityDecision]] = {}
    for result in results:
        decisions_by_supplier.setdefault(result.supplier_id, set()).add(result.decision)

    eligible = [
        supplier_id
        for supplier_id, decisions in decisions_by_supplier.items()
        if decisions == {EligibilityDecision.ELIGIBLE}
    ]
    eligible.sort(key=lambda supplier_id: (supplier_id.casefold(), supplier_id))
    return eligible[:limit]


class DeterministicSupplierSelector:
    def select(
        self,
        results: Iterable[SupplierEligibilityResult],
        limit: int,
    ) -> list[str]:
        return select_rfq_recipients(results, limit)


__all__ = ["DeterministicSupplierSelector", "select_rfq_recipients"]
