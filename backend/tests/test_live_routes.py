from __future__ import annotations

import re
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta

import httpx
import pytest
from fastapi import FastAPI, Request

from app.live.facade import (
    ActionReceipt,
    ApprovalPage,
    AwardPage,
    ComparisonCandidatePage,
    ComparisonPage,
    ComparisonScoreComponentPage,
    ExecutionEvidencePage,
    ExecutionEvidenceTimelineItem,
    FrozenAwardTerms,
    HumanActor,
    ManualDeliveryActivity,
    ManualDeliveryActivityPage,
    ManualDeliveryPage,
    ManualDeliverySummary,
    ManualLinkReveal,
    ManualSendReceipt,
    QuoteFormSubmission,
    RequestEvidence,
    ReservationFormSubmission,
    SupplierRFQPage,
)
from app.live.router import create_live_router
from app.live.security import CsrfProtector

NOW = datetime(2026, 8, 19, 16, 0, tzinfo=UTC)
HUMAN = HumanActor(tenant_id="org-real", user_id="buyer-1", display_name="Ana")
OPERATOR = HumanActor(
    tenant_id="org-real",
    user_id="operator-1",
    display_name="Carlos",
)


class SpyFacade:
    def __init__(self) -> None:
        self.calls: list[tuple[str, object]] = []
        self.rfq_page = SupplierRFQPage(
            rfq_round_id="rfq-real-1",
            supplier_id="supplier-real-1",
            supplier_name="Fornecedor Real",
            response_deadline=NOW + timedelta(hours=4),
            requirements={"Descrição": "Coffee break", "Pessoas": 80},
            opened_at=NOW,
        )
        self.approval_page = ApprovalPage(
            approval_id="approval-1",
            approval_version=3,
            status="REQUESTED",
            procurement_request_id="request-1",
            supplier_name="Fornecedor Real",
            quote_id="quote-1",
            quote_version=2,
            total_cents=420_000,
            currency="BRL",
            comparison_summary=("Primeiro no score determinístico",),
        )
        self.award_page = AwardPage(
            award_id="award-1",
            award_version=1,
            status="DELIVERED",
            supplier_name="Fornecedor Real",
            procurement_request_id="request-1",
            quote_id="quote-1",
            quote_version=2,
            approved_total_cents=420_000,
            currency="BRL",
            event_date=date(2026, 8, 22),
            delivery_window="08:30",
            people_count=80,
            reservation_status="NOT_CREATED",
            terms_snapshot_hash="sha256:award-terms-v1",
            terms_snapshot=FrozenAwardTerms(
                quote_id="quote-1",
                quote_version=2,
                supplier_id="supplier-real-1",
                total_cents=420_000,
                currency="BRL",
                included_items=("Café", "Frutas <script>alert(1)</script>"),
                substitutions=("Leite por bebida vegetal",),
                cancellation_terms="Sem custo até 24h <não executar>",
                event_date=date(2026, 8, 22),
                delivery_time=datetime.strptime("08:30", "%H:%M").time(),
                people_count=80,
            ),
            opened_at=NOW,
        )
        self.manual_delivery = ManualDeliveryPage(
            external_id="manual-real-1",
            kind="RFQ",
            supplier_name="Fornecedor Real",
            delivery_status="SENT_TO_GATEWAY",
            procurement_request_id="request-1",
            created_at=NOW,
        )
        self.execution_evidence = ExecutionEvidencePage(
            procurement_request_id="request-1",
            final_status="READY_FOR_CONTRACTING",
            confirmed_delivery_count=2,
            delivery_count=2,
            valid_quote_count=2,
            quote_count=2,
            clarification_count=1,
            resolved_clarification_count=1,
            approval_status="APPROVED",
            approval_actor_display_name="Ana <Aprovadora>",
            award_status="ACCEPTED",
            reservation_status="CONFIRMED",
            comparison_ids=("comparison-1",),
            timeline=(
                ExecutionEvidenceTimelineItem(
                    occurred_at=NOW,
                    event_type="APPROVAL_GRANTED",
                    actor_display_name="Ana <Aprovadora>",
                    source="DOMAIN",
                    detail="Aprovação humana concedida",
                ),
                ExecutionEvidenceTimelineItem(
                    occurred_at=NOW + timedelta(minutes=1),
                    event_type="SUPPLIER_OPENED",
                    actor_display_name="supplier-real-1",
                    source="MANUAL_DELIVERY",
                    detail="Fornecedor <Real>: abertura confirmada",
                ),
            ),
        )
        self.comparison_page = ComparisonPage(
            comparison_id="comparison-1",
            comparison_version=1,
            procurement_request_id="request-1",
            rfq_round_id="rfq-real-1",
            quote_collection_version=2,
            status="READY",
            recommended_quote_id="quote-1",
            recommended_quote_version=2,
            created_at=NOW,
            requirements={
                "Descrição": "Coffee break <script>não executar</script>",
                "Nota fiscal obrigatória": True,
                "Requisitos obrigatórios": ("invoice", "dietary_restrictions"),
            },
            ranking_weights={"price": 35, "documentation": 5},
            candidates=(
                ComparisonCandidatePage(
                    quote_id="quote-1",
                    quote_version=2,
                    supplier_id="supplier-real-1",
                    supplier_name="Fornecedor <Real>",
                    eligible=True,
                    total_cents=420_000,
                    currency="BRL",
                    price_per_person_cents=5_250,
                    invoice_available=True,
                    valid_until=NOW + timedelta(days=2),
                    availability_confirmed=True,
                    no_single_use_plastic_confirmed=True,
                    vegetarian_status="confirmed",
                    vegan_status="partial",
                    gluten_free_status="confirmed",
                    included_items=("Café", "Frutas <premium>"),
                    substitutions=("Leite vegetal",),
                    score_basis_points=9_125,
                    score_components=(
                        ComparisonScoreComponentPage(
                            criterion="price",
                            weight_percent=35,
                            normalized_score_basis_points=10_000,
                            points_basis_points=3_500,
                            reason="menor preço <auditado>",
                            evidence_refs=("quote:quote-1:v2",),
                        ),
                    ),
                    risks=("VEGAN_PARTIAL <review>",),
                    evidence_refs=("quote:quote-1:v2",),
                ),
            ),
        )
        self.supplier_url = "https://supplier.example/live/supplier/rfq/opaque-real-token"

    async def get_rfq(
        self,
        capability_token: str,
        *,
        evidence: RequestEvidence,
    ) -> SupplierRFQPage:
        self.calls.append(("get_rfq", (capability_token, evidence)))
        return self.rfq_page

    async def open_rfq(
        self,
        capability_token: str,
        *,
        idempotency_key: str,
        evidence: RequestEvidence,
    ) -> ActionReceipt:
        self.calls.append(
            ("open_rfq", (capability_token, idempotency_key, evidence))
        )
        self.rfq_page = replace(self.rfq_page, opened_at=NOW)
        return ActionReceipt("rfq-real-1", "DELIVERED", "Abertura registrada")

    async def submit_quote(
        self,
        capability_token: str,
        submission: QuoteFormSubmission,
        *,
        idempotency_key: str,
        evidence: RequestEvidence,
    ) -> ActionReceipt:
        self.calls.append(
            (
                "submit_quote",
                (capability_token, submission, idempotency_key, evidence),
            )
        )
        return ActionReceipt("quote-1", "FINAL", "Proposta recebida")

    async def get_approval(
        self,
        approval_id: str,
        *,
        actor: HumanActor,
        evidence: RequestEvidence,
    ) -> ApprovalPage:
        self.calls.append(("get_approval", (approval_id, actor, evidence)))
        return self.approval_page

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
        self.calls.append(
            (
                "decide_approval",
                (
                    approval_id,
                    expected_version,
                    approve,
                    reason,
                    actor,
                    idempotency_key,
                    evidence,
                ),
            )
        )
        return ActionReceipt(approval_id, "APPROVED" if approve else "REJECTED", "Ok")

    async def get_award(
        self,
        capability_token: str,
        *,
        evidence: RequestEvidence,
    ) -> AwardPage:
        self.calls.append(("get_award", (capability_token, evidence)))
        return self.award_page

    async def open_award(
        self,
        capability_token: str,
        *,
        idempotency_key: str,
        evidence: RequestEvidence,
    ) -> ActionReceipt:
        self.calls.append(
            ("open_award", (capability_token, idempotency_key, evidence))
        )
        self.award_page = replace(
            self.award_page,
            status="DELIVERED",
            opened_at=NOW,
        )
        return ActionReceipt("award-1", "DELIVERED", "Abertura registrada")

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
    ) -> ActionReceipt:
        self.calls.append(
            (
                "respond_to_award",
                (
                    capability_token,
                    accept,
                    respondent_name,
                    reason,
                    terms_accepted,
                    terms_snapshot_hash,
                    idempotency_key,
                    evidence,
                ),
            )
        )
        return ActionReceipt("award-1", "ACCEPTED" if accept else "DECLINED", "Ok")

    async def confirm_reservation(
        self,
        capability_token: str,
        submission: ReservationFormSubmission,
        *,
        idempotency_key: str,
        evidence: RequestEvidence,
    ) -> ActionReceipt:
        self.calls.append(
            (
                "confirm_reservation",
                (capability_token, submission, idempotency_key, evidence),
            )
        )
        return ActionReceipt("reservation-1", "CONFIRMED", "Reserva persistida")

    async def list_manual_deliveries(
        self,
        *,
        actor: HumanActor,
        evidence: RequestEvidence,
    ) -> tuple[ManualDeliverySummary, ...]:
        self.calls.append(("list_manual_deliveries", (actor, evidence)))
        delivery = self.manual_delivery
        return (
            ManualDeliverySummary(
                external_id=delivery.external_id,
                kind=delivery.kind,
                supplier_name=delivery.supplier_name,
                delivery_status=delivery.delivery_status,
                created_at=delivery.created_at,
            ),
        )

    async def get_manual_delivery(
        self,
        external_id: str,
        *,
        actor: HumanActor,
        evidence: RequestEvidence,
    ) -> ManualDeliveryPage:
        self.calls.append(("get_manual_delivery", (external_id, actor, evidence)))
        return self.manual_delivery

    async def reveal_manual_delivery_link(
        self,
        external_id: str,
        *,
        actor: HumanActor,
        idempotency_key: str,
        evidence: RequestEvidence,
    ) -> ManualLinkReveal:
        self.calls.append(
            (
                "reveal_manual_delivery_link",
                (external_id, actor, idempotency_key, evidence),
            )
        )
        return ManualLinkReveal(external_id=external_id, supplier_url=self.supplier_url)

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
        self.calls.append(
            (
                "record_manual_delivery_sent",
                (
                    external_id,
                    channel,
                    recipient_contact,
                    actor,
                    idempotency_key,
                    evidence,
                ),
            )
        )
        return ManualSendReceipt(
            external_id=external_id,
            delivery_status="SENT_TO_GATEWAY",
            message="Envio declarado pelo operador",
        )

    async def get_manual_delivery_activity(
        self,
        external_id: str,
        *,
        actor: HumanActor,
        evidence: RequestEvidence,
    ) -> ManualDeliveryActivityPage:
        self.calls.append(
            ("get_manual_delivery_activity", (external_id, actor, evidence))
        )
        return ManualDeliveryActivityPage(
            external_id=external_id,
            supplier_name=self.manual_delivery.supplier_name,
            delivery_status=self.manual_delivery.delivery_status,
            activities=(
                ManualDeliveryActivity(
                    event_type="LINK_COPIED",
                    occurred_at=NOW,
                    actor_display_name=OPERATOR.display_name,
                    detail="Link copiado para envio manual",
                ),
                ManualDeliveryActivity(
                    event_type="SEND_RECORDED",
                    occurred_at=NOW + timedelta(minutes=1),
                    actor_display_name=OPERATOR.display_name,
                    detail="Envio por WhatsApp registrado",
                ),
            ),
        )

    async def get_execution_evidence(
        self,
        procurement_request_id: str,
        *,
        actor: HumanActor,
        evidence: RequestEvidence,
    ) -> ExecutionEvidencePage:
        self.calls.append(
            ("get_execution_evidence", (procurement_request_id, actor, evidence))
        )
        return self.execution_evidence

    async def get_comparison(
        self,
        comparison_id: str,
        *,
        actor: HumanActor,
        evidence: RequestEvidence,
    ) -> ComparisonPage:
        self.calls.append(("get_comparison", (comparison_id, actor, evidence)))
        return self.comparison_page


@pytest.fixture
def live_app() -> tuple[FastAPI, SpyFacade]:
    facade = SpyFacade()

    async def authenticate(_request: Request) -> HumanActor:
        return HUMAN

    async def authenticate_operator(_request: Request) -> HumanActor:
        return OPERATOR

    app = FastAPI()
    app.include_router(
        create_live_router(
            facade=facade,
            authenticate_approver=authenticate,
            authenticate_operator=authenticate_operator,
            csrf=CsrfProtector("a-real-demo-csrf-secret-with-at-least-32-bytes"),
        )
    )
    return app, facade


async def _request(app: FastAPI, method: str, path: str, **kwargs: object) -> httpx.Response:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="https://testserver") as client:
        return await client.request(method, path, **kwargs)


def _hidden(page: str, name: str) -> str:
    match = re.search(rf'name="{re.escape(name)}" value="([^"]+)"', page)
    assert match is not None
    return match.group(1)


def _hidden_values(page: str, name: str) -> list[str]:
    values = re.findall(rf'name="{re.escape(name)}" value="([^"]+)"', page)
    assert values
    return values


def _quote_form(page: str) -> dict[str, str]:
    return {
        "csrf_token": _hidden(page, "csrf_token"),
        "idempotency_key": _hidden(page, "idempotency_key"),
        "availability_confirmed": "true",
        "subtotal_cents": "400000",
        "delivery_fee_cents": "20000",
        "other_fee_cents": "0",
        "total_cents": "420000",
        "included_items": "Café\nFrutas",
        "substitutions": "",
        "invoice_available": "true",
        "no_single_use_plastic_confirmed": "true",
        "vegetarian_status": "confirmed",
        "vegan_status": "confirmed",
        "gluten_free_status": "confirmed",
        "cross_contamination_warning": "Produção separada",
        "valid_until": "2026-08-21T18:00:00-03:00",
        "cancellation_terms": "Sem custo até 24 horas antes",
        "respondent_name": "João",
        "respondent_contact": "joao@fornecedor.example",
        "supplier_confirmation": "true",
    }


@pytest.mark.asyncio
async def test_operator_gets_are_read_only_and_never_expose_supplier_link(live_app):
    app, facade = live_app

    listing = await _request(app, "GET", "/live/operator/deliveries")
    detail = await _request(
        app,
        "GET",
        "/live/operator/deliveries/manual-real-1",
    )

    assert listing.status_code == 200
    assert detail.status_code == 200
    assert facade.supplier_url not in listing.text
    assert facade.supplier_url not in detail.text
    assert [name for name, _ in facade.calls] == [
        "list_manual_deliveries",
        "get_manual_delivery",
    ]
    assert detail.headers["cache-control"] == "no-store, max-age=0"
    assert "Registrar e revelar link" in detail.text


@pytest.mark.asyncio
async def test_operator_execution_evidence_is_read_only_escaped_and_no_store(live_app):
    app, facade = live_app

    response = await _request(
        app,
        "GET",
        "/live/operator/runs/request-1",
        headers={"User-Agent": "private-user-agent-must-not-render"},
    )

    assert response.status_code == 200
    assert "READY_FOR_CONTRACTING" in response.text
    assert "2/2" in response.text
    assert "1/1" in response.text
    assert "Ana &lt;Aprovadora&gt;" in response.text
    assert "Fornecedor &lt;Real&gt;" in response.text
    assert "Ana <Aprovadora>" not in response.text
    assert facade.supplier_url not in response.text
    assert "opaque-real-token" not in response.text
    assert "private-user-agent-must-not-render" not in response.text
    assert response.headers["cache-control"] == "no-store, max-age=0"
    assert [name for name, _ in facade.calls] == ["get_execution_evidence"]
    _, (request_id, actor, evidence) = facade.calls[0]
    assert request_id == "request-1"
    assert actor == OPERATOR
    assert evidence.user_agent == "private-user-agent-must-not-render"


@pytest.mark.asyncio
async def test_operator_comparison_is_read_only_complete_escaped_and_no_store(live_app):
    app, facade = live_app

    response = await _request(
        app,
        "GET",
        "/live/operator/comparisons/comparison-1",
        headers={"User-Agent": "comparison-private-agent"},
    )

    assert response.status_code == 200
    assert "Matriz de propostas" in response.text
    assert "Preço/pessoa" in response.text
    assert "Nota fiscal obrigatória" in response.text
    assert "Score agregado" in response.text
    assert "91.25%" in response.text
    assert "35%" in response.text
    assert "quote:quote-1:v2" in response.text
    assert "Fornecedor &lt;Real&gt;" in response.text
    assert "Coffee break &lt;script&gt;não executar&lt;/script&gt;" in response.text
    assert "Frutas &lt;premium&gt;" in response.text
    assert "menor preço &lt;auditado&gt;" in response.text
    assert "<script>não executar</script>" not in response.text
    assert facade.supplier_url not in response.text
    assert "opaque-real-token" not in response.text
    assert "comparison-private-agent" not in response.text
    assert "<form" not in response.text
    assert response.headers["cache-control"] == "no-store, max-age=0"
    assert [name for name, _ in facade.calls] == ["get_comparison"]
    _, (comparison_id, actor, evidence) = facade.calls[0]
    assert comparison_id == "comparison-1"
    assert actor == OPERATOR
    assert evidence.user_agent == "comparison-private-agent"


@pytest.mark.asyncio
async def test_operator_link_appears_only_after_audited_copy_post(live_app):
    app, facade = live_app
    detail = await _request(
        app,
        "GET",
        "/live/operator/deliveries/manual-real-1",
    )
    assert facade.supplier_url not in detail.text

    response = await _request(
        app,
        "POST",
        "/live/operator/deliveries/manual-real-1/reveal",
        data={
            "csrf_token": _hidden_values(detail.text, "csrf_token")[0],
            "idempotency_key": _hidden_values(detail.text, "idempotency_key")[0],
            "operator_id": "forged-user-from-form",
        },
        headers={"Origin": "https://testserver"},
    )

    assert response.status_code == 200
    assert facade.supplier_url in response.text
    assert "LINK_COPIED" in response.text
    assert [name for name, _ in facade.calls] == [
        "get_manual_delivery",
        "reveal_manual_delivery_link",
    ]
    _, (external_id, actor, _, evidence) = facade.calls[-1]
    assert external_id == "manual-real-1"
    assert actor == OPERATOR
    assert evidence.request_id


@pytest.mark.asyncio
async def test_record_sent_uses_authenticated_operator_and_never_marks_delivered(live_app):
    app, facade = live_app
    detail = await _request(
        app,
        "GET",
        "/live/operator/deliveries/manual-real-1",
    )

    response = await _request(
        app,
        "POST",
        "/live/operator/deliveries/manual-real-1/sent",
        data={
            "csrf_token": _hidden_values(detail.text, "csrf_token")[1],
            "idempotency_key": _hidden_values(detail.text, "idempotency_key")[1],
            "channel": "whatsapp",
            "recipient_contact": "+5511999999999",
            "operator_id": "forged-user-from-form",
        },
        headers={"Origin": "https://testserver"},
    )

    assert response.status_code == 200
    assert "SEND_RECORDED" in response.text
    assert "SENT_TO_GATEWAY" in response.text
    assert "DELIVERED" not in response.text
    assert [name for name, _ in facade.calls] == [
        "get_manual_delivery",
        "record_manual_delivery_sent",
    ]
    _, (external_id, channel, contact, actor, _, _) = facade.calls[-1]
    assert (external_id, channel, contact) == (
        "manual-real-1",
        "whatsapp",
        "+5511999999999",
    )
    assert actor == OPERATOR


@pytest.mark.asyncio
async def test_operator_csrf_is_bound_to_exact_action(live_app):
    app, facade = live_app
    detail = await _request(
        app,
        "GET",
        "/live/operator/deliveries/manual-real-1",
    )

    response = await _request(
        app,
        "POST",
        "/live/operator/deliveries/manual-real-1/sent",
        data={
            "csrf_token": _hidden_values(detail.text, "csrf_token")[0],
            "idempotency_key": _hidden_values(detail.text, "idempotency_key")[0],
            "channel": "email",
            "recipient_contact": "supplier@example.test",
        },
        headers={"Origin": "https://testserver"},
    )

    assert response.status_code == 403
    assert [name for name, _ in facade.calls] == ["get_manual_delivery"]


@pytest.mark.asyncio
async def test_operator_activity_page_is_read_only_and_shows_real_status(live_app):
    app, facade = live_app

    response = await _request(
        app,
        "GET",
        "/live/operator/deliveries/manual-real-1/activity",
    )

    assert response.status_code == 200
    assert "SENT_TO_GATEWAY" in response.text
    assert "LINK_COPIED" in response.text
    assert "SEND_RECORDED" in response.text
    assert facade.supplier_url not in response.text
    assert [name for name, _ in facade.calls] == ["get_manual_delivery_activity"]


@pytest.mark.asyncio
async def test_rfq_get_is_read_only_after_supplier_has_opened(live_app):
    app, facade = live_app
    token = "opaque-rfq-capability"

    response = await _request(app, "GET", f"/live/supplier/rfq/{token}")

    assert response.status_code == 200
    assert [name for name, _ in facade.calls] == ["get_rfq"]
    assert token not in response.text
    assert response.headers["cache-control"] == "no-store, max-age=0"
    assert response.headers["referrer-policy"] == "no-referrer"
    assert response.headers["x-frame-options"] == "DENY"
    assert "form-action 'self'" in response.headers["content-security-policy"]


@pytest.mark.asyncio
async def test_rfq_crawler_get_never_opens_and_interstitial_post_is_protected(live_app):
    app, facade = live_app
    token = "opaque-rfq-capability"
    facade.rfq_page = replace(facade.rfq_page, opened_at=None)

    interstitial = await _request(app, "GET", f"/live/supplier/rfq/{token}")

    assert interstitial.status_code == 200
    assert "Confirmar abertura" in interstitial.text
    assert "Enviar proposta" not in interstitial.text
    assert [name for name, _ in facade.calls] == ["get_rfq"]

    form = {
        "csrf_token": _hidden(interstitial.text, "csrf_token"),
        "idempotency_key": _hidden(interstitial.text, "idempotency_key"),
    }
    cross_origin = await _request(
        app,
        "POST",
        f"/live/supplier/rfq/{token}/open",
        data=form,
        headers={"Origin": "https://crawler.example"},
    )
    invalid_csrf = await _request(
        app,
        "POST",
        f"/live/supplier/rfq/{token}/open",
        data={**form, "csrf_token": "tampered"},
        headers={"Origin": "https://testserver"},
    )

    assert cross_origin.status_code == 403
    assert invalid_csrf.status_code == 403
    assert [name for name, _ in facade.calls] == ["get_rfq"]


@pytest.mark.asyncio
async def test_explicit_rfq_open_posts_once_then_get_only_reads(live_app):
    app, facade = live_app
    token = "opaque-rfq-capability"
    facade.rfq_page = replace(facade.rfq_page, opened_at=None)
    interstitial = await _request(app, "GET", f"/live/supplier/rfq/{token}")

    opened = await _request(
        app,
        "POST",
        f"/live/supplier/rfq/{token}/open",
        data={
            "csrf_token": _hidden(interstitial.text, "csrf_token"),
            "idempotency_key": _hidden(interstitial.text, "idempotency_key"),
        },
        headers={"Origin": "https://testserver"},
    )

    assert opened.status_code == 303
    assert opened.headers["location"] == f"/live/supplier/rfq/{token}"
    assert [name for name, _ in facade.calls] == ["get_rfq", "open_rfq"]

    page = await _request(app, "GET", opened.headers["location"])

    assert page.status_code == 200
    assert "Enviar proposta" in page.text
    assert [name for name, _ in facade.calls] == [
        "get_rfq",
        "open_rfq",
        "get_rfq",
    ]


@pytest.mark.asyncio
async def test_quote_requires_an_explicit_csrf_protected_post(live_app):
    app, facade = live_app
    token = "opaque-rfq-capability"
    page = await _request(app, "GET", f"/live/supplier/rfq/{token}")

    response = await _request(
        app,
        "POST",
        f"/live/supplier/rfq/{token}",
        data=_quote_form(page.text),
        headers={"Origin": "https://testserver"},
    )

    assert response.status_code == 200
    assert [name for name, _ in facade.calls] == ["get_rfq", "submit_quote"]
    _, (_, submission, _, evidence) = facade.calls[-1]
    assert submission.total_cents == 420_000
    assert submission.included_items == ("Café", "Frutas")
    assert submission.no_single_use_plastic_confirmed is True
    assert evidence.request_id


@pytest.mark.asyncio
async def test_invalid_csrf_and_cross_origin_posts_never_reach_facade(live_app):
    app, facade = live_app
    token = "opaque-rfq-capability"
    page = await _request(app, "GET", f"/live/supplier/rfq/{token}")
    form = _quote_form(page.text)
    form["csrf_token"] = "tampered"

    invalid_csrf = await _request(
        app,
        "POST",
        f"/live/supplier/rfq/{token}",
        data=form,
        headers={"Origin": "https://testserver"},
    )
    cross_origin = await _request(
        app,
        "POST",
        f"/live/supplier/rfq/{token}",
        data=_quote_form(page.text),
        headers={"Origin": "https://attacker.example"},
    )

    assert invalid_csrf.status_code == 403
    assert cross_origin.status_code == 403
    assert [name for name, _ in facade.calls] == ["get_rfq"]


@pytest.mark.asyncio
async def test_approval_get_does_not_decide_and_post_uses_authenticated_human(live_app):
    app, facade = live_app
    page = await _request(app, "GET", "/live/approvals/approval-1")

    assert [name for name, _ in facade.calls] == ["get_approval"]
    response = await _request(
        app,
        "POST",
        "/live/approvals/approval-1",
        data={
            "csrf_token": _hidden(page.text, "csrf_token"),
            "idempotency_key": _hidden(page.text, "idempotency_key"),
            "expected_version": _hidden(page.text, "expected_version"),
            "decision": "approve",
            "reason": "Dentro da política",
            "actor_id": "forged-agent-id",
        },
        headers={"Origin": "https://testserver"},
    )

    assert response.status_code == 200
    assert [name for name, _ in facade.calls] == ["get_approval", "decide_approval"]
    _, (_, version, approve, _, actor, _, _) = facade.calls[-1]
    assert version == 3
    assert approve is True
    assert actor == HUMAN


@pytest.mark.asyncio
async def test_award_crawler_get_never_opens_or_accepts(live_app):
    app, facade = live_app
    token = "opaque-award-capability"
    facade.award_page = replace(
        facade.award_page,
        status="SENT_TO_GATEWAY",
        opened_at=None,
    )

    interstitial = await _request(
        app,
        "GET",
        f"/live/supplier/awards/{token}",
    )

    assert interstitial.status_code == 200
    assert "Confirmar abertura" in interstitial.text
    assert "Aceitar award" not in interstitial.text
    assert [name for name, _ in facade.calls] == ["get_award"]

    opened = await _request(
        app,
        "POST",
        f"/live/supplier/awards/{token}/open",
        data={
            "csrf_token": _hidden(interstitial.text, "csrf_token"),
            "idempotency_key": _hidden(interstitial.text, "idempotency_key"),
        },
        headers={"Origin": "https://testserver"},
    )

    assert opened.status_code == 303
    assert [name for name, _ in facade.calls] == ["get_award", "open_award"]

    page = await _request(app, "GET", opened.headers["location"])

    assert "Aceitar award" in page.text
    assert [name for name, _ in facade.calls] == [
        "get_award",
        "open_award",
        "get_award",
    ]


@pytest.mark.asyncio
async def test_award_displays_complete_frozen_terms_safely_before_acceptance(live_app):
    app, _facade = live_app

    page = await _request(
        app,
        "GET",
        "/live/supplier/awards/opaque-award-capability",
    )

    assert page.status_code == 200
    assert "Termos congelados deste award" in page.text
    assert "quote-1" in page.text
    assert "supplier-real-1" in page.text
    assert "BRL 4.200,00" in page.text
    assert "2026-08-22" in page.text
    assert "08:30:00" in page.text
    assert "80" in page.text
    assert "Café" in page.text
    assert "Leite por bebida vegetal" in page.text
    assert "Sem custo até 24h &lt;não executar&gt;" in page.text
    assert "Frutas &lt;script&gt;alert(1)&lt;/script&gt;" in page.text
    assert "sha256:award-terms-v1" in page.text
    assert "Frutas <script>alert(1)</script>" not in page.text
    assert "Sem custo até 24h <não executar>" not in page.text
    assert _hidden(page.text, "terms_snapshot_hash") == "sha256:award-terms-v1"


@pytest.mark.asyncio
async def test_award_open_does_not_accept_and_accept_does_not_reserve(live_app):
    app, facade = live_app
    token = "opaque-award-capability"
    page = await _request(app, "GET", f"/live/supplier/awards/{token}")

    assert [name for name, _ in facade.calls] == ["get_award"]
    response = await _request(
        app,
        "POST",
        f"/live/supplier/awards/{token}",
        data={
            "csrf_token": _hidden(page.text, "csrf_token"),
            "idempotency_key": _hidden(page.text, "idempotency_key"),
            "action": "award-response",
            "respondent_name": "João",
            "reason": "",
            "terms_accepted": "true",
            "terms_snapshot_hash": _hidden(page.text, "terms_snapshot_hash"),
        },
        headers={"Origin": "https://testserver"},
    )

    assert response.status_code == 200
    assert [name for name, _ in facade.calls] == ["get_award", "respond_to_award"]
    _, (_, accept, _, _, terms_accepted, terms_hash, _, _) = facade.calls[-1]
    assert accept is True
    assert terms_accepted is True
    assert terms_hash == "sha256:award-terms-v1"


@pytest.mark.asyncio
async def test_award_accept_requires_terms_and_hash_is_bound_to_form_proof(live_app):
    app, facade = live_app
    token = "opaque-award-capability"
    page = await _request(app, "GET", f"/live/supplier/awards/{token}")
    base_form = {
        "csrf_token": _hidden(page.text, "csrf_token"),
        "idempotency_key": _hidden(page.text, "idempotency_key"),
        "action": "award-response",
        "respondent_name": "João",
        "reason": "",
        "terms_snapshot_hash": _hidden(page.text, "terms_snapshot_hash"),
    }

    missing_acceptance = await _request(
        app,
        "POST",
        f"/live/supplier/awards/{token}",
        data=base_form,
        headers={"Origin": "https://testserver"},
    )
    tampered_hash = await _request(
        app,
        "POST",
        f"/live/supplier/awards/{token}",
        data={
            **base_form,
            "terms_accepted": "true",
            "terms_snapshot_hash": "sha256:attacker-terms",
        },
        headers={"Origin": "https://testserver"},
    )

    assert missing_acceptance.status_code == 422
    assert tampered_hash.status_code == 403
    assert [name for name, _ in facade.calls] == ["get_award"]


@pytest.mark.asyncio
async def test_supplier_can_explicitly_decline_award(live_app):
    app, facade = live_app
    token = "opaque-award-capability"
    page = await _request(app, "GET", f"/live/supplier/awards/{token}")

    response = await _request(
        app,
        "POST",
        f"/live/supplier/awards/{token}",
        data={
            "csrf_token": _hidden(page.text, "csrf_token"),
            "idempotency_key": _hidden(page.text, "idempotency_key"),
            "action": "award-decline",
            "respondent_name": "João",
            "reason": "Capacidade indisponível",
            "terms_snapshot_hash": _hidden(page.text, "terms_snapshot_hash"),
        },
        headers={"Origin": "https://testserver"},
    )

    assert response.status_code == 200
    _, (_, accept, _, reason, terms_accepted, _, _, _) = facade.calls[-1]
    assert accept is False
    assert reason == "Capacidade indisponível"
    assert terms_accepted is False


@pytest.mark.asyncio
async def test_reservation_is_a_separate_supplier_confirmation(live_app):
    app, facade = live_app
    token = "opaque-award-capability"
    facade.award_page = replace(
        facade.award_page,
        status="ACCEPTED",
        reservation_status="PENDING",
    )
    page = await _request(app, "GET", f"/live/supplier/awards/{token}")

    response = await _request(
        app,
        "POST",
        f"/live/supplier/awards/{token}",
        data={
            "csrf_token": _hidden(page.text, "csrf_token"),
            "idempotency_key": _hidden(page.text, "idempotency_key"),
            "action": "reservation",
            "event_date": "2026-08-22",
            "delivery_window": "08:30",
            "people_count": "80",
            "confirmed_by": "João",
        },
        headers={"Origin": "https://testserver"},
    )

    assert response.status_code == 200
    assert [name for name, _ in facade.calls] == ["get_award", "confirm_reservation"]
    _, (_, reservation, _, _) = facade.calls[-1]
    assert reservation.event_date == date(2026, 8, 22)
    assert reservation.people_count == 80


@pytest.mark.asyncio
async def test_live_routes_fail_closed_on_plain_http(live_app):
    app, facade = live_app
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.get("/live/supplier/rfq/token")

    assert response.status_code == 400
    assert facade.calls == []
