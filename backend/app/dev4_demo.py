"""Minimal HTTP surface for the Dev 4 executable prototype."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict

from app.bootstrap import create_execution_service
from app.modules.rfq.contracts import (
    ApprovalDTO,
    AuditEventDTO,
    AwardDTO,
    CommandContextDTO,
    CompareQuotesCommand,
    CreateRFQRoundCommand,
    DeliveryBatchDTO,
    ExecutionPolicySnapshotDTO,
    QuoteComparisonDTO,
    QuoteDTO,
    QuoteSubmissionDTO,
    RequestApprovalCommand,
    RFQRequirementsSnapshotDTO,
    SendAwardCommand,
    SendRFQRoundCommand,
)
from app.shared.errors import DomainError, ErrorCode

DEMO_NOW = datetime(2026, 8, 19, 15, 0, tzinfo=UTC)
DEMO_REQUEST_ID = "pr_demo_coffee_break"
DEMO_APPROVER_ID = "buyer_gabriel"


class APIModel(BaseModel):
    model_config = ConfigDict(use_enum_values=True)


class HealthResponse(APIModel):
    status: Literal["ok"] = "ok"
    service: str = "canal-agente-dev4"
    prototype: bool = True


class DemoRunResponse(APIModel):
    demo_id: str
    mode: Literal["FAKE_DEMO"]
    simulated_external_actions: Literal[True]
    executed_at: datetime
    procurement_request_id: str
    status: str
    ready_for_contracting: bool
    rfq_round_id: str
    delivery: DeliveryBatchDTO
    quotes: list[QuoteDTO]
    comparison: QuoteComparisonDTO
    approval: ApprovalDTO
    award: AwardDTO
    timeline: list[AuditEventDTO]


class DomainErrorBody(APIModel):
    code: str
    message: str
    retryable: bool
    details: dict[str, Any]


class DomainErrorResponse(APIModel):
    error: DomainErrorBody


DemoRunner = Callable[[], Awaitable[DemoRunResponse]]


def create_app(*, demo_runner: DemoRunner | None = None) -> FastAPI:
    application = FastAPI(
        title="Canal Agente — Procurement Execution Prototype",
        version="0.1.0",
        description=(
            "Dev 4 vertical slice: RFQ, quotes, deterministic comparison, "
            "human approval, award acceptance and capacity reservation."
        ),
    )
    runner = demo_runner or run_canonical_demo

    @application.exception_handler(DomainError)
    async def domain_error_handler(
        _request: Request,
        error: DomainError,
    ) -> JSONResponse:
        status_code = _domain_error_status(error.code)
        body = DomainErrorResponse(
            error=DomainErrorBody(
                code=error.code,
                message=error.message,
                retryable=error.retryable,
                details=error.details,
            )
        )
        return JSONResponse(
            status_code=status_code,
            content=jsonable_encoder(body),
        )

    @application.get(
        "/health",
        response_model=HealthResponse,
        tags=["operations"],
    )
    async def health() -> HealthResponse:
        return HealthResponse()

    @application.post(
        "/api/v1/demo/run",
        response_model=DemoRunResponse,
        responses={
            401: {"model": DomainErrorResponse},
            403: {"model": DomainErrorResponse},
            409: {"model": DomainErrorResponse},
            422: {"model": DomainErrorResponse},
        },
        tags=["demo"],
        summary="Run the deterministic procurement happy path",
    )
    async def run_demo() -> DemoRunResponse:
        return await runner()

    return application


async def run_canonical_demo() -> DemoRunResponse:
    """Execute the canonical Dev 4 flow against explicit in-memory fakes."""

    service = create_execution_service(now=DEMO_NOW, auto_ack=True)
    created = await service.create_round(
        CreateRFQRoundCommand(
            context=_context("demo:rfq:create"),
            procurement_request_id=DEMO_REQUEST_ID,
            request_version=1,
            plan_version=1,
            recipient_supplier_ids=["supplier_alpha", "supplier_beta"],
            response_deadline=DEMO_NOW + timedelta(hours=3),
            requirements=_requirements(),
            execution_policy=_policy(),
        )
    )
    delivery = await service.send_round(
        SendRFQRoundCommand(
            context=_context("demo:rfq:send"),
            rfq_round_id=created.rfq_round_id,
            expected_round_version=created.round_version,
            channel="manual_link",
        )
    )

    response_links = {
        message.supplier_id: message.response_token for message in service.delivery_gateway.messages
    }
    alpha_quote = await service.submit_quote(
        response_links["supplier_alpha"],
        _quote(total_cents=420_000, supplier_name="Alpha"),
    )
    beta_quote = await service.submit_quote(
        response_links["supplier_beta"],
        _quote(total_cents=435_000, supplier_name="Beta"),
    )

    quote_status = await service.get_quote_status(created.rfq_round_id)
    comparison = await service.compare(
        CompareQuotesCommand(
            context=_context("demo:quotes:compare"),
            procurement_request_id=DEMO_REQUEST_ID,
            rfq_round_id=created.rfq_round_id,
            expected_quote_collection_version=quote_status.collection_version,
        )
    )
    if comparison.recommended_quote is None:
        raise DomainError(ErrorCode.INVALID_STATE, "demo produced no recommended quote")

    requested_approval = await service.request_approval(
        RequestApprovalCommand(
            context=_context("demo:approval:request"),
            procurement_request_id=DEMO_REQUEST_ID,
            comparison_id=comparison.comparison_id,
            comparison_version=comparison.comparison_version,
            selected_quote=comparison.recommended_quote,
            approver_user_id=DEMO_APPROVER_ID,
        )
    )
    approved = await service.decide_approval(
        requested_approval.approval_id,
        actor_type="human",
        actor_id=DEMO_APPROVER_ID,
        approve=True,
        idempotency_key="demo:approval:grant",
    )
    delivered_award = await service.send_award(
        SendAwardCommand(
            context=_context("demo:award:send"),
            procurement_request_id=DEMO_REQUEST_ID,
            approval_id=approved.approval_id,
            expected_approval_version=approved.approval_version,
        )
    )

    award_response_token = service.delivery_gateway.messages[-1].response_token
    accepted_award = await service.accept_award(
        award_response_token,
        respondent_name="Alpha",
        idempotency_key="demo:award:accept",
    )
    completed_award = await service.confirm_reservation(
        accepted_award.award_id,
        event_date="2026-08-22",
        delivery_window="08:30",
        people_count=80,
        confirmed_by="Alpha",
        idempotency_key="demo:reservation:confirm",
    )
    status = service.get_procurement_status(DEMO_REQUEST_ID)

    # Keeping this assertion close to the HTTP demo prevents a visually green
    # response from masking a gateway/award regression.
    if delivered_award.status != "DELIVERED" or status != "READY_FOR_CONTRACTING":
        raise DomainError(
            ErrorCode.INVALID_STATE,
            "canonical demo did not complete its externally observable actions",
        )

    return DemoRunResponse(
        demo_id="dev4-canonical-v1",
        mode="FAKE_DEMO",
        simulated_external_actions=True,
        executed_at=DEMO_NOW,
        procurement_request_id=DEMO_REQUEST_ID,
        status=status,
        ready_for_contracting=completed_award.ready_for_contracting,
        rfq_round_id=created.rfq_round_id,
        delivery=delivery,
        quotes=[alpha_quote, beta_quote],
        comparison=comparison,
        approval=approved,
        award=completed_award,
        timeline=list(service.audit_events),
    )


def _context(idempotency_key: str) -> CommandContextDTO:
    return CommandContextDTO(
        tenant_id="org_demo",
        idempotency_key=idempotency_key,
        correlation_id="cor_demo_api",
        actor_type="agent",
        actor_id="agent_demo",
        agent_run_id="run_demo_api",
    )


def _requirements() -> RFQRequirementsSnapshotDTO:
    return RFQRequirementsSnapshotDTO(
        description="Coffee break corporativo para 80 pessoas",
        category="corporate_catering",
        event_date="2026-08-22",
        delivery_time="08:30",
        timezone="America/Sao_Paulo",
        location_city="Sao Paulo",
        location_district="Vila Olimpia",
        people_count=80,
        maximum_total_cents=450_000,
        vegetarian_count=12,
        vegan_count=4,
        gluten_free_count=3,
        invoice_required=True,
        no_single_use_plastic=True,
        mandatory_requirements=["invoice", "dietary_restrictions"],
    )


def _policy() -> ExecutionPolicySnapshotDTO:
    return ExecutionPolicySnapshotDTO(
        source_policy_version=1,
        minimum_confirmed_deliveries=1,
        maximum_total_cents=450_000,
        ranking_weights={
            "price": 35,
            "restrictions": 20,
            "adequacy": 15,
            "logistics": 10,
            "response": 5,
            "sustainability": 5,
            "documentation": 5,
            "history": 5,
        },
        approver_user_id=DEMO_APPROVER_ID,
    )


def _quote(*, total_cents: int, supplier_name: str) -> QuoteSubmissionDTO:
    delivery_fee_cents = 20_000
    return QuoteSubmissionDTO(
        availability_confirmed=True,
        subtotal_cents=total_cents - delivery_fee_cents,
        delivery_fee_cents=delivery_fee_cents,
        other_fee_cents=0,
        total_cents=total_cents,
        included_items=["cafe", "salgados", "frutas"],
        substitutions=[],
        invoice_available=True,
        vegetarian_status="confirmed",
        vegan_status="confirmed",
        gluten_free_status="confirmed",
        cross_contamination_warning="producao separada sem certificacao",
        valid_until=DEMO_NOW + timedelta(hours=2),
        cancellation_terms="Cancelamento sem custo ate 24h antes",
        respondent_name=supplier_name,
        respondent_contact=f"{supplier_name.lower()}@example.test",
        supplier_confirmation=True,
        sustainability_score=5,
        history_score=4,
        response_time_minutes=10,
    )


def _domain_error_status(code: str) -> int:
    if code == ErrorCode.NOT_FOUND:
        return 404
    if code == ErrorCode.POLICY_DENIED:
        return 403
    if code == ErrorCode.INVALID_RESPONSE_TOKEN:
        return 401
    if code in {
        ErrorCode.IDEMPOTENCY_CONFLICT,
        ErrorCode.INVALID_STATE,
        ErrorCode.STALE_VERSION,
    }:
        return 409
    return 422


app = create_app()


__all__ = ["app", "create_app", "run_canonical_demo"]
