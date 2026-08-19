"""Deterministic factories for cross-branch automated tests only."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, time

from app.contracts import (
    QuoteComparisonDTO,
    QuoteComparisonEntryDTO,
    SupplierCandidateDTO,
    SupplierSearchCriteria,
    SupplierState,
)

FIXED_NOW = datetime(2026, 8, 19, 15, tzinfo=UTC)
PEOPLE_COUNT = 80
MAXIMUM_TOTAL_CENTS = 450_000
TARGET_TOTAL_CENTS = 410_000


@dataclass(frozen=True, slots=True)
class FixtureIds:
    org_demo: str = "org_demo"
    buyer_gabriel: str = "buyer_gabriel"
    approver_demo: str = "approver_demo"
    supplier_alpha: str = "sup_alpha"
    supplier_beta: str = "sup_beta"
    pr_demo_coffee_break: str = "pr_demo_coffee_break"
    quote_alpha_v1: str = "quo_alpha_v1"
    quote_beta_v1: str = "quo_beta_v1"


def make_supplier_candidates() -> list[SupplierCandidateDTO]:
    ids = FixtureIds()
    return [
        SupplierCandidateDTO(
            supplier_id=ids.supplier_alpha,
            display_name="Supplier Alpha",
            status=SupplierState.ACTIVE,
            categories=["corporate_catering"],
            service_areas=["Sao Paulo"],
            minimum_people=20,
            maximum_people=200,
            lead_time_hours=24,
            invoice_available=True,
            dietary_capabilities={"vegan": "supported"},
            sustainability_tags=["no_single_use_plastic"],
            last_confirmed_at=FIXED_NOW,
            evidence_refs=["evd_supplier_alpha"],
            missing_fields=[],
        ),
        SupplierCandidateDTO(
            supplier_id=ids.supplier_beta,
            display_name="Supplier Beta",
            status=SupplierState.ACTIVE,
            categories=["corporate_catering"],
            service_areas=["Sao Paulo"],
            minimum_people=30,
            maximum_people=120,
            lead_time_hours=36,
            invoice_available=True,
            dietary_capabilities={"vegan": "supported"},
            sustainability_tags=["no_single_use_plastic"],
            last_confirmed_at=FIXED_NOW,
            evidence_refs=["evd_supplier_beta"],
            missing_fields=[],
        ),
    ]


def make_supplier_search() -> SupplierSearchCriteria:
    return SupplierSearchCriteria(
        tenant_id=FixtureIds().org_demo,
        category="corporate_catering",
        city="Sao Paulo",
        district=None,
        event_date=date(2026, 8, 28),
        delivery_time=time(10, 30),
        people_count=PEOPLE_COUNT,
        invoice_required=True,
        dietary_requirements={"vegan": 4},
        mandatory_tags=["no_single_use_plastic"],
        maximum_lead_time_hours=48,
    )


def make_quote_comparison() -> QuoteComparisonDTO:
    ids = FixtureIds()
    return QuoteComparisonDTO(
        comparison_id="cmp_demo_coffee_break",
        procurement_request_id=ids.pr_demo_coffee_break,
        entries=[
            QuoteComparisonEntryDTO(
                quote_id=ids.quote_alpha_v1,
                quote_version=1,
                supplier_id=ids.supplier_alpha,
                eligible=True,
                total_cents=TARGET_TOTAL_CENTS,
                score=95,
                rank=1,
                reason_codes=["WITHIN_BUDGET"],
                evidence_refs=["evd_quote_alpha"],
            ),
            QuoteComparisonEntryDTO(
                quote_id=ids.quote_beta_v1,
                quote_version=1,
                supplier_id=ids.supplier_beta,
                eligible=True,
                total_cents=430_000,
                score=88,
                rank=2,
                reason_codes=["WITHIN_BUDGET"],
                evidence_refs=["evd_quote_beta"],
            ),
        ],
        recommended_quote_id=ids.quote_alpha_v1,
        generated_at=FIXED_NOW,
        version=1,
    )
