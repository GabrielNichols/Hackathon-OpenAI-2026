from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from app.live.application import (
    DurableLiveProcurementFacade,
    DurableProcurementExecutionPort,
)
from app.live.config import LiveSettings
from app.live.facade import (
    HumanActor,
    QuoteFormSubmission,
    RequestEvidence,
    ReservationFormSubmission,
)
from app.live.manual_link_repository import SqlAlchemyManualLinkDeliveryRepository
from app.live.repository import SqlAlchemyExecutionStoreRepository
from app.live.server import create_live_app
from app.modules.messaging.manual_link import ManualLinkDeliveryAdapter
from app.modules.rfq.contracts import (
    CommandContextDTO,
    CompareQuotesCommand,
    CreateRFQRoundCommand,
    ExecutionPolicySnapshotDTO,
    RequestApprovalCommand,
    RFQRequirementsSnapshotDTO,
    SendAwardCommand,
    SendRFQRoundCommand,
)
from app.shared.errors import DomainError, ErrorCode


def _settings(database_path: str) -> LiveSettings:
    return LiveSettings(
        database_url=f"sqlite:///{database_path}",
        public_base_url="https://demo.canal-agente.test",
        token_secret="token-secret-for-live-e2e-is-32-bytes-minimum",
        csrf_secret="csrf-secret-for-live-e2e-is-different-and-long",
        pii_hash_secret="pii-secret-for-live-e2e-is-also-different-and-long",
        operator_user_id="operator-real",
        operator_access_token="operator-access-token-real-1234567890",
        approver_user_id="approver-real",
        approver_access_token="approver-access-token-real-1234567890",
        tenant_id="tenant-real-e2e",
        allow_test_database=True,
    )


def _context(settings: LiveSettings, key: str) -> CommandContextDTO:
    return CommandContextDTO(
        tenant_id=settings.tenant_id,
        idempotency_key=key,
        correlation_id="correlation-real-e2e",
        actor_type="agent",
        actor_id="dev3-real-agent",
        agent_run_id="run-real-e2e",
    )


def _quote(
    *,
    now: datetime,
    total_cents: int,
    respondent_name: str,
    respondent_contact: str,
    invoice_available: bool = True,
) -> QuoteFormSubmission:
    delivery_fee_cents = 20_000
    return QuoteFormSubmission(
        availability_confirmed=True,
        subtotal_cents=total_cents - delivery_fee_cents,
        delivery_fee_cents=delivery_fee_cents,
        other_fee_cents=0,
        total_cents=total_cents,
        included_items=("café", "salgados", "frutas"),
        substitutions=(),
        invoice_available=invoice_available,
        no_single_use_plastic_confirmed=True,
        vegetarian_status="confirmed",
        vegan_status="confirmed",
        gluten_free_status="confirmed",
        cross_contamination_warning="produção separada",
        valid_until=now + timedelta(days=1),
        cancellation_terms="Sem custo até 24 horas antes",
        respondent_name=respondent_name,
        respondent_contact=respondent_contact,
        supplier_confirmation=True,
    )


def _hidden(page: str, name: str) -> str:
    match = re.search(rf'name="{re.escape(name)}" value="([^"]+)"', page)
    assert match is not None
    return match.group(1)


async def _get(app: object, path: str, **kwargs: object) -> httpx.Response:
    transport = httpx.ASGITransport(app=app)  # type: ignore[arg-type]
    async with httpx.AsyncClient(
        transport=transport,
        base_url="https://demo.canal-agente.test",
    ) as client:
        return await client.get(path, **kwargs)


@pytest.mark.asyncio
async def test_real_live_golden_path_survives_restart_without_simulated_dependencies(
    tmp_path,
) -> None:
    database_path = tmp_path / "dev4-live-real.sqlite3"
    settings = _settings(database_path.as_posix())
    app = create_live_app(settings)
    port: DurableProcurementExecutionPort = app.state.execution_port
    facade: DurableLiveProcurementFacade = app.state.live_facade
    runtime = app.state.live_runtime

    # The composition root must be backed by the concrete SQL repositories and
    # the real manual-delivery adapter. SQLite is used only for this test.
    with runtime.uow_factory() as uow:
        manual_repository, gateway, _service = runtime.build(uow)
        assert isinstance(uow.repository, SqlAlchemyExecutionStoreRepository)
        assert isinstance(manual_repository, SqlAlchemyManualLinkDeliveryRepository)
        assert isinstance(gateway, ManualLinkDeliveryAdapter)

    now = datetime.now(UTC)
    event_date = (now + timedelta(days=4)).date()
    procurement_request_id = "procurement-real-e2e"
    created = await port.create_round(
        CreateRFQRoundCommand(
            context=_context(settings, "create-real-rfq"),
            procurement_request_id=procurement_request_id,
            request_version=1,
            plan_version=1,
            recipient_supplier_ids=["supplier-alpha-real", "supplier-beta-real"],
            response_deadline=now + timedelta(days=2),
            requirements=RFQRequirementsSnapshotDTO(
                description="Coffee break corporativo real para 80 pessoas",
                category="corporate_catering",
                event_date=event_date,
                delivery_time="08:30",
                timezone="America/Sao_Paulo",
                location_city="São Paulo",
                location_district="Vila Olímpia",
                people_count=80,
                maximum_total_cents=450_000,
                vegetarian_count=12,
                vegan_count=4,
                gluten_free_count=3,
                invoice_required=True,
                no_single_use_plastic=True,
                mandatory_requirements=["invoice", "dietary_restrictions"],
            ),
            execution_policy=ExecutionPolicySnapshotDTO(
                source_policy_version=1,
                minimum_confirmed_deliveries=2,
                minimum_valid_quotes=2,
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
                approver_user_id=settings.approver_user_id,
            ),
        )
    )
    dispatched = await port.send_round(
        SendRFQRoundCommand(
            context=_context(settings, "send-real-rfq"),
            rfq_round_id=created.rfq_round_id,
            expected_round_version=created.round_version,
            channel="manual_link",
        )
    )
    assert len(dispatched.deliveries) == 2
    assert {item.status for item in dispatched.deliveries} == {"SENT_TO_GATEWAY"}

    operator = HumanActor(
        tenant_id=settings.tenant_id,
        user_id=settings.operator_user_id,
        display_name="Operador real",
    )
    approver = HumanActor(
        tenant_id=settings.tenant_id,
        user_id=settings.approver_user_id,
        display_name="Aprovadora real",
    )
    evidence = RequestEvidence(
        request_id="request-real-e2e",
        ip_address="203.0.113.10",
        user_agent="real-supplier-browser/1.0",
    )
    recipient_contacts = {
        "supplier-alpha-real": "+5511988881111",
        "supplier-beta-real": "compras-beta@example.test",
    }
    rfq_capabilities: dict[str, str] = {}

    for delivery in dispatched.deliveries:
        assert delivery.external_id is not None
        capability = delivery.external_id
        rfq_capabilities[delivery.supplier_id] = capability
        reveal = await facade.reveal_manual_delivery_link(
            capability,
            actor=operator,
            idempotency_key=f"reveal-{delivery.supplier_id}",
            evidence=evidence,
        )
        assert reveal.supplier_url.endswith(f"/{capability}")
        sent = await facade.record_manual_delivery_sent(
            capability,
            channel="whatsapp" if delivery.supplier_id.endswith("alpha-real") else "email",
            recipient_contact=recipient_contacts[delivery.supplier_id],
            actor=operator,
            idempotency_key=f"record-send-{delivery.supplier_id}",
            evidence=evidence,
        )
        assert sent.delivery_status == "SENT_TO_GATEWAY"

        # A real browser GET is deliberately read-only even after SEND_RECORDED.
        preview = await _get(app, f"/live/supplier/rfq/{capability}")
        assert preview.status_code == 200
        assert "Confirmar abertura" in preview.text
        pending = await facade.get_manual_delivery(
            capability,
            actor=operator,
            evidence=evidence,
        )
        assert pending.delivery_status == "SENT_TO_GATEWAY"
        assert pending.opened_at is None

        opened = await facade.open_rfq(
            capability,
            idempotency_key=f"supplier-open-{delivery.supplier_id}",
            evidence=evidence,
        )
        assert opened.status == "DELIVERED"

    delivered = await port.get_delivery_status(created.rfq_round_id)
    assert delivered.confirmed_count == 2
    assert delivered.all_confirmed is True

    alpha_contact = "alpha.sales@example.test"
    beta_contact = "beta.sales@example.test"
    alpha_capability = rfq_capabilities["supplier-alpha-real"]
    clarification = await facade.submit_quote(
        alpha_capability,
        _quote(
            now=now,
            total_cents=420_000,
            respondent_name="Alpha Real",
            respondent_contact=alpha_contact,
            invoice_available=False,
        ),
        idempotency_key="alpha-quote-v1-needs-clarification",
        evidence=evidence,
    )
    assert clarification.status == "NEEDS_CLARIFICATION"
    clarified = await facade.submit_quote(
        alpha_capability,
        _quote(
            now=now,
            total_cents=420_000,
            respondent_name="Alpha Real",
            respondent_contact=alpha_contact,
        ),
        idempotency_key="alpha-quote-v2-final",
        evidence=evidence,
    )
    assert clarified.status == "FINAL"

    beta_capability = rfq_capabilities["supplier-beta-real"]
    beta_quote = await facade.submit_quote(
        beta_capability,
        _quote(
            now=now,
            total_cents=435_000,
            respondent_name="Beta Real",
            respondent_contact=beta_contact,
        ),
        idempotency_key="beta-quote-v1-final",
        evidence=evidence,
    )
    assert beta_quote.status == "FINAL"

    quote_status = await port.get_quote_status(created.rfq_round_id)
    assert quote_status.valid_count == 2
    assert quote_status.needs_clarification_count == 0
    assert quote_status.ready_for_comparison is True
    comparison = await port.compare(
        CompareQuotesCommand(
            context=_context(settings, "compare-real-quotes"),
            procurement_request_id=procurement_request_id,
            rfq_round_id=created.rfq_round_id,
            expected_quote_collection_version=quote_status.collection_version,
        )
    )
    assert comparison.recommended_quote is not None
    comparison_projection = await facade.get_comparison(
        comparison.comparison_id,
        actor=operator,
        evidence=evidence,
    )
    assert comparison_projection.procurement_request_id == procurement_request_id
    assert len(comparison_projection.candidates) == 2
    assert sum(comparison_projection.ranking_weights.values()) == 100
    assert all(
        candidate.invoice_available is True
        and candidate.valid_until is not None
        and candidate.availability_confirmed is True
        and candidate.no_single_use_plastic_confirmed is True
        and candidate.score_components
        and candidate.evidence_refs
        for candidate in comparison_projection.candidates
    )
    with pytest.raises(DomainError) as tenant_error:
        await facade.get_comparison(
            comparison.comparison_id,
            actor=HumanActor(
                tenant_id="another-tenant",
                user_id="operator-real",
                display_name="Cross-tenant operator",
            ),
            evidence=evidence,
        )
    assert tenant_error.value.code == ErrorCode.POLICY_DENIED
    rendered_comparison = await _get(
        app,
        f"/live/operator/comparisons/{comparison.comparison_id}",
        headers={"Authorization": f"Bearer {settings.operator_access_token}"},
    )
    assert rendered_comparison.status_code == 200
    assert rendered_comparison.headers["cache-control"] == "no-store, max-age=0"
    assert "Matriz de propostas" in rendered_comparison.text
    assert "Preço/pessoa" in rendered_comparison.text
    assert "Nota fiscal obrigatória" in rendered_comparison.text
    assert "Score agregado" in rendered_comparison.text
    assert "Componentes do score" in rendered_comparison.text
    assert "supplier-alpha-real" in rendered_comparison.text
    assert "supplier-beta-real" in rendered_comparison.text
    assert "<form" not in rendered_comparison.text

    approval = await port.request_approval(
        RequestApprovalCommand(
            context=_context(settings, "request-real-approval"),
            procurement_request_id=procurement_request_id,
            comparison_id=comparison.comparison_id,
            comparison_version=comparison.comparison_version,
            selected_quote=comparison.recommended_quote,
            approver_user_id=settings.approver_user_id,
        )
    )
    approval_page = await facade.get_approval(
        approval.approval_id,
        actor=approver,
        evidence=evidence,
    )
    assert approval_page.status == "REQUESTED"

    # The live server resolves the human from its configured credential; no
    # actor id from the form is trusted for the binding approval decision.
    authorization = {"Authorization": f"Bearer {settings.approver_access_token}"}
    rendered_approval = await _get(
        app,
        f"/live/approvals/{approval.approval_id}",
        headers=authorization,
    )
    assert rendered_approval.status_code == 200
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport,
        base_url=settings.public_base_url,
    ) as client:
        approval_response = await client.post(
            f"/live/approvals/{approval.approval_id}",
            data={
                "csrf_token": _hidden(rendered_approval.text, "csrf_token"),
                "idempotency_key": _hidden(rendered_approval.text, "idempotency_key"),
                "expected_version": _hidden(rendered_approval.text, "expected_version"),
                "decision": "approve",
                "reason": "Escolha conferida pela aprovadora real",
                "actor_id": "forged-form-actor-must-be-ignored",
            },
            headers={
                **authorization,
                "Origin": settings.public_base_url,
            },
        )
    assert approval_response.status_code == 200
    approved = await port.get_approval_status(approval.approval_id)
    assert approved.status == "APPROVED"
    assert approved.decided_by_user_id == settings.approver_user_id

    award = await port.send_award(
        SendAwardCommand(
            context=_context(settings, "send-real-award"),
            procurement_request_id=procurement_request_id,
            approval_id=approved.approval_id,
            expected_approval_version=approved.approval_version,
        )
    )
    assert award.status == "SENT_TO_GATEWAY"

    deliveries = await facade.list_manual_deliveries(actor=operator, evidence=evidence)
    award_deliveries = [item for item in deliveries if item.kind == "award"]
    assert len(award_deliveries) == 1
    award_capability = award_deliveries[0].external_id
    award_link = await facade.reveal_manual_delivery_link(
        award_capability,
        actor=operator,
        idempotency_key="reveal-real-award",
        evidence=evidence,
    )
    assert award_link.supplier_url.endswith(f"/{award_capability}")
    award_recipient_contact = "+5511988882222"
    award_sent = await facade.record_manual_delivery_sent(
        award_capability,
        channel="whatsapp",
        recipient_contact=award_recipient_contact,
        actor=operator,
        idempotency_key="record-send-real-award",
        evidence=evidence,
    )
    assert award_sent.delivery_status == "SENT_TO_GATEWAY"
    award_preview = await facade.get_award(award_capability, evidence=evidence)
    assert award_preview.opened_at is None
    opened_award = await facade.open_award(
        award_capability,
        idempotency_key="supplier-open-real-award",
        evidence=evidence,
    )
    assert opened_award.status == "DELIVERED"
    award_page = await facade.get_award(award_capability, evidence=evidence)
    assert award_page.status == "DELIVERED"
    assert award_page.terms_snapshot.quote_id == award.approved_quote.quote_id
    assert award_page.terms_snapshot.quote_version == award.approved_quote.quote_version
    assert award_page.terms_snapshot.supplier_id == award.supplier_id
    assert award_page.terms_snapshot.total_cents == award.approved_total_cents
    assert award_page.terms_snapshot.included_items == ("café", "salgados", "frutas")
    assert award_page.terms_snapshot.substitutions == ()
    assert award_page.terms_snapshot.cancellation_terms == ("Sem custo até 24 horas antes")
    rendered_award = await _get(
        app,
        f"/live/supplier/awards/{award_capability}",
    )
    assert rendered_award.status_code == 200
    assert "Termos congelados deste award" in rendered_award.text
    assert "café" in rendered_award.text
    assert "salgados" in rendered_award.text
    assert "frutas" in rendered_award.text
    assert "Sem custo até 24 horas antes" in rendered_award.text
    assert award_page.terms_snapshot_hash in rendered_award.text
    assert _hidden(rendered_award.text, "terms_snapshot_hash") == (award_page.terms_snapshot_hash)

    accepted = await facade.respond_to_award(
        award_capability,
        accept=True,
        respondent_name="Alpha Real",
        reason=None,
        terms_accepted=True,
        terms_snapshot_hash=award_page.terms_snapshot_hash,
        idempotency_key="accept-real-award-terms",
        evidence=evidence,
    )
    assert accepted.status == "ACCEPTED"
    reserved = await facade.confirm_reservation(
        award_capability,
        ReservationFormSubmission(
            event_date=event_date,
            delivery_window="08:30",
            people_count=80,
            confirmed_by="Alpha Real",
        ),
        idempotency_key="confirm-real-capacity",
        evidence=evidence,
    )
    assert reserved.status == "READY_FOR_CONTRACTING"

    # Dispose and reconstruct the complete composition to prove that the final
    # state and evidence live in SQL, rather than in a process-local object.
    app.state.database_engine.dispose()
    restarted_app = create_live_app(settings, create_schema_on_start=False)
    restarted_port: DurableProcurementExecutionPort = restarted_app.state.execution_port
    restarted_facade: DurableLiveProcurementFacade = restarted_app.state.live_facade
    persisted_award = await restarted_port.get_award_status(award.award_id)
    assert persisted_award.ready_for_contracting is True
    assert persisted_award.reservation_status == "CONFIRMED"

    expected_manual_timeline = [
        "LINK_CREATED",
        "LINK_COPIED",
        "SEND_RECORDED",
        "SUPPLIER_OPENED",
    ]
    for capability in [*rfq_capabilities.values(), award_capability]:
        activity = await restarted_facade.get_manual_delivery_activity(
            capability,
            actor=operator,
            evidence=evidence,
        )
        assert activity.delivery_status == "DELIVERED"
        assert [item.event_type for item in activity.activities] == expected_manual_timeline

    internal_response_tokens: list[str] = []
    persisted_pii_hashes: list[str] = []
    with restarted_app.state.live_runtime.uow_factory() as uow:
        assert uow.store is not None
        assert uow.store.procurement_status[procurement_request_id] == ("READY_FOR_CONTRACTING")
        alpha_recipient = next(
            item
            for item in uow.store.recipients.values()
            if item["supplier_id"] == "supplier-alpha-real"
        )
        alpha_persisted_quote = uow.store.quotes[alpha_recipient["quote_id"]]["dto"]
        assert alpha_persisted_quote.quote_version == 2
        internal_response_tokens.extend(
            item["response_token"] for item in uow.store.recipients.values()
        )
        internal_response_tokens.append(uow.store.awards[award.award_id]["response_token"])
        audit_types = {event.event_type for event in uow.store.audit_events}
        assert {
            "CLARIFICATION_ANSWERED",
            "APPROVAL_GRANTED",
            "AWARD_DELIVERY_CONFIRMED",
            "SUPPLIER_ACCEPTED_AWARD",
            "CAPACITY_RESERVED",
            "PROCUREMENT_READY_FOR_CONTRACTING",
        } <= audit_types
        manual_repository, _gateway, _service = restarted_app.state.live_runtime.build(uow)
        for manual_record in await manual_repository.list_records():
            for activity in await manual_repository.list_activities(manual_record.external_id):
                persisted_pii_hashes.extend(
                    str(value)
                    for key, value in activity.metadata.items()
                    if key.endswith("_hmac_sha256")
                )
    assert persisted_pii_hashes

    evidence_projection = await restarted_facade.get_execution_evidence(
        procurement_request_id,
        actor=operator,
        evidence=evidence,
    )
    assert evidence_projection.final_status == "READY_FOR_CONTRACTING"
    assert (
        evidence_projection.confirmed_delivery_count,
        evidence_projection.delivery_count,
    ) == (2, 2)
    assert (evidence_projection.valid_quote_count, evidence_projection.quote_count) == (
        2,
        2,
    )
    assert (
        evidence_projection.resolved_clarification_count,
        evidence_projection.clarification_count,
    ) == (1, 1)
    occurred_at = [item.occurred_at for item in evidence_projection.timeline]
    assert occurred_at == sorted(occurred_at)

    evidence_response = await _get(
        restarted_app,
        f"/live/operator/runs/{procurement_request_id}",
        headers={"Authorization": f"Bearer {settings.operator_access_token}"},
    )
    assert evidence_response.status_code == 200
    assert evidence_response.headers["cache-control"] == "no-store, max-age=0"
    assert "READY_FOR_CONTRACTING" in evidence_response.text
    assert "Entregas confirmadas</dt>\n          <dd>2/2" in evidence_response.text
    assert "Propostas válidas</dt>\n          <dd>2/2" in evidence_response.text
    assert "Clarificações resolvidas</dt>\n          <dd>1/1" in evidence_response.text
    assert "APPROVED" in evidence_response.text
    assert settings.approver_user_id in evidence_response.text
    assert "ACCEPTED" in evidence_response.text
    assert "CONFIRMED" in evidence_response.text
    assert f"/live/operator/comparisons/{comparison.comparison_id}" in evidence_response.text
    timeline_order = [
        evidence_response.text.index(event_type)
        for event_type in (
            "RFQ_ROUND_CREATED",
            "SUPPLIER_OPENED",
            "APPROVAL_GRANTED",
            "CAPACITY_RESERVED",
        )
    ]
    assert timeline_order == sorted(timeline_order)

    assert all(token not in award_link.supplier_url for token in internal_response_tokens)
    forbidden_evidence_values = [
        *internal_response_tokens,
        *persisted_pii_hashes,
        *rfq_capabilities.values(),
        award_capability,
        award_link.supplier_url,
        *recipient_contacts.values(),
        alpha_contact,
        beta_contact,
        award_recipient_contact,
        evidence.ip_address,
        evidence.user_agent,
    ]
    serialized_projection = repr(evidence_projection)
    for forbidden_value in forbidden_evidence_values:
        assert forbidden_value not in serialized_projection
        assert forbidden_value not in evidence_response.text
    restarted_app.state.database_engine.dispose()
    database_bytes = database_path.read_bytes()
    sensitive_values = [
        *internal_response_tokens,
        *recipient_contacts.values(),
        alpha_contact,
        beta_contact,
        award_recipient_contact,
    ]
    for sensitive_value in sensitive_values:
        assert sensitive_value.encode("utf-8") not in database_bytes
