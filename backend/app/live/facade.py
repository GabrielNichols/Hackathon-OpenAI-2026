"""Contracts for the real supplier and approver web boundary.

The router deliberately depends on this small facade instead of the in-memory
prototype service.  A production adapter can validate one-time capability
links, persist openings/submissions and call the Dev 4 application services in
a database transaction without coupling the HTTP pages to those details.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date, datetime, time
from typing import Literal, Protocol


@dataclass(frozen=True, slots=True)
class RequestEvidence:
    """Server-observed evidence recorded with a human action."""

    request_id: str
    ip_address: str | None
    user_agent: str | None


@dataclass(frozen=True, slots=True)
class HumanActor:
    """A human resolved by the application's real authentication layer."""

    tenant_id: str
    user_id: str
    display_name: str


@dataclass(frozen=True, slots=True)
class SupplierRFQPage:
    rfq_round_id: str
    supplier_id: str
    supplier_name: str
    response_deadline: datetime
    requirements: Mapping[str, object]
    opened_at: datetime | None = None
    quote_already_submitted: bool = False
    clarification_messages: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True, slots=True)
class QuoteFormSubmission:
    availability_confirmed: bool
    subtotal_cents: int
    delivery_fee_cents: int
    other_fee_cents: int
    total_cents: int
    included_items: tuple[str, ...]
    substitutions: tuple[str, ...]
    invoice_available: bool
    no_single_use_plastic_confirmed: bool
    vegetarian_status: str
    vegan_status: str
    gluten_free_status: str
    cross_contamination_warning: str | None
    valid_until: datetime
    cancellation_terms: str
    respondent_name: str
    respondent_contact: str
    supplier_confirmation: bool


@dataclass(frozen=True, slots=True)
class ApprovalPage:
    approval_id: str
    approval_version: int
    status: str
    procurement_request_id: str
    supplier_name: str
    quote_id: str
    quote_version: int
    total_cents: int
    currency: str
    comparison_summary: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True, slots=True)
class FrozenAwardTerms:
    """Exact allowlisted snapshot the supplier is being asked to accept."""

    quote_id: str
    quote_version: int
    supplier_id: str
    total_cents: int
    currency: str
    included_items: tuple[str, ...]
    substitutions: tuple[str, ...]
    cancellation_terms: str
    event_date: date
    delivery_time: time
    people_count: int


@dataclass(frozen=True, slots=True)
class AwardPage:
    award_id: str
    award_version: int
    status: str
    supplier_name: str
    procurement_request_id: str
    quote_id: str
    quote_version: int
    approved_total_cents: int
    currency: str
    event_date: date
    delivery_window: str
    people_count: int
    reservation_status: str
    terms_snapshot_hash: str
    terms_snapshot: FrozenAwardTerms
    opened_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class ReservationFormSubmission:
    event_date: date
    delivery_window: str
    people_count: int
    confirmed_by: str


@dataclass(frozen=True, slots=True)
class ActionReceipt:
    resource_id: str
    status: str
    message: str


@dataclass(frozen=True, slots=True)
class ManualDeliverySummary:
    """Safe operator listing data; capability URLs are intentionally absent."""

    external_id: str
    kind: str
    supplier_name: str
    delivery_status: str
    created_at: datetime


@dataclass(frozen=True, slots=True)
class ManualDeliveryPage:
    """Safe operator detail data; capability URLs are intentionally absent."""

    external_id: str
    kind: str
    supplier_name: str
    delivery_status: str
    procurement_request_id: str
    created_at: datetime
    opened_at: datetime | None = None
    last_send_channel: str | None = None
    last_recipient_contact: str | None = None


@dataclass(frozen=True, slots=True)
class ManualLinkReveal:
    """Sensitive result returned only after an audited operator POST."""

    external_id: str
    supplier_url: str
    event_type: Literal["LINK_COPIED"] = "LINK_COPIED"


@dataclass(frozen=True, slots=True)
class ManualSendReceipt:
    """Manual send evidence; this action cannot confirm delivery."""

    external_id: str
    delivery_status: Literal["SENT_TO_GATEWAY"]
    message: str
    event_type: Literal["SEND_RECORDED"] = "SEND_RECORDED"


@dataclass(frozen=True, slots=True)
class ManualDeliveryActivity:
    event_type: str
    occurred_at: datetime
    actor_display_name: str
    detail: str


@dataclass(frozen=True, slots=True)
class ManualDeliveryActivityPage:
    external_id: str
    supplier_name: str
    delivery_status: str
    activities: tuple[ManualDeliveryActivity, ...] = field(default_factory=tuple)


@dataclass(frozen=True, slots=True)
class ExecutionEvidenceTimelineItem:
    """Safe, presentation-ready evidence with no capability or request metadata."""

    occurred_at: datetime
    event_type: str
    actor_display_name: str
    source: Literal["DOMAIN", "MANUAL_DELIVERY"]
    detail: str


@dataclass(frozen=True, slots=True)
class ExecutionEvidencePage:
    """Read-only proof that the procurement workflow used real human actions."""

    procurement_request_id: str
    final_status: str
    confirmed_delivery_count: int
    delivery_count: int
    valid_quote_count: int
    quote_count: int
    clarification_count: int
    resolved_clarification_count: int
    approval_status: str | None
    approval_actor_display_name: str | None
    award_status: str | None
    reservation_status: str | None
    comparison_ids: tuple[str, ...] = field(default_factory=tuple)
    timeline: tuple[ExecutionEvidenceTimelineItem, ...] = field(default_factory=tuple)


@dataclass(frozen=True, slots=True)
class ComparisonScoreComponentPage:
    criterion: str
    weight_percent: int
    normalized_score_basis_points: int
    points_basis_points: int
    reason: str | None
    evidence_refs: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True, slots=True)
class ComparisonCandidatePage:
    quote_id: str
    quote_version: int
    supplier_id: str
    supplier_name: str
    eligible: bool
    total_cents: int
    currency: str
    price_per_person_cents: int
    invoice_available: bool | None
    valid_until: datetime | None
    availability_confirmed: bool | None
    no_single_use_plastic_confirmed: bool | None
    vegetarian_status: str | None
    vegan_status: str | None
    gluten_free_status: str | None
    included_items: tuple[str, ...]
    substitutions: tuple[str, ...]
    score_basis_points: int
    score_components: tuple[ComparisonScoreComponentPage, ...]
    disqualification_reasons: tuple[str, ...] = field(default_factory=tuple)
    risks: tuple[str, ...] = field(default_factory=tuple)
    evidence_refs: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True, slots=True)
class ComparisonPage:
    comparison_id: str
    comparison_version: int
    procurement_request_id: str
    rfq_round_id: str
    quote_collection_version: int
    status: str
    recommended_quote_id: str | None
    recommended_quote_version: int | None
    created_at: datetime
    requirements: Mapping[str, object]
    ranking_weights: Mapping[str, int]
    candidates: tuple[ComparisonCandidatePage, ...] = field(default_factory=tuple)


class ApproverAuthenticator(Protocol):
    async def __call__(self, request: object) -> HumanActor:
        """Resolve a logged-in human; raise when authentication fails."""


class OperatorAuthenticator(Protocol):
    async def __call__(self, request: object) -> HumanActor:
        """Resolve an authorized operator; raise when authentication fails."""


class LiveProcurementFacade(Protocol):
    """Application operations used by the live server-rendered pages."""

    async def get_rfq(
        self,
        capability_token: str,
        *,
        evidence: RequestEvidence,
    ) -> SupplierRFQPage:
        """Validate and preview an RFQ capability without confirming delivery."""

    async def open_rfq(
        self,
        capability_token: str,
        *,
        idempotency_key: str,
        evidence: RequestEvidence,
    ) -> ActionReceipt:
        """Persist opening only after the supplier's explicit protected POST."""

    async def submit_quote(
        self,
        capability_token: str,
        submission: QuoteFormSubmission,
        *,
        idempotency_key: str,
        evidence: RequestEvidence,
    ) -> ActionReceipt: ...

    async def get_approval(
        self,
        approval_id: str,
        *,
        actor: HumanActor,
        evidence: RequestEvidence,
    ) -> ApprovalPage: ...

    async def decide_approval(
        self,
        approval_id: str,
        *,
        expected_version: int,
        approve: bool,
        reason: str | None,
        actor: HumanActor,
        idempotency_key: str,
        evidence: RequestEvidence,
    ) -> ActionReceipt:
        """Persist an explicit decision by the authenticated human."""

    async def get_award(
        self,
        capability_token: str,
        *,
        evidence: RequestEvidence,
    ) -> AwardPage:
        """Validate and preview an award capability without confirming delivery."""

    async def open_award(
        self,
        capability_token: str,
        *,
        idempotency_key: str,
        evidence: RequestEvidence,
    ) -> ActionReceipt:
        """Persist opening only after the supplier's explicit protected POST."""

    async def respond_to_award(
        self,
        capability_token: str,
        *,
        accept: bool,
        respondent_name: str,
        reason: str | None,
        terms_accepted: bool,
        terms_snapshot_hash: str,
        idempotency_key: str,
        evidence: RequestEvidence,
    ) -> ActionReceipt: ...

    async def confirm_reservation(
        self,
        capability_token: str,
        submission: ReservationFormSubmission,
        *,
        idempotency_key: str,
        evidence: RequestEvidence,
    ) -> ActionReceipt: ...

    async def list_manual_deliveries(
        self,
        *,
        actor: HumanActor,
        evidence: RequestEvidence,
    ) -> tuple[ManualDeliverySummary, ...]:
        """List operator-visible deliveries without returning capability URLs."""

    async def get_manual_delivery(
        self,
        external_id: str,
        *,
        actor: HumanActor,
        evidence: RequestEvidence,
    ) -> ManualDeliveryPage:
        """Read delivery status without mutating delivery or revealing its URL."""

    async def reveal_manual_delivery_link(
        self,
        external_id: str,
        *,
        actor: HumanActor,
        idempotency_key: str,
        evidence: RequestEvidence,
    ) -> ManualLinkReveal:
        """Persist LINK_COPIED and then return the supplier capability URL."""

    async def record_manual_delivery_sent(
        self,
        external_id: str,
        *,
        channel: str,
        recipient_contact: str,
        actor: HumanActor,
        idempotency_key: str,
        evidence: RequestEvidence,
    ) -> ManualSendReceipt:
        """Persist SEND_RECORDED, never DELIVERED; supplier opening confirms it."""

    async def get_manual_delivery_activity(
        self,
        external_id: str,
        *,
        actor: HumanActor,
        evidence: RequestEvidence,
    ) -> ManualDeliveryActivityPage:
        """Read the immutable activity/status timeline without side effects."""

    async def get_execution_evidence(
        self,
        procurement_request_id: str,
        *,
        actor: HumanActor,
        evidence: RequestEvidence,
    ) -> ExecutionEvidencePage:
        """Read tenant-scoped execution evidence without changing workflow state."""

    async def get_comparison(
        self,
        comparison_id: str,
        *,
        actor: HumanActor,
        evidence: RequestEvidence,
    ) -> ComparisonPage:
        """Read a tenant-scoped deterministic comparison without side effects."""


__all__ = [
    "ActionReceipt",
    "ApprovalPage",
    "ApproverAuthenticator",
    "AwardPage",
    "ComparisonCandidatePage",
    "ComparisonPage",
    "ComparisonScoreComponentPage",
    "ExecutionEvidencePage",
    "ExecutionEvidenceTimelineItem",
    "FrozenAwardTerms",
    "HumanActor",
    "LiveProcurementFacade",
    "ManualDeliveryActivity",
    "ManualDeliveryActivityPage",
    "ManualDeliveryPage",
    "ManualDeliverySummary",
    "ManualLinkReveal",
    "ManualSendReceipt",
    "OperatorAuthenticator",
    "QuoteFormSubmission",
    "RequestEvidence",
    "ReservationFormSubmission",
    "SupplierRFQPage",
]
