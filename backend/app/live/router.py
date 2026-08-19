"""Server-rendered supplier and approver routes for a live Dev 4 demo.

There is intentionally no JavaScript and no automatic business transition in
this module.  Opening a capability link records only the opening.  Quote
submission, approval, award response and reservation each require a distinct
human POST protected by a short-lived action-bound CSRF proof.
"""

from __future__ import annotations

import html
import secrets
from collections.abc import Mapping
from datetime import date, datetime
from typing import NoReturn
from urllib.parse import parse_qs, quote, urlsplit
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from app.live.facade import (
    ActionReceipt,
    ApprovalPage,
    ApproverAuthenticator,
    AwardPage,
    ComparisonPage,
    ExecutionEvidencePage,
    LiveProcurementFacade,
    ManualDeliveryActivityPage,
    ManualDeliveryPage,
    ManualDeliverySummary,
    ManualLinkReveal,
    ManualSendReceipt,
    OperatorAuthenticator,
    QuoteFormSubmission,
    RequestEvidence,
    ReservationFormSubmission,
    SupplierRFQPage,
)
from app.live.security import CsrfProtector, CsrfValidationError, capability_fingerprint

_MAX_FORM_BYTES = 64 * 1024
_DIETARY_STATUSES = {"confirmed", "partial", "unknown", "not_available"}
_MANUAL_CHANNELS = {"email", "whatsapp"}
_SECURITY_HEADERS = {
    "Cache-Control": "no-store, max-age=0",
    "Content-Security-Policy": (
        "default-src 'none'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'; "
        "style-src 'unsafe-inline'"
    ),
    "Cross-Origin-Opener-Policy": "same-origin",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
    "Referrer-Policy": "no-referrer",
    "Strict-Transport-Security": "max-age=31536000; includeSubDomains",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
}


def create_live_router(
    *,
    facade: LiveProcurementFacade,
    authenticate_approver: ApproverAuthenticator,
    authenticate_operator: OperatorAuthenticator,
    csrf: CsrfProtector,
    require_https: bool = True,
) -> APIRouter:
    """Build routes around injected, real persistence/authentication adapters.

    There are no permissive defaults: callers must supply the application
    facade, a human authenticator and a CSRF secret.  ``require_https`` should
    only be disabled by local integration tests.
    """

    router = APIRouter(prefix="/live", tags=["live-human-workflow"])

    @router.get("/operator/deliveries", response_class=HTMLResponse)
    async def list_operator_deliveries(request: Request) -> HTMLResponse:
        _enforce_https(request, require_https)
        actor = await authenticate_operator(request)
        deliveries = await facade.list_manual_deliveries(
            actor=actor,
            evidence=_evidence(request),
        )
        return _secure_html(_operator_deliveries_page(deliveries))

    @router.get(
        "/operator/runs/{procurement_request_id}",
        response_class=HTMLResponse,
    )
    async def view_execution_evidence(
        procurement_request_id: str,
        request: Request,
    ) -> HTMLResponse:
        _enforce_https(request, require_https)
        actor = await authenticate_operator(request)
        page = await facade.get_execution_evidence(
            procurement_request_id,
            actor=actor,
            evidence=_evidence(request),
        )
        return _secure_html(_execution_evidence_page(page))

    @router.get(
        "/operator/comparisons/{comparison_id}",
        response_class=HTMLResponse,
    )
    async def view_operator_comparison(
        comparison_id: str,
        request: Request,
    ) -> HTMLResponse:
        _enforce_https(request, require_https)
        actor = await authenticate_operator(request)
        page = await facade.get_comparison(
            comparison_id,
            actor=actor,
            evidence=_evidence(request),
        )
        return _secure_html(_comparison_page(page))

    @router.get(
        "/operator/deliveries/{external_id}",
        response_class=HTMLResponse,
    )
    async def view_operator_delivery(external_id: str, request: Request) -> HTMLResponse:
        _enforce_https(request, require_https)
        actor = await authenticate_operator(request)
        page = await facade.get_manual_delivery(
            external_id,
            actor=actor,
            evidence=_evidence(request),
        )
        reveal_idempotency = secrets.token_urlsafe(24)
        sent_idempotency = secrets.token_urlsafe(24)
        reveal_csrf = csrf.issue(
            _operator_action_context(
                "reveal",
                actor.tenant_id,
                actor.user_id,
                external_id,
                reveal_idempotency,
            )
        )
        sent_csrf = csrf.issue(
            _operator_action_context(
                "record-sent",
                actor.tenant_id,
                actor.user_id,
                external_id,
                sent_idempotency,
            )
        )
        return _secure_html(
            _operator_delivery_page(
                page,
                reveal_csrf=reveal_csrf,
                reveal_idempotency=reveal_idempotency,
                sent_csrf=sent_csrf,
                sent_idempotency=sent_idempotency,
            )
        )

    @router.post(
        "/operator/deliveries/{external_id}/reveal",
        response_class=HTMLResponse,
    )
    async def reveal_operator_delivery_link(
        external_id: str,
        request: Request,
    ) -> HTMLResponse:
        _enforce_https(request, require_https)
        _enforce_same_origin(request)
        actor = await authenticate_operator(request)
        form = await _read_form(request)
        idempotency_key = _required(form, "idempotency_key", maximum=200)
        _verify_csrf(
            csrf,
            _required(form, "csrf_token", maximum=2_000),
            _operator_action_context(
                "reveal",
                actor.tenant_id,
                actor.user_id,
                external_id,
                idempotency_key,
            ),
        )
        revealed = await facade.reveal_manual_delivery_link(
            external_id,
            actor=actor,
            idempotency_key=idempotency_key,
            evidence=_evidence(request),
        )
        _require_link_copied_result(revealed, expected_external_id=external_id)
        return _secure_html(_manual_link_revealed_page(revealed))

    @router.post(
        "/operator/deliveries/{external_id}/sent",
        response_class=HTMLResponse,
    )
    async def record_operator_delivery_sent(
        external_id: str,
        request: Request,
    ) -> HTMLResponse:
        _enforce_https(request, require_https)
        _enforce_same_origin(request)
        actor = await authenticate_operator(request)
        form = await _read_form(request)
        idempotency_key = _required(form, "idempotency_key", maximum=200)
        _verify_csrf(
            csrf,
            _required(form, "csrf_token", maximum=2_000),
            _operator_action_context(
                "record-sent",
                actor.tenant_id,
                actor.user_id,
                external_id,
                idempotency_key,
            ),
        )
        channel = _required(form, "channel", maximum=30).lower()
        if channel not in _MANUAL_CHANNELS:
            _unprocessable("channel must be email or whatsapp")
        receipt = await facade.record_manual_delivery_sent(
            external_id,
            channel=channel,
            recipient_contact=_required(
                form,
                "recipient_contact",
                maximum=320,
            ),
            actor=actor,
            idempotency_key=idempotency_key,
            evidence=_evidence(request),
        )
        _require_send_recorded_result(receipt, expected_external_id=external_id)
        return _secure_html(_manual_send_recorded_page(receipt))

    @router.get(
        "/operator/deliveries/{external_id}/activity",
        response_class=HTMLResponse,
    )
    async def view_operator_delivery_activity(
        external_id: str,
        request: Request,
    ) -> HTMLResponse:
        _enforce_https(request, require_https)
        actor = await authenticate_operator(request)
        page = await facade.get_manual_delivery_activity(
            external_id,
            actor=actor,
            evidence=_evidence(request),
        )
        return _secure_html(_manual_delivery_activity_page(page))

    @router.get("/supplier/rfq/{capability_token}", response_class=HTMLResponse)
    async def view_rfq(capability_token: str, request: Request) -> HTMLResponse:
        _enforce_https(request, require_https)
        page = await facade.get_rfq(
            capability_token,
            evidence=_evidence(request),
        )
        if page.opened_at is None:
            idempotency_key = secrets.token_urlsafe(24)
            csrf_proof = csrf.issue(
                _supplier_action_context(
                    "open-rfq",
                    capability_token,
                    idempotency_key,
                )
            )
            return _secure_html(
                _capability_open_page(
                    title="Abrir solicitação de cotação",
                    description=(
                        "Confirme que você deseja abrir esta solicitação. "
                        "Essa ação registrará o recebimento pelo fornecedor."
                    ),
                    action_path=(f"/live/supplier/rfq/{quote(capability_token, safe='')}/open"),
                    csrf_proof=csrf_proof,
                    idempotency_key=idempotency_key,
                )
            )
        idempotency_key = secrets.token_urlsafe(24)
        csrf_proof = csrf.issue(
            _supplier_action_context("quote", capability_token, idempotency_key)
        )
        return _secure_html(_rfq_page(page, csrf_proof, idempotency_key))

    @router.post(
        "/supplier/rfq/{capability_token}/open",
        response_class=RedirectResponse,
    )
    async def confirm_rfq_open(
        capability_token: str,
        request: Request,
    ) -> RedirectResponse:
        _enforce_https(request, require_https)
        _enforce_same_origin(request)
        form = await _read_form(request)
        idempotency_key = _required(form, "idempotency_key", maximum=200)
        _verify_csrf(
            csrf,
            _required(form, "csrf_token", maximum=2_000),
            _supplier_action_context("open-rfq", capability_token, idempotency_key),
        )
        await facade.open_rfq(
            capability_token,
            idempotency_key=idempotency_key,
            evidence=_evidence(request),
        )
        return _secure_redirect(f"/live/supplier/rfq/{quote(capability_token, safe='')}")

    @router.post("/supplier/rfq/{capability_token}", response_class=HTMLResponse)
    async def submit_quote(capability_token: str, request: Request) -> HTMLResponse:
        _enforce_https(request, require_https)
        _enforce_same_origin(request)
        form = await _read_form(request)
        idempotency_key = _required(form, "idempotency_key", maximum=200)
        _verify_csrf(
            csrf,
            _required(form, "csrf_token", maximum=2_000),
            _supplier_action_context("quote", capability_token, idempotency_key),
        )
        receipt = await facade.submit_quote(
            capability_token,
            _quote_submission(form),
            idempotency_key=idempotency_key,
            evidence=_evidence(request),
        )
        return _secure_html(_receipt_page("Proposta enviada", receipt))

    @router.get("/approvals/{approval_id}", response_class=HTMLResponse)
    async def view_approval(approval_id: str, request: Request) -> HTMLResponse:
        _enforce_https(request, require_https)
        actor = await authenticate_approver(request)
        page = await facade.get_approval(
            approval_id,
            actor=actor,
            evidence=_evidence(request),
        )
        idempotency_key = secrets.token_urlsafe(24)
        csrf_proof = csrf.issue(
            _approval_action_context(
                page.approval_id,
                page.approval_version,
                actor.tenant_id,
                actor.user_id,
                idempotency_key,
            )
        )
        return _secure_html(_approval_page(page, csrf_proof, idempotency_key))

    @router.post("/approvals/{approval_id}", response_class=HTMLResponse)
    async def decide_approval(approval_id: str, request: Request) -> HTMLResponse:
        _enforce_https(request, require_https)
        _enforce_same_origin(request)
        actor = await authenticate_approver(request)
        form = await _read_form(request)
        expected_version = _positive_int(form, "expected_version")
        idempotency_key = _required(form, "idempotency_key", maximum=200)
        _verify_csrf(
            csrf,
            _required(form, "csrf_token", maximum=2_000),
            _approval_action_context(
                approval_id,
                expected_version,
                actor.tenant_id,
                actor.user_id,
                idempotency_key,
            ),
        )
        decision = _required(form, "decision", maximum=20)
        if decision not in {"approve", "reject"}:
            _unprocessable("decision must be approve or reject")
        reason = _optional(form, "reason", maximum=2_000)
        if decision == "reject" and not reason:
            _unprocessable("a rejection reason is required")
        receipt = await facade.decide_approval(
            approval_id,
            expected_version=expected_version,
            approve=decision == "approve",
            reason=reason,
            actor=actor,
            idempotency_key=idempotency_key,
            evidence=_evidence(request),
        )
        return _secure_html(_receipt_page("Decisão registrada", receipt))

    @router.get("/supplier/awards/{capability_token}", response_class=HTMLResponse)
    async def view_award(capability_token: str, request: Request) -> HTMLResponse:
        _enforce_https(request, require_https)
        page = await facade.get_award(
            capability_token,
            evidence=_evidence(request),
        )
        if page.opened_at is None:
            idempotency_key = secrets.token_urlsafe(24)
            csrf_proof = csrf.issue(
                _supplier_action_context(
                    "open-award",
                    capability_token,
                    idempotency_key,
                )
            )
            return _secure_html(
                _capability_open_page(
                    title="Abrir award",
                    description=(
                        "Confirme que você deseja abrir este award. "
                        "A abertura não o aceita e não confirma a reserva."
                    ),
                    action_path=(f"/live/supplier/awards/{quote(capability_token, safe='')}/open"),
                    csrf_proof=csrf_proof,
                    idempotency_key=idempotency_key,
                )
            )
        response_idempotency = secrets.token_urlsafe(24)
        reservation_idempotency = secrets.token_urlsafe(24)
        response_csrf = csrf.issue(
            _award_response_action_context(
                capability_token,
                response_idempotency,
                page.terms_snapshot_hash,
            )
        )
        reservation_csrf = csrf.issue(
            _supplier_action_context("reservation", capability_token, reservation_idempotency)
        )
        return _secure_html(
            _award_page(
                page,
                response_csrf=response_csrf,
                response_idempotency=response_idempotency,
                reservation_csrf=reservation_csrf,
                reservation_idempotency=reservation_idempotency,
            )
        )

    @router.post(
        "/supplier/awards/{capability_token}/open",
        response_class=RedirectResponse,
    )
    async def confirm_award_open(
        capability_token: str,
        request: Request,
    ) -> RedirectResponse:
        _enforce_https(request, require_https)
        _enforce_same_origin(request)
        form = await _read_form(request)
        idempotency_key = _required(form, "idempotency_key", maximum=200)
        _verify_csrf(
            csrf,
            _required(form, "csrf_token", maximum=2_000),
            _supplier_action_context(
                "open-award",
                capability_token,
                idempotency_key,
            ),
        )
        await facade.open_award(
            capability_token,
            idempotency_key=idempotency_key,
            evidence=_evidence(request),
        )
        return _secure_redirect(f"/live/supplier/awards/{quote(capability_token, safe='')}")

    @router.post("/supplier/awards/{capability_token}", response_class=HTMLResponse)
    async def act_on_award(capability_token: str, request: Request) -> HTMLResponse:
        _enforce_https(request, require_https)
        _enforce_same_origin(request)
        form = await _read_form(request)
        action = _required(form, "action", maximum=30)
        idempotency_key = _required(form, "idempotency_key", maximum=200)
        if action in {"award-response", "award-decline"}:
            terms_snapshot_hash = _required(
                form,
                "terms_snapshot_hash",
                maximum=200,
            )
            csrf_context = _award_response_action_context(
                capability_token,
                idempotency_key,
                terms_snapshot_hash,
            )
        elif action == "reservation":
            terms_snapshot_hash = ""
            csrf_context = _supplier_action_context(
                "reservation",
                capability_token,
                idempotency_key,
            )
        else:
            _unprocessable("unsupported award action")
        _verify_csrf(
            csrf,
            _required(form, "csrf_token", maximum=2_000),
            csrf_context,
        )

        if action in {"award-response", "award-decline"}:
            accept = action == "award-response"
            respondent_name = _required(form, "respondent_name", maximum=200)
            reason = _optional(form, "reason", maximum=2_000)
            terms_accepted = _checkbox(form, "terms_accepted")
            if accept and not terms_accepted:
                _unprocessable("terms must be explicitly accepted")
            if not accept and not reason:
                _unprocessable("a decline reason is required")
            receipt = await facade.respond_to_award(
                capability_token,
                accept=accept,
                respondent_name=respondent_name,
                reason=reason,
                terms_accepted=terms_accepted,
                terms_snapshot_hash=terms_snapshot_hash,
                idempotency_key=idempotency_key,
                evidence=_evidence(request),
            )
            return _secure_html(_receipt_page("Resposta registrada", receipt))

        if action == "reservation":
            receipt = await facade.confirm_reservation(
                capability_token,
                _reservation_submission(form),
                idempotency_key=idempotency_key,
                evidence=_evidence(request),
            )
            return _secure_html(_receipt_page("Reserva confirmada", receipt))

        _unprocessable("unsupported award action")

    return router


def _operator_deliveries_page(deliveries: tuple[ManualDeliverySummary, ...]) -> str:
    rows = "".join(
        "<tr>"
        f'<td><a href="/live/operator/deliveries/{quote(item.external_id, safe="")}">'
        f"{_escape(item.external_id)}</a></td>"
        f"<td>{_escape(item.kind)}</td>"
        f"<td>{_escape(item.supplier_name)}</td>"
        f"<td>{_escape(item.delivery_status)}</td>"
        f"<td><time>{_escape(item.created_at.isoformat())}</time></td>"
        "</tr>"
        for item in deliveries
    )
    if not rows:
        rows = '<tr><td colspan="5">Nenhuma entrega manual pendente.</td></tr>'
    return _document(
        "Entregas manuais",
        "<h1>Entregas manuais</h1>"
        "<table><thead><tr><th>ID externo</th><th>Tipo</th><th>Fornecedor</th>"
        "<th>Status</th><th>Criada em</th></tr></thead>"
        f"<tbody>{rows}</tbody></table>",
    )


def _execution_evidence_page(page: ExecutionEvidencePage) -> str:
    timeline = "".join(
        "<li>"
        f"<time>{_escape(item.occurred_at.isoformat())}</time> — "
        f"{_escape(item.source)} — {_escape(item.event_type)} — "
        f"{_escape(item.actor_display_name)} — {_escape(item.detail)}"
        "</li>"
        for item in page.timeline
    )
    if not timeline:
        timeline = "<li>Nenhuma evidência registrada.</li>"
    comparisons = "".join(
        f'<li><a href="/live/operator/comparisons/{quote(item, safe="")}">{_escape(item)}</a></li>'
        for item in page.comparison_ids
    )
    if not comparisons:
        comparisons = "<li>Nenhuma comparação criada.</li>"
    approval_actor = page.approval_actor_display_name or "Ainda não definido"
    return _document(
        "Evidência da execução",
        f"""
        <h1>Evidência da execução real</h1>
        <dl>
          <dt>Pedido</dt><dd>{_escape(page.procurement_request_id)}</dd>
          <dt>Status final</dt><dd>{_escape(page.final_status)}</dd>
          <dt>Entregas confirmadas</dt>
          <dd>{page.confirmed_delivery_count}/{page.delivery_count}</dd>
          <dt>Propostas válidas</dt>
          <dd>{page.valid_quote_count}/{page.quote_count}</dd>
          <dt>Clarificações resolvidas</dt>
          <dd>{page.resolved_clarification_count}/{page.clarification_count}</dd>
          <dt>Aprovação</dt><dd>{_escape(page.approval_status or "Não solicitada")}</dd>
          <dt>Ator da aprovação</dt><dd>{_escape(approval_actor)}</dd>
          <dt>Award</dt><dd>{_escape(page.award_status or "Não criado")}</dd>
          <dt>Reserva</dt><dd>{_escape(page.reservation_status or "Não criada")}</dd>
        </dl>
        <h2>Comparações determinísticas</h2>
        <ul>{comparisons}</ul>
        <h2>Linha do tempo</h2>
        <ol>{timeline}</ol>""",
    )


def _comparison_page(page: ComparisonPage) -> str:
    requirements = "".join(
        f"<dt>{_escape(key)}</dt><dd>{_escape(_display(value))}</dd>"
        for key, value in page.requirements.items()
    )
    weights = "".join(
        f"<tr><td>{_escape(criterion)}</td><td>{weight}%</td></tr>"
        for criterion, weight in page.ranking_weights.items()
    )
    if not weights:
        weights = '<tr><td colspan="2">Nenhum peso registrado.</td></tr>'

    candidate_rows: list[str] = []
    component_sections: list[str] = []
    for candidate in page.candidates:
        recommended = (
            candidate.quote_id == page.recommended_quote_id
            and candidate.quote_version == page.recommended_quote_version
        )
        candidate_rows.append(
            "<tr>"
            f"<td>{_escape(candidate.supplier_name)}<br>"
            f"<small>{_escape(candidate.supplier_id)}</small></td>"
            f"<td>{_escape(candidate.quote_id)} v{candidate.quote_version}"
            f"{'<br><strong>Recomendada</strong>' if recommended else ''}</td>"
            f"<td>{_escape(_display(candidate.eligible))}</td>"
            f"<td>{_money(candidate.total_cents, candidate.currency)}</td>"
            f"<td>{_money(candidate.price_per_person_cents, candidate.currency)}</td>"
            f"<td>{_escape(_display(candidate.invoice_available))}</td>"
            f"<td>{_escape(_display(candidate.valid_until))}</td>"
            f"<td>{_escape(_display(candidate.availability_confirmed))}</td>"
            f"<td>{_escape(_display(candidate.no_single_use_plastic_confirmed))}</td>"
            "<td>"
            f"Vegetariano: {_escape(_display(candidate.vegetarian_status))}<br>"
            f"Vegano: {_escape(_display(candidate.vegan_status))}<br>"
            f"Sem glúten: {_escape(_display(candidate.gluten_free_status))}</td>"
            f"<td>{_escaped_values(candidate.included_items)}</td>"
            f"<td>{_escaped_values(candidate.substitutions)}</td>"
            f"<td>{_basis_points(candidate.score_basis_points)}</td>"
            f"<td>{_escaped_values(candidate.disqualification_reasons)}</td>"
            f"<td>{_escaped_values(candidate.risks)}</td>"
            f"<td>{_escaped_values(candidate.evidence_refs)}</td>"
            "</tr>"
        )
        component_rows = "".join(
            "<tr>"
            f"<td>{_escape(component.criterion)}</td>"
            f"<td>{component.weight_percent}%</td>"
            f"<td>{_basis_points(component.normalized_score_basis_points)}</td>"
            f"<td>{_basis_points(component.points_basis_points)}</td>"
            f"<td>{_escape(component.reason or '—')}</td>"
            f"<td>{_escaped_values(component.evidence_refs)}</td>"
            "</tr>"
            for component in candidate.score_components
        )
        if not component_rows:
            component_rows = '<tr><td colspan="6">Sem componentes de score.</td></tr>'
        component_sections.append(
            f"<h3>{_escape(candidate.supplier_name)} — "
            f"{_escape(candidate.quote_id)} v{candidate.quote_version}</h3>"
            "<table><thead><tr><th>Critério</th><th>Peso</th>"
            "<th>Score normalizado</th><th>Pontos ponderados</th>"
            "<th>Motivo</th><th>Evidências</th></tr></thead>"
            f"<tbody>{component_rows}</tbody></table>"
        )
    candidates = "".join(candidate_rows)
    if not candidates:
        candidates = '<tr><td colspan="16">Nenhuma proposta comparada.</td></tr>'
    recommendation = "Nenhuma"
    if page.recommended_quote_id and page.recommended_quote_version:
        recommendation = f"{page.recommended_quote_id} v{page.recommended_quote_version}"
    return _document(
        "Comparação determinística",
        f"""
        <h1>Comparação determinística de propostas</h1>
        <dl>
          <dt>Comparação</dt><dd>{_escape(page.comparison_id)} v{page.comparison_version}</dd>
          <dt>Pedido</dt><dd>{_escape(page.procurement_request_id)}</dd>
          <dt>Rodada RFQ</dt><dd>{_escape(page.rfq_round_id)}</dd>
          <dt>Versão da coleta</dt><dd>{page.quote_collection_version}</dd>
          <dt>Status</dt><dd>{_escape(page.status)}</dd>
          <dt>Recomendação</dt><dd>{_escape(recommendation)}</dd>
          <dt>Criada em</dt><dd>{_escape(page.created_at.isoformat())}</dd>
        </dl>
        <h2>Requisitos congelados da rodada</h2><dl>{requirements}</dl>
        <h2>Pesos da política</h2>
        <table><thead><tr><th>Critério</th><th>Peso</th></tr></thead>
          <tbody>{weights}</tbody></table>
        <h2>Matriz de propostas</h2>
        <table><thead><tr>
          <th>Fornecedor</th><th>Proposta</th><th>Elegível</th>
          <th>Total</th><th>Preço/pessoa</th><th>NF</th><th>Validade</th>
          <th>Disponibilidade</th><th>Sem plástico</th><th>Dietas</th>
          <th>Itens</th><th>Substituições</th><th>Score agregado</th>
          <th>Desqualificações</th><th>Riscos</th><th>Evidências</th>
        </tr></thead><tbody>{candidates}</tbody></table>
        <h2>Componentes do score</h2>
        {"".join(component_sections)}""",
    )


def _operator_delivery_page(
    page: ManualDeliveryPage,
    *,
    reveal_csrf: str,
    reveal_idempotency: str,
    sent_csrf: str,
    sent_idempotency: str,
) -> str:
    encoded_external_id = quote(page.external_id, safe="")
    opened = page.opened_at.isoformat() if page.opened_at else "Ainda não aberta"
    last_send = "Não registrado"
    if page.last_send_channel and page.last_recipient_contact:
        last_send = f"{page.last_send_channel}: {page.last_recipient_contact}"
    return _document(
        "Entrega manual",
        f"""
        <h1>Entrega manual</h1>
        <dl>
          <dt>ID externo</dt><dd>{_escape(page.external_id)}</dd>
          <dt>Tipo</dt><dd>{_escape(page.kind)}</dd>
          <dt>Fornecedor</dt><dd>{_escape(page.supplier_name)}</dd>
          <dt>Pedido</dt><dd>{_escape(page.procurement_request_id)}</dd>
          <dt>Status</dt><dd>{_escape(page.delivery_status)}</dd>
          <dt>Aberta pelo fornecedor</dt><dd>{_escape(opened)}</dd>
          <dt>Último envio registrado</dt><dd>{_escape(last_send)}</dd>
        </dl>
        <p><a href="/live/operator/deliveries/{encoded_external_id}/activity">
          Ver atividade e status</a></p>
        <h2>Copiar link</h2>
        <p>O link só será exibido depois que esta ação for registrada.</p>
        <form method="post"
              action="/live/operator/deliveries/{encoded_external_id}/reveal">
          {_hidden("csrf_token", reveal_csrf)}
          {_hidden("idempotency_key", reveal_idempotency)}
          <button type="submit">Registrar e revelar link</button>
        </form>
        <h2>Registrar envio manual</h2>
        <p>Isto registra o envio, mas não confirma a entrega.</p>
        <form method="post"
              action="/live/operator/deliveries/{encoded_external_id}/sent">
          {_hidden("csrf_token", sent_csrf)}
          {_hidden("idempotency_key", sent_idempotency)}
          <label>Canal <select name="channel" required>
            <option value="whatsapp">WhatsApp</option>
            <option value="email">E-mail</option>
          </select></label>
          {_text_input("recipient_contact", "Contato do destinatário")}
          <button type="submit">Registrar envio</button>
        </form>""",
    )


def _manual_link_revealed_page(revealed: ManualLinkReveal) -> str:
    safe_url = _safe_supplier_url(revealed.supplier_url)
    return _document(
        "Link registrado",
        "<h1>Link pronto para cópia</h1>"
        f"<p>Evento: {_escape(revealed.event_type)}</p>"
        f'<p><a href="{_escape(safe_url)}" rel="noreferrer noopener">'
        f"{_escape(safe_url)}</a></p>"
        "<p>A abertura deste link pelo fornecedor é que confirmará a entrega.</p>",
    )


def _manual_send_recorded_page(receipt: ManualSendReceipt) -> str:
    return _document(
        "Envio registrado",
        "<h1>Envio manual registrado</h1>"
        f"<p>Evento: {_escape(receipt.event_type)}</p>"
        f"<p>Status: {_escape(receipt.delivery_status)}</p>"
        f"<p>{_escape(receipt.message)}</p>"
        "<p>A entrega só será confirmada quando o fornecedor abrir o link.</p>",
    )


def _manual_delivery_activity_page(page: ManualDeliveryActivityPage) -> str:
    activities = "".join(
        "<li>"
        f"<time>{_escape(activity.occurred_at.isoformat())}</time> — "
        f"{_escape(activity.event_type)} — {_escape(activity.actor_display_name)} — "
        f"{_escape(activity.detail)}"
        "</li>"
        for activity in page.activities
    )
    if not activities:
        activities = "<li>Nenhuma atividade registrada.</li>"
    return _document(
        "Atividade da entrega",
        "<h1>Atividade da entrega</h1>"
        f"<p>ID externo: {_escape(page.external_id)}</p>"
        f"<p>Fornecedor: {_escape(page.supplier_name)}</p>"
        f"<p>Status: {_escape(page.delivery_status)}</p>"
        f"<ol>{activities}</ol>",
    )


def _capability_open_page(
    *,
    title: str,
    description: str,
    action_path: str,
    csrf_proof: str,
    idempotency_key: str,
) -> str:
    return _document(
        title,
        f"""
        <h1>{_escape(title)}</h1>
        <p>{_escape(description)}</p>
        <form method="post" action="{_escape(action_path)}">
          {_hidden("csrf_token", csrf_proof)}
          {_hidden("idempotency_key", idempotency_key)}
          <button type="submit">Confirmar abertura</button>
        </form>""",
    )


def _rfq_page(page: SupplierRFQPage, csrf_proof: str, idempotency_key: str) -> str:
    requirements = "".join(
        f"<dt>{_escape(key)}</dt><dd>{_escape(_display(value))}</dd>"
        for key, value in page.requirements.items()
    )
    clarification = ""
    if page.clarification_messages:
        items = "".join(f"<li>{_escape(message)}</li>" for message in page.clarification_messages)
        clarification = (
            "<section><h2>Esclarecimento solicitado</h2>"
            f"<ul>{items}</ul><p>Envie uma nova versão da proposta abaixo.</p></section>"
        )
    if page.quote_already_submitted:
        form = "<p>Uma proposta já foi recebida para este link.</p>"
    else:
        form = f"""
        <form method="post" action="" autocomplete="on">
          {_hidden("csrf_token", csrf_proof)}
          {_hidden("idempotency_key", idempotency_key)}
          <fieldset><legend>Disponibilidade e preço</legend>
            {_boolean_select("availability_confirmed", "Disponibilidade confirmada")}
            {_number_input("subtotal_cents", "Subtotal (centavos)")}
            {_number_input("delivery_fee_cents", "Entrega (centavos)", value="0")}
            {_number_input("other_fee_cents", "Outras taxas (centavos)", value="0")}
            {_number_input("total_cents", "Total (centavos)")}
          </fieldset>
          <fieldset><legend>Escopo e condições</legend>
            {_textarea("included_items", "Itens incluídos, um por linha", required=True)}
            {_textarea("substitutions", "Substituições, uma por linha")}
            {_boolean_select("invoice_available", "Emite nota fiscal")}
            {
            _boolean_select(
                "no_single_use_plastic_confirmed",
                "Confirma operação sem plástico de uso único",
            )
        }
            {_dietary_select("vegetarian_status", "Opções vegetarianas")}
            {_dietary_select("vegan_status", "Opções veganas")}
            {_dietary_select("gluten_free_status", "Opções sem glúten")}
            {_textarea("cross_contamination_warning", "Aviso de contaminação cruzada")}
            {
            _text_input(
                "valid_until",
                "Validade ISO com fuso",
                placeholder="2026-08-22T18:00:00-03:00",
            )
        }
            {_textarea("cancellation_terms", "Termos de cancelamento", required=True)}
          </fieldset>
          <fieldset><legend>Responsável</legend>
            {_text_input("respondent_name", "Nome", autocomplete="name")}
            {_text_input("respondent_contact", "Contato", autocomplete="email")}
            <label><input type="checkbox" name="supplier_confirmation" value="true" required>
              Confirmo que os dados e a disponibilidade acima são verdadeiros.</label>
          </fieldset>
          <button type="submit">Enviar proposta</button>
        </form>"""
    return _document(
        "Responder RFQ",
        f"""
        <h1>Solicitação de cotação</h1>
        <p>Fornecedor: {_escape(page.supplier_name)}</p>
        <p>Prazo: <time>{_escape(page.response_deadline.isoformat())}</time></p>
        <dl>{requirements}</dl>
        {clarification}
        {form}""",
    )


def _approval_page(page: ApprovalPage, csrf_proof: str, idempotency_key: str) -> str:
    comparison = "".join(f"<li>{_escape(item)}</li>" for item in page.comparison_summary)
    form = "<p>Esta aprovação já foi decidida.</p>"
    if page.status == "REQUESTED":
        form = f"""
        <form method="post" action="">
          {_hidden("csrf_token", csrf_proof)}
          {_hidden("idempotency_key", idempotency_key)}
          {_hidden("expected_version", str(page.approval_version))}
          {_textarea("reason", "Justificativa (obrigatória para rejeitar)")}
          <button type="submit" name="decision" value="approve">Aprovar proposta</button>
          <button type="submit" name="decision" value="reject">Rejeitar proposta</button>
        </form>"""
    return _document(
        "Aprovação humana",
        f"""
        <h1>Aprovação humana</h1>
        <p>Status: {_escape(page.status)}</p>
        <p>Pedido: {_escape(page.procurement_request_id)}</p>
        <p>Fornecedor: {_escape(page.supplier_name)}</p>
        <p>Proposta: {_escape(page.quote_id)} v{page.quote_version}</p>
        <p>Total: {_money(page.total_cents, page.currency)}</p>
        <h2>Comparação</h2><ul>{comparison}</ul>
        {form}""",
    )


def _award_page(
    page: AwardPage,
    *,
    response_csrf: str,
    response_idempotency: str,
    reservation_csrf: str,
    reservation_idempotency: str,
) -> str:
    terms = page.terms_snapshot
    included_items = "".join(f"<li>{_escape(item)}</li>" for item in terms.included_items)
    substitutions = "".join(f"<li>{_escape(item)}</li>" for item in terms.substitutions)
    if not substitutions:
        substitutions = "<li>Nenhuma substituição.</li>"
    frozen_terms = f"""
        <section aria-labelledby="frozen-award-terms">
          <h2 id="frozen-award-terms">Termos congelados deste award</h2>
          <p>O aceite abaixo se refere exatamente a este snapshot imutável.</p>
          <dl>
            <dt>Proposta</dt><dd>{_escape(terms.quote_id)} v{terms.quote_version}</dd>
            <dt>Fornecedor (ID)</dt><dd>{_escape(terms.supplier_id)}</dd>
            <dt>Total</dt><dd>{_money(terms.total_cents, terms.currency)}</dd>
            <dt>Data do evento</dt><dd>{_escape(terms.event_date.isoformat())}</dd>
            <dt>Horário de entrega</dt><dd>{_escape(terms.delivery_time.isoformat())}</dd>
            <dt>Número de pessoas</dt><dd>{terms.people_count}</dd>
            <dt>Termos de cancelamento</dt>
            <dd>{_escape(terms.cancellation_terms)}</dd>
            <dt>Hash do snapshot</dt><dd><code>{_escape(page.terms_snapshot_hash)}</code></dd>
          </dl>
          <h3>Itens incluídos</h3><ul>{included_items}</ul>
          <h3>Substituições</h3><ul>{substitutions}</ul>
        </section>"""
    action = "<p>Este award não aceita mais respostas.</p>"
    if page.status == "DELIVERED":
        action = f"""
        <form method="post" action="">
          {_hidden("csrf_token", response_csrf)}
          {_hidden("idempotency_key", response_idempotency)}
          {_hidden("terms_snapshot_hash", page.terms_snapshot_hash)}
          {_text_input("respondent_name", "Nome do responsável", autocomplete="name")}
          {_textarea("reason", "Motivo (obrigatório para recusar)")}
          <label><input type="checkbox" name="terms_accepted" value="true">
            Li e aceito integralmente os termos congelados deste award.</label>
          <button type="submit" name="action" value="award-response">Aceitar award</button>
          <button type="submit" name="action" value="award-decline">Recusar award</button>
        </form>"""
    elif page.status == "ACCEPTED" and page.reservation_status != "CONFIRMED":
        action = f"""
        <form method="post" action="">
          {_hidden("csrf_token", reservation_csrf)}
          {_hidden("idempotency_key", reservation_idempotency)}
          {_hidden("action", "reservation")}
          {_text_input("event_date", "Data", value=page.event_date.isoformat())}
          {_text_input("delivery_window", "Janela de entrega", value=page.delivery_window)}
          {_number_input("people_count", "Número de pessoas", value=str(page.people_count))}
          {_text_input("confirmed_by", "Confirmado por", autocomplete="name")}
          <button type="submit">Confirmar reserva de capacidade</button>
        </form>"""
    return _document(
        "Award do fornecedor",
        f"""
        <h1>Award</h1>
        <p>Fornecedor: {_escape(page.supplier_name)}</p>
        <p>Pedido: {_escape(page.procurement_request_id)}</p>
        <p>Proposta aprovada: {_escape(page.quote_id)} v{page.quote_version}</p>
        <p>Total aprovado: {_money(page.approved_total_cents, page.currency)}</p>
        <p>Status: {_escape(page.status)}</p>
        <p>Reserva: {_escape(page.reservation_status)}</p>
        {frozen_terms}
        {action}""",
    )


def _quote_submission(form: Mapping[str, str]) -> QuoteFormSubmission:
    subtotal = _non_negative_int(form, "subtotal_cents")
    delivery_fee = _non_negative_int(form, "delivery_fee_cents")
    other_fee = _non_negative_int(form, "other_fee_cents")
    total = _non_negative_int(form, "total_cents")
    if subtotal + delivery_fee + other_fee != total:
        _unprocessable("total_cents must equal subtotal plus all fees")

    valid_until_raw = _required(form, "valid_until", maximum=100)
    try:
        valid_until = datetime.fromisoformat(valid_until_raw)
    except ValueError:
        _unprocessable("valid_until must be an ISO datetime")
    if valid_until.tzinfo is None:
        _unprocessable("valid_until must include a timezone offset")

    return QuoteFormSubmission(
        availability_confirmed=_boolean(form, "availability_confirmed"),
        subtotal_cents=subtotal,
        delivery_fee_cents=delivery_fee,
        other_fee_cents=other_fee,
        total_cents=total,
        included_items=_lines(form, "included_items", required=True),
        substitutions=_lines(form, "substitutions"),
        invoice_available=_boolean(form, "invoice_available"),
        no_single_use_plastic_confirmed=_boolean(
            form,
            "no_single_use_plastic_confirmed",
        ),
        vegetarian_status=_dietary(form, "vegetarian_status"),
        vegan_status=_dietary(form, "vegan_status"),
        gluten_free_status=_dietary(form, "gluten_free_status"),
        cross_contamination_warning=_optional(form, "cross_contamination_warning", maximum=2_000),
        valid_until=valid_until,
        cancellation_terms=_required(form, "cancellation_terms", maximum=2_000),
        respondent_name=_required(form, "respondent_name", maximum=200),
        respondent_contact=_required(form, "respondent_contact", maximum=320),
        supplier_confirmation=_boolean(form, "supplier_confirmation"),
    )


def _reservation_submission(form: Mapping[str, str]) -> ReservationFormSubmission:
    raw_date = _required(form, "event_date", maximum=20)
    try:
        event_date = date.fromisoformat(raw_date)
    except ValueError:
        _unprocessable("event_date must be an ISO date")
    return ReservationFormSubmission(
        event_date=event_date,
        delivery_window=_required(form, "delivery_window", maximum=100),
        people_count=_positive_int(form, "people_count"),
        confirmed_by=_required(form, "confirmed_by", maximum=200),
    )


async def _read_form(request: Request) -> dict[str, str]:
    content_type = request.headers.get("content-type", "").split(";", maxsplit=1)[0].lower()
    if content_type != "application/x-www-form-urlencoded":
        raise HTTPException(status_code=415, detail="urlencoded form required")
    content_length = request.headers.get("content-length")
    if content_length:
        try:
            if int(content_length) > _MAX_FORM_BYTES:
                raise HTTPException(status_code=413, detail="form is too large")
        except ValueError:
            raise HTTPException(status_code=400, detail="invalid content length") from None
    body = await request.body()
    if len(body) > _MAX_FORM_BYTES:
        raise HTTPException(status_code=413, detail="form is too large")
    try:
        decoded = body.decode("utf-8", errors="strict")
        parsed = parse_qs(
            decoded,
            keep_blank_values=True,
            strict_parsing=True,
            max_num_fields=50,
        )
    except (UnicodeDecodeError, ValueError):
        raise HTTPException(status_code=400, detail="malformed form") from None
    if any(len(values) != 1 for values in parsed.values()):
        raise HTTPException(status_code=400, detail="duplicate form fields are not allowed")
    return {key: values[0] for key, values in parsed.items()}


def _enforce_https(request: Request, required: bool) -> None:
    if required and request.url.scheme != "https":
        raise HTTPException(status_code=400, detail="HTTPS is required")


def _enforce_same_origin(request: Request) -> None:
    origin = request.headers.get("origin")
    if not origin:
        return
    parsed = urlsplit(origin)
    if (parsed.scheme, parsed.netloc) != (request.url.scheme, request.url.netloc):
        raise HTTPException(status_code=403, detail="cross-origin form submission denied")


def _verify_csrf(csrf: CsrfProtector, proof: str, context: str) -> None:
    try:
        csrf.verify(proof, context)
    except CsrfValidationError:
        raise HTTPException(status_code=403, detail="invalid or expired form proof") from None


def _supplier_action_context(action: str, token: str, idempotency_key: str) -> str:
    return f"{action}:{capability_fingerprint(token)}:{idempotency_key}"


def _award_response_action_context(
    token: str,
    idempotency_key: str,
    terms_snapshot_hash: str,
) -> str:
    return f"award-response:{capability_fingerprint(token)}:{terms_snapshot_hash}:{idempotency_key}"


def _approval_action_context(
    approval_id: str,
    version: int,
    tenant_id: str,
    user_id: str,
    idempotency_key: str,
) -> str:
    return f"approval:{tenant_id}:{user_id}:{approval_id}:{version}:{idempotency_key}"


def _operator_action_context(
    action: str,
    tenant_id: str,
    user_id: str,
    external_id: str,
    idempotency_key: str,
) -> str:
    return f"operator:{action}:{tenant_id}:{user_id}:{external_id}:{idempotency_key}"


def _require_link_copied_result(
    result: ManualLinkReveal,
    *,
    expected_external_id: str,
) -> None:
    if result.external_id != expected_external_id or result.event_type != "LINK_COPIED":
        raise HTTPException(status_code=500, detail="invalid link reveal result")


def _require_send_recorded_result(
    result: ManualSendReceipt,
    *,
    expected_external_id: str,
) -> None:
    if (
        result.external_id != expected_external_id
        or result.event_type != "SEND_RECORDED"
        or result.delivery_status != "SENT_TO_GATEWAY"
    ):
        raise HTTPException(status_code=500, detail="invalid manual send result")


def _safe_supplier_url(value: str) -> str:
    if len(value) > 4_096:
        raise HTTPException(status_code=500, detail="invalid supplier URL")
    parsed = urlsplit(value)
    if (
        parsed.scheme != "https"
        or not parsed.netloc
        or parsed.username is not None
        or parsed.password is not None
    ):
        raise HTTPException(status_code=500, detail="invalid supplier URL")
    return value


def _evidence(request: Request) -> RequestEvidence:
    user_agent = request.headers.get("user-agent")
    return RequestEvidence(
        request_id=str(uuid4()),
        ip_address=request.client.host if request.client else None,
        user_agent=user_agent[:512] if user_agent else None,
    )


def _secure_html(content: str, *, status_code: int = 200) -> HTMLResponse:
    return HTMLResponse(content, status_code=status_code, headers=_SECURITY_HEADERS)


def _secure_redirect(location: str) -> RedirectResponse:
    return RedirectResponse(
        location,
        status_code=303,
        headers=_SECURITY_HEADERS,
    )


def _receipt_page(title: str, receipt: ActionReceipt) -> str:
    return _document(
        title,
        f"<h1>{_escape(title)}</h1>"
        f"<p>Status: {_escape(receipt.status)}</p>"
        f"<p>{_escape(receipt.message)}</p>",
    )


def _document(title: str, body: str) -> str:
    styles = """
      :root{--ink:#17221e;--muted:#66716c;--paper:#f4f2ea;--panel:#fffef9;
        --line:#dcdad0;--green:#186449;--green-soft:#e2f0e8;--red:#a13f38;
        font-family:Inter,ui-sans-serif,system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;
        color:var(--ink);background:var(--paper)}
      *{box-sizing:border-box}body{margin:0;min-height:100vh;background:
        radial-gradient(circle at 80% 0%,#e3eee6 0,transparent 31rem),var(--paper)}
      .topbar{height:72px;padding:0 max(24px,calc((100vw - 1100px)/2));display:flex;
        align-items:center;justify-content:space-between;border-bottom:1px solid rgb(23 34 30/12%)}
      .brand{display:flex;align-items:center;gap:11px;font-weight:750}.brand-mark{display:grid;
        place-items:center;width:34px;height:34px;border-radius:10px;color:#fff;background:var(--ink);
        font-size:12px}.environment{display:flex;align-items:center;gap:8px;color:var(--muted);
        font-size:13px}.environment::before{content:"";width:8px;height:8px;border-radius:50%;
        background:#31a372;box-shadow:0 0 0 4px rgb(49 163 114/13%)}
      .page-shell{width:min(1100px,calc(100% - 32px));margin:0 auto;padding:48px 0 80px}
      main>*{background:var(--panel);border:1px solid var(--line);border-radius:16px;
        box-shadow:0 18px 55px rgb(37 49 43/8%);padding:24px;margin:0 0 18px}
      h1,h2,h3{letter-spacing:-.025em}h1{margin:0 0 20px;font-family:Georgia,"Times New Roman",serif;
        font-size:clamp(32px,5vw,52px);font-weight:500;line-height:1.02}h2{margin-top:28px}
      p,li,dd,td,th,label{line-height:1.55}p{color:var(--muted)}a{color:var(--green);
        font-weight:700;text-underline-offset:3px}table{width:100%;border-collapse:collapse;overflow:hidden}
      th,td{padding:12px;text-align:left;border-bottom:1px solid var(--line);vertical-align:top}
      th{color:var(--muted);font-size:12px;text-transform:uppercase;letter-spacing:.06em}
      dl{display:grid;grid-template-columns:minmax(150px,.5fr) 1fr;gap:0;margin:0}
      dt,dd{margin:0;padding:10px 0;border-bottom:1px solid var(--line)}dt{color:var(--muted)}
      dd{font-weight:700}form{display:grid;gap:14px}label{display:grid;gap:6px;font-weight:700}
      input,textarea,select{width:100%;min-height:44px;padding:10px 12px;border:1px solid #8d978f;
        border-radius:10px;background:#fbfaf5;color:var(--ink);font:inherit}textarea{min-height:110px;resize:vertical}
      button{min-height:44px;padding:10px 16px;border:0;border-radius:10px;background:var(--ink);
        color:#fff;font:inherit;font-weight:750;cursor:pointer}button:hover{background:var(--green)}
      button:focus-visible,input:focus-visible,textarea:focus-visible,select:focus-visible,a:focus-visible{
        outline:3px solid rgb(24 100 73/24%);outline-offset:2px}ol,ul{padding-left:22px}
      code{overflow-wrap:anywhere}.status{display:inline-flex;padding:6px 10px;border-radius:999px;
        color:var(--green);background:var(--green-soft);font-size:12px;font-weight:800}
      @media(max-width:700px){.topbar{height:64px;padding:0 16px}.environment{font-size:11px}
        .page-shell{padding-top:24px}main>*{padding:18px;overflow-x:auto}dl{grid-template-columns:1fr}
        dt{border-bottom:0;padding-bottom:0}h1{font-size:34px}}
    """
    return (
        '<!doctype html><html lang="pt-BR"><head>'
        '<meta charset="utf-8"><meta name="viewport" '
        'content="width=device-width,initial-scale=1">'
        '<meta name="referrer" content="no-referrer">'
        f"<title>{_escape(title)}</title><style>{styles}</style></head>"
        '<body><header class="topbar"><div class="brand"><span class="brand-mark">CA</span>'
        '<span>Canal Agente</span></div><div class="environment">Execução verificável</div></header>'
        f'<main class="page-shell"><section>{body}</section></main></body></html>'
    )


def _hidden(name: str, value: str) -> str:
    return f'<input type="hidden" name="{_escape(name)}" value="{_escape(value)}">'


def _text_input(
    name: str,
    label: str,
    *,
    value: str = "",
    placeholder: str = "",
    autocomplete: str = "off",
) -> str:
    return (
        f'<label>{_escape(label)} <input type="text" name="{_escape(name)}" '
        f'value="{_escape(value)}" placeholder="{_escape(placeholder)}" '
        f'autocomplete="{_escape(autocomplete)}" required></label>'
    )


def _number_input(name: str, label: str, *, value: str = "") -> str:
    return (
        f'<label>{_escape(label)} <input type="number" min="0" step="1" '
        f'name="{_escape(name)}" value="{_escape(value)}" required></label>'
    )


def _textarea(name: str, label: str, *, required: bool = False) -> str:
    required_attribute = " required" if required else ""
    return (
        f'<label>{_escape(label)} <textarea name="{_escape(name)}"'
        f"{required_attribute}></textarea></label>"
    )


def _boolean_select(name: str, label: str) -> str:
    return (
        f'<label>{_escape(label)} <select name="{_escape(name)}" required>'
        '<option value="true">Sim</option><option value="false">Não</option>'
        "</select></label>"
    )


def _dietary_select(name: str, label: str) -> str:
    return (
        f'<label>{_escape(label)} <select name="{_escape(name)}" required>'
        '<option value="confirmed">Confirmado</option>'
        '<option value="partial">Parcial</option>'
        '<option value="unknown">Não confirmado</option>'
        '<option value="not_available">Indisponível</option>'
        "</select></label>"
    )


def _required(form: Mapping[str, str], name: str, *, maximum: int) -> str:
    value = form.get(name, "").strip()
    if not value:
        _unprocessable(f"{name} is required")
    if len(value) > maximum:
        _unprocessable(f"{name} is too long")
    return value


def _optional(form: Mapping[str, str], name: str, *, maximum: int) -> str | None:
    value = form.get(name, "").strip()
    if len(value) > maximum:
        _unprocessable(f"{name} is too long")
    return value or None


def _boolean(form: Mapping[str, str], name: str) -> bool:
    value = form.get(name)
    if value not in {"true", "false"}:
        _unprocessable(f"{name} must be true or false")
    return value == "true"


def _checkbox(form: Mapping[str, str], name: str) -> bool:
    value = form.get(name)
    if value is None:
        return False
    if value != "true":
        _unprocessable(f"{name} must be true when provided")
    return True


def _dietary(form: Mapping[str, str], name: str) -> str:
    value = _required(form, name, maximum=30)
    if value not in _DIETARY_STATUSES:
        _unprocessable(f"{name} has an unsupported value")
    return value


def _non_negative_int(form: Mapping[str, str], name: str) -> int:
    raw = _required(form, name, maximum=20)
    try:
        value = int(raw)
    except ValueError:
        _unprocessable(f"{name} must be an integer")
    if value < 0:
        _unprocessable(f"{name} must not be negative")
    return value


def _positive_int(form: Mapping[str, str], name: str) -> int:
    value = _non_negative_int(form, name)
    if value < 1:
        _unprocessable(f"{name} must be positive")
    return value


def _lines(form: Mapping[str, str], name: str, *, required: bool = False) -> tuple[str, ...]:
    raw = form.get(name, "")
    if len(raw) > 10_000:
        _unprocessable(f"{name} is too long")
    values = tuple(line.strip() for line in raw.splitlines() if line.strip())
    if required and not values:
        _unprocessable(f"{name} is required")
    return values


def _display(value: object) -> str:
    if isinstance(value, bool):
        return "Sim" if value else "Não"
    if isinstance(value, (list, tuple)):
        return ", ".join(str(item) for item in value)
    return str(value) if value is not None else "—"


def _escaped_values(values: tuple[str, ...]) -> str:
    if not values:
        return "—"
    return "<br>".join(_escape(value) for value in values)


def _basis_points(value: int) -> str:
    return f"{value / 100:.2f}%"


def _money(cents: int, currency: str) -> str:
    formatted = f"{_escape(currency)} {cents / 100:,.2f}"
    return formatted.replace(",", "X").replace(".", ",").replace("X", ".")


def _escape(value: object) -> str:
    return html.escape(str(value), quote=True)


def _unprocessable(message: str) -> NoReturn:
    raise HTTPException(status_code=422, detail=message)


__all__ = ["create_live_router"]
