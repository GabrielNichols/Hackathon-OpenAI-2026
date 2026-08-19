"""Concrete, durable application boundary for the Dev 4 live workflow."""

from __future__ import annotations

import hashlib
import hmac
from collections.abc import Callable, Mapping
from copy import deepcopy
from datetime import date, time
from typing import Any

from app.live.config import (
    LiveConfigurationError,
    LiveSettings,
    reject_fake_live_component,
)
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
from app.live.manual_link_repository import SqlAlchemyManualLinkDeliveryRepository
from app.live.uow import (
    SqlAlchemyExecutionUnitOfWork,
    SqlAlchemyExecutionUnitOfWorkFactory,
)
from app.modules.messaging.gateway import DeliveryState, GatewayMessageNotFound
from app.modules.messaging.manual_link import (
    ManualDeliveryAction,
    ManualLinkDeliveryAdapter,
    ManualLinkDeliveryRecord,
)
from app.modules.rfq.contracts import (
    CompareQuotesCommand,
    CreateRFQRoundCommand,
    QuoteSubmissionDTO,
    RequestApprovalCommand,
    SendAwardCommand,
    SendRFQRoundCommand,
)
from app.modules.rfq.service import ProcurementExecutionService
from app.modules.rfq.store import ExecutionStore
from app.shared.errors import DomainError, ErrorCode, require
from app.shared.runtime import Clock, SystemClock, payload_hash
from app.shared.tokens import SignedTokenService, TokenValidationError

SupplierNameResolver = Callable[[str], str]


class LiveExecutionRuntime:
    """Create transaction-scoped real service dependencies with no fallback."""

    def __init__(
        self,
        *,
        settings: LiveSettings,
        uow_factory: SqlAlchemyExecutionUnitOfWorkFactory,
        clock: Clock | None = None,
    ) -> None:
        self.settings = settings
        self.uow_factory = uow_factory
        if not uow_factory.state_protection_enabled:
            raise LiveConfigurationError(
                "live execution persistence must use authenticated encryption"
            )
        self.clock = clock or SystemClock()
        self.token_service = SignedTokenService(
            settings.token_secret,
            clock=self.clock,
        )

    def build(
        self,
        uow: SqlAlchemyExecutionUnitOfWork,
    ) -> tuple[
        SqlAlchemyManualLinkDeliveryRepository,
        ManualLinkDeliveryAdapter,
        ProcurementExecutionService,
    ]:
        if uow.session is None or uow.store is None:
            raise RuntimeError("live unit of work must be entered before wiring services")
        manual_repository = SqlAlchemyManualLinkDeliveryRepository(
            uow.session,
            pii_hash_secret=self.settings.pii_hash_secret,
        )
        gateway = ManualLinkDeliveryAdapter(
            repository=manual_repository,
            public_base_url=self.settings.public_base_url,
            clock=self.clock,
        )
        reject_fake_live_component(gateway, role="delivery gateway")
        reject_fake_live_component(uow.store, role="durable execution buffer")
        service = ProcurementExecutionService(
            store=uow.store,
            clock=self.clock,
            token_service=self.token_service,
            delivery_gateway=gateway,
        )
        return manual_repository, gateway, service


class DurableProcurementExecutionPort:
    """Transactional RFQ/decision port suitable for the Dev 3 adapter."""

    def __init__(self, runtime: LiveExecutionRuntime) -> None:
        self._runtime = runtime

    async def create_round(self, command: CreateRFQRoundCommand):
        return await self._run("create_round", command)

    async def send_round(self, command: SendRFQRoundCommand):
        return await self._run("send_round", command)

    async def get_delivery_status(self, rfq_round_id: str):
        return await self._run("get_delivery_status", rfq_round_id)

    async def get_quote_status(self, rfq_round_id: str):
        return await self._run("get_quote_status", rfq_round_id)

    async def compare(self, command: CompareQuotesCommand):
        return await self._run("compare", command)

    async def request_approval(self, command: RequestApprovalCommand):
        return await self._run("request_approval", command)

    async def get_approval_status(self, approval_id: str):
        return await self._run("get_approval_status", approval_id)

    async def send_award(self, command: SendAwardCommand):
        return await self._run("send_award", command)

    async def get_award_status(self, award_id: str):
        return await self._run("get_award_status", award_id)

    async def _run(self, method_name: str, argument: object):
        with self._runtime.uow_factory() as uow:
            _manual_repository, _gateway, service = self._runtime.build(uow)
            method = getattr(service, method_name)
            result = await method(argument)
            uow.commit()
            return result


class DurableLiveProcurementFacade:
    """Connect live human pages to Postgres-backed Dev 4 domain operations."""

    def __init__(
        self,
        runtime: LiveExecutionRuntime,
        *,
        supplier_name_resolver: SupplierNameResolver | None = None,
    ) -> None:
        self._runtime = runtime
        self._tenant_id = runtime.settings.tenant_id
        self._supplier_name = supplier_name_resolver or (lambda supplier_id: supplier_id)

    async def get_rfq(
        self,
        capability_token: str,
        *,
        evidence: RequestEvidence,
    ) -> SupplierRFQPage:
        del evidence
        with self._runtime.uow_factory() as uow:
            repository, _gateway, service = self._runtime.build(uow)
            record, internal_token, context = await self._rfq_binding(
                capability_token,
                uow,
                repository,
                service,
            )
            del internal_token
            recipient = _required_mapping_item(
                _store(uow).recipients,
                context.recipient_id,
                "RFQ recipient",
            )
            quote_id = recipient.get("quote_id")
            quote_is_final = False
            clarification_messages: tuple[str, ...] = ()
            if isinstance(quote_id, str):
                quote_record = _store(uow).quotes.get(quote_id)
                if isinstance(quote_record, Mapping):
                    quote = quote_record.get("dto")
                    quote_is_final = getattr(quote, "status", None) == "FINAL"
                    if getattr(quote, "status", None) == "NEEDS_CLARIFICATION":
                        codes = [
                            *getattr(quote, "validation_errors", ()),
                            *getattr(quote, "validation_warnings", ()),
                        ]
                        clarification_messages = tuple(
                            _clarification_message(code) for code in codes
                        )
            return SupplierRFQPage(
                rfq_round_id=context.rfq_round_id,
                supplier_id=context.supplier_id,
                supplier_name=self._supplier_name(context.supplier_id),
                response_deadline=context.response_deadline,
                requirements=context.requirements.model_dump(mode="python"),
                opened_at=record.delivered_at,
                quote_already_submitted=quote_is_final,
                clarification_messages=clarification_messages,
            )

    async def open_rfq(
        self,
        capability_token: str,
        *,
        idempotency_key: str,
        evidence: RequestEvidence,
    ) -> ActionReceipt:
        operation = self._operation("rfq.open")
        payload = {"capability": capability_token}
        with self._runtime.uow_factory() as uow:
            replay = _facade_replay(_store(uow), operation, idempotency_key, payload)
            if replay is not None:
                return _action_receipt(replay)
            repository, gateway, service = self._runtime.build(uow)
            _record, _internal_token, context = await self._rfq_binding(
                capability_token,
                uow,
                repository,
                service,
            )
            await gateway.confirm_supplier_open(
                capability_token,
                client_ip=evidence.ip_address,
                user_agent=evidence.user_agent,
            )
            batch = await service.get_delivery_status(context.rfq_round_id)
            delivery = next(
                item for item in batch.deliveries if item.recipient_id == context.recipient_id
            )
            require(
                delivery.status == "DELIVERED",
                ErrorCode.DEPENDENCY_UNAVAILABLE,
                "supplier opening was not durably confirmed",
            )
            receipt = ActionReceipt(
                resource_id=context.recipient_id,
                status="DELIVERED",
                message="Recebimento da RFQ confirmado",
            )
            _remember_facade(
                _store(uow), operation, idempotency_key, payload, _receipt_dict(receipt)
            )
            uow.commit()
            return receipt

    async def submit_quote(
        self,
        capability_token: str,
        submission: QuoteFormSubmission,
        *,
        idempotency_key: str,
        evidence: RequestEvidence,
    ) -> ActionReceipt:
        del evidence
        with self._runtime.uow_factory() as uow:
            repository, _gateway, service = self._runtime.build(uow)
            record, internal_token, _context = await self._rfq_binding(
                capability_token,
                uow,
                repository,
                service,
            )
            require(
                record.status == DeliveryState.DELIVERED,
                ErrorCode.INVALID_STATE,
                "RFQ must be explicitly opened before a proposal is submitted",
            )
            quote = await service.submit_quote(
                internal_token,
                QuoteSubmissionDTO(
                    availability_confirmed=submission.availability_confirmed,
                    subtotal_cents=submission.subtotal_cents,
                    delivery_fee_cents=submission.delivery_fee_cents,
                    other_fee_cents=submission.other_fee_cents,
                    total_cents=submission.total_cents,
                    included_items=list(submission.included_items),
                    substitutions=list(submission.substitutions),
                    invoice_available=submission.invoice_available,
                    vegetarian_status=submission.vegetarian_status,
                    vegan_status=submission.vegan_status,
                    gluten_free_status=submission.gluten_free_status,
                    cross_contamination_warning=submission.cross_contamination_warning,
                    no_single_use_plastic_confirmed=(
                        submission.no_single_use_plastic_confirmed
                    ),
                    valid_until=submission.valid_until,
                    cancellation_terms=submission.cancellation_terms,
                    respondent_name=submission.respondent_name,
                    respondent_contact=submission.respondent_contact,
                    supplier_confirmation=submission.supplier_confirmation,
                ),
                idempotency_key=idempotency_key,
            )
            uow.commit()
            message = "Proposta válida recebida"
            if quote.status == "NEEDS_CLARIFICATION":
                risks = ", ".join(quote.validation_errors) or "dados pendentes"
                message = f"Proposta recebida; ajuste solicitado: {risks}"
            return ActionReceipt(quote.quote_id, str(quote.status), message)

    async def get_approval(
        self,
        approval_id: str,
        *,
        actor: HumanActor,
        evidence: RequestEvidence,
    ) -> ApprovalPage:
        del evidence
        self._require_actor_tenant(actor)
        with self._runtime.uow_factory() as uow:
            _repository, _gateway, service = self._runtime.build(uow)
            approval = await service.get_approval_status(approval_id)
            require(
                approval.approver_user_id == actor.user_id,
                ErrorCode.POLICY_DENIED,
                "actor is not the authorized approver",
            )
            quote = _quote(_store(uow), approval.selected_quote.quote_id)
            comparison = _comparison(_store(uow), approval.comparison_id)
            summaries = tuple(
                (
                    f"{self._supplier_name(candidate.supplier_id)}: "
                    f"R$ {candidate.total_cents / 100:,.2f}; "
                    f"score {candidate.score_basis_points / 100:.2f}"
                )
                for candidate in comparison.candidates
            )
            return ApprovalPage(
                approval_id=approval.approval_id,
                approval_version=approval.approval_version,
                status=str(approval.status),
                procurement_request_id=approval.procurement_request_id,
                supplier_name=self._supplier_name(quote.supplier_id),
                quote_id=quote.quote_id,
                quote_version=quote.quote_version,
                total_cents=quote.total_cents,
                currency="BRL",
                comparison_summary=summaries,
            )

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
        del evidence
        self._require_actor_tenant(actor)
        with self._runtime.uow_factory() as uow:
            _repository, _gateway, service = self._runtime.build(uow)
            current = await service.get_approval_status(approval_id)
            require(
                current.approval_version == expected_version,
                ErrorCode.STALE_VERSION,
                "approval changed before the decision",
            )
            decided = await service.decide_approval(
                approval_id,
                actor_type="human",
                actor_id=actor.user_id,
                approve=approve,
                reason=reason,
                idempotency_key=idempotency_key,
            )
            uow.commit()
            return ActionReceipt(
                resource_id=decided.approval_id,
                status=str(decided.status),
                message="Decisão humana registrada",
            )

    async def get_award(
        self,
        capability_token: str,
        *,
        evidence: RequestEvidence,
    ) -> AwardPage:
        del evidence
        with self._runtime.uow_factory() as uow:
            repository, _gateway, service = self._runtime.build(uow)
            record, _internal_token, award = await self._award_binding(
                capability_token,
                uow,
                repository,
                service,
            )
            quote = _quote(_store(uow), award.approved_quote.quote_id)
            store = _store(uow)
            requirements = _round_for_quote(store, quote)["requirements"]
            terms_snapshot = _frozen_award_terms(store, award)
            return AwardPage(
                award_id=award.award_id,
                award_version=award.award_version,
                status=str(award.status),
                supplier_name=self._supplier_name(award.supplier_id),
                procurement_request_id=award.procurement_request_id,
                quote_id=award.approved_quote.quote_id,
                quote_version=award.approved_quote.quote_version,
                approved_total_cents=award.approved_total_cents,
                currency=award.currency,
                event_date=requirements.event_date,
                delivery_window=requirements.delivery_time.strftime("%H:%M"),
                people_count=requirements.people_count,
                reservation_status=str(award.reservation_status),
                terms_snapshot_hash=award.terms_snapshot_hash,
                terms_snapshot=terms_snapshot,
                opened_at=record.delivered_at,
            )

    async def open_award(
        self,
        capability_token: str,
        *,
        idempotency_key: str,
        evidence: RequestEvidence,
    ) -> ActionReceipt:
        operation = self._operation("award.open")
        payload = {"capability": capability_token}
        with self._runtime.uow_factory() as uow:
            replay = _facade_replay(_store(uow), operation, idempotency_key, payload)
            if replay is not None:
                return _action_receipt(replay)
            repository, gateway, service = self._runtime.build(uow)
            _record, _internal_token, award = await self._award_binding(
                capability_token,
                uow,
                repository,
                service,
            )
            await gateway.confirm_supplier_open(
                capability_token,
                client_ip=evidence.ip_address,
                user_agent=evidence.user_agent,
            )
            updated = await service.get_award_status(award.award_id)
            require(
                updated.status == "DELIVERED",
                ErrorCode.DEPENDENCY_UNAVAILABLE,
                "award opening was not durably confirmed",
            )
            receipt = ActionReceipt(
                resource_id=updated.award_id,
                status="DELIVERED",
                message="Recebimento do award confirmado; nenhuma aceitação foi inferida",
            )
            _remember_facade(
                _store(uow), operation, idempotency_key, payload, _receipt_dict(receipt)
            )
            uow.commit()
            return receipt

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
        del evidence
        with self._runtime.uow_factory() as uow:
            repository, _gateway, service = self._runtime.build(uow)
            record, internal_token, award = await self._award_binding(
                capability_token,
                uow,
                repository,
                service,
            )
            require(
                record.status == DeliveryState.DELIVERED,
                ErrorCode.INVALID_STATE,
                "award must be explicitly opened before a response",
            )
            if accept:
                updated = await service.accept_award(
                    internal_token,
                    respondent_name=respondent_name,
                    terms_snapshot_hash=terms_snapshot_hash,
                    terms_accepted=terms_accepted,
                    idempotency_key=idempotency_key,
                )
                message = "Award aceito; confirme a reserva de capacidade separadamente"
            else:
                updated = await service.decline_award(
                    internal_token,
                    respondent_name=respondent_name,
                    reason=reason or "",
                    idempotency_key=idempotency_key,
                )
                message = "Award recusado e motivo registrado"
            require(
                updated.award_id == award.award_id,
                ErrorCode.INVALID_RESPONSE_TOKEN,
                "capability resolved to another award",
            )
            uow.commit()
            return ActionReceipt(updated.award_id, str(updated.status), message)

    async def confirm_reservation(
        self,
        capability_token: str,
        submission: ReservationFormSubmission,
        *,
        idempotency_key: str,
        evidence: RequestEvidence,
    ) -> ActionReceipt:
        del evidence
        with self._runtime.uow_factory() as uow:
            repository, _gateway, service = self._runtime.build(uow)
            record, _internal_token, award = await self._award_binding(
                capability_token,
                uow,
                repository,
                service,
            )
            require(
                record.status == DeliveryState.DELIVERED,
                ErrorCode.INVALID_STATE,
                "award must be explicitly opened before reservation",
            )
            completed = await service.confirm_reservation(
                award.award_id,
                event_date=submission.event_date,
                delivery_window=submission.delivery_window,
                people_count=submission.people_count,
                confirmed_by=submission.confirmed_by,
                idempotency_key=idempotency_key,
            )
            uow.commit()
            return ActionReceipt(
                completed.award_id,
                "READY_FOR_CONTRACTING",
                "Aceite e capacidade real confirmados",
            )

    async def list_manual_deliveries(
        self,
        *,
        actor: HumanActor,
        evidence: RequestEvidence,
    ) -> tuple[ManualDeliverySummary, ...]:
        del evidence
        self._require_actor_tenant(actor)
        with self._runtime.uow_factory() as uow:
            repository, _gateway, _service = self._runtime.build(uow)
            records = await repository.list_records()
            return tuple(
                ManualDeliverySummary(
                    external_id=record.external_id,
                    kind=record.message_type,
                    supplier_name=self._supplier_name(record.supplier_id),
                    delivery_status=str(record.status),
                    created_at=record.accepted_at,
                )
                for record in records
                if self._record_belongs_to_tenant(record)
            )

    async def get_manual_delivery(
        self,
        external_id: str,
        *,
        actor: HumanActor,
        evidence: RequestEvidence,
    ) -> ManualDeliveryPage:
        del evidence
        self._require_actor_tenant(actor)
        with self._runtime.uow_factory() as uow:
            repository, _gateway, _service = self._runtime.build(uow)
            record = await self._manual_record(repository, external_id)
            activities = await repository.list_activities(external_id)
            last_send = next(
                (
                    activity
                    for activity in reversed(activities)
                    if activity.action == ManualDeliveryAction.SEND_RECORDED
                ),
                None,
            )
            return ManualDeliveryPage(
                external_id=record.external_id,
                kind=record.message_type,
                supplier_name=self._supplier_name(record.supplier_id),
                delivery_status=str(record.status),
                procurement_request_id=_procurement_request_for_record(
                    _store(uow), record
                ),
                created_at=record.accepted_at,
                opened_at=record.delivered_at,
                last_send_channel=last_send.channel if last_send else None,
                last_recipient_contact=("protegido" if last_send else None),
            )

    async def reveal_manual_delivery_link(
        self,
        external_id: str,
        *,
        actor: HumanActor,
        idempotency_key: str,
        evidence: RequestEvidence,
    ) -> ManualLinkReveal:
        del evidence
        self._require_actor_tenant(actor)
        operation = self._operation("manual-link.copy")
        payload = {"external_id": external_id, "actor_id": actor.user_id}
        with self._runtime.uow_factory() as uow:
            replay = _facade_replay(_store(uow), operation, idempotency_key, payload)
            repository, gateway, _service = self._runtime.build(uow)
            record = await self._manual_record(repository, external_id)
            if replay is None:
                await gateway.record_link_copied(
                    external_id,
                    actor_id=actor.user_id,
                    channel="other",
                )
                _remember_facade(
                    _store(uow),
                    operation,
                    idempotency_key,
                    payload,
                    {"external_id": external_id, "event_type": "LINK_COPIED"},
                )
                uow.commit()
            return ManualLinkReveal(record.external_id, record.public_link)

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
        del evidence
        self._require_actor_tenant(actor)
        operation = self._operation("manual-link.sent")
        payload = {
            "external_id": external_id,
            "actor_id": actor.user_id,
            "channel": channel,
            "recipient_contact": recipient_contact,
        }
        with self._runtime.uow_factory() as uow:
            replay = _facade_replay(_store(uow), operation, idempotency_key, payload)
            repository, gateway, _service = self._runtime.build(uow)
            await self._manual_record(repository, external_id)
            if replay is None:
                await gateway.record_sent(
                    external_id,
                    actor_id=actor.user_id,
                    channel=channel,
                    recipient_contact=recipient_contact,
                )
                result = {
                    "external_id": external_id,
                    "delivery_status": "SENT_TO_GATEWAY",
                    "message": "Envio manual registrado; aguardando abertura do fornecedor",
                }
                _remember_facade(
                    _store(uow), operation, idempotency_key, payload, result
                )
                uow.commit()
            return ManualSendReceipt(
                external_id=external_id,
                delivery_status="SENT_TO_GATEWAY",
                message="Envio manual registrado; aguardando abertura do fornecedor",
            )

    async def get_manual_delivery_activity(
        self,
        external_id: str,
        *,
        actor: HumanActor,
        evidence: RequestEvidence,
    ) -> ManualDeliveryActivityPage:
        del evidence
        self._require_actor_tenant(actor)
        with self._runtime.uow_factory() as uow:
            repository, _gateway, _service = self._runtime.build(uow)
            record = await self._manual_record(repository, external_id)
            activities = await repository.list_activities(external_id)
            return ManualDeliveryActivityPage(
                external_id=external_id,
                supplier_name=self._supplier_name(record.supplier_id),
                delivery_status=str(record.status),
                activities=tuple(
                    ManualDeliveryActivity(
                        event_type=activity.action.value,
                        occurred_at=activity.occurred_at,
                        actor_display_name=activity.actor_id,
                        detail=_activity_detail(activity.action, activity.channel),
                    )
                    for activity in activities
                ),
            )

    async def get_execution_evidence(
        self,
        procurement_request_id: str,
        *,
        actor: HumanActor,
        evidence: RequestEvidence,
    ) -> ExecutionEvidencePage:
        """Build a tenant-scoped projection without refreshing or committing state."""

        del evidence
        self._require_actor_tenant(actor)
        with self._runtime.uow_factory() as uow:
            repository, _gateway, _service = self._runtime.build(uow)
            store = _store(uow)
            rounds = [
                record
                for record in store.rounds.values()
                if _round_belongs_to_request(
                    record,
                    procurement_request_id=procurement_request_id,
                    tenant_id=self._tenant_id,
                )
            ]
            require(
                bool(rounds),
                ErrorCode.NOT_FOUND,
                "procurement request not found",
            )
            round_ids = {str(record["dto"].rfq_round_id) for record in rounds}
            recipient_ids = {
                str(recipient_id)
                for record in rounds
                for recipient_id in record.get("recipient_ids", ())
            }
            recipients = [
                record
                for recipient_id in recipient_ids
                if isinstance((record := store.recipients.get(recipient_id)), Mapping)
            ]
            quotes = [
                record["dto"]
                for record in store.quotes.values()
                if isinstance(record, Mapping)
                and getattr(record.get("dto"), "rfq_round_id", None) in round_ids
            ]
            quote_ids = {str(quote.quote_id) for quote in quotes}
            comparisons = [
                record["dto"]
                for record in store.comparisons.values()
                if isinstance(record, Mapping)
                and getattr(record.get("dto"), "procurement_request_id", None)
                == procurement_request_id
                and getattr(record.get("dto"), "rfq_round_id", None) in round_ids
            ]
            comparison_ids = {
                str(comparison.comparison_id) for comparison in comparisons
            }
            approvals = [
                record["dto"]
                for record in store.approvals.values()
                if isinstance(record, Mapping)
                and getattr(record.get("dto"), "procurement_request_id", None)
                == procurement_request_id
                and getattr(record.get("dto"), "comparison_id", None)
                in comparison_ids
            ]
            approval_ids = {str(approval.approval_id) for approval in approvals}
            awards = [
                record["dto"]
                for record in store.awards.values()
                if isinstance(record, Mapping)
                and getattr(record.get("dto"), "procurement_request_id", None)
                == procurement_request_id
                and getattr(record.get("dto"), "approval_id", None) in approval_ids
            ]
            award_ids = {str(award.award_id) for award in awards}
            reservations = [
                record
                for record in store.reservations.values()
                if isinstance(record, Mapping)
                and record.get("procurement_request_id") == procurement_request_id
                and record.get("award_id") in award_ids
            ]

            relevant_aggregate_ids = {
                procurement_request_id,
                *round_ids,
                *recipient_ids,
                *quote_ids,
                *comparison_ids,
                *approval_ids,
                *award_ids,
                *(str(item["reservation_id"]) for item in reservations),
            }
            domain_events = [
                event
                for event in store.audit_events
                if hmac.compare_digest(str(event.tenant_id), self._tenant_id)
                and str(event.aggregate_id) in relevant_aggregate_ids
            ]
            timeline = [
                ExecutionEvidenceTimelineItem(
                    occurred_at=event.occurred_at,
                    event_type=str(event.event_type),
                    actor_display_name=_actor_label(event.actor_id, actor),
                    source="DOMAIN",
                    detail=_audit_event_detail(
                        str(event.event_type),
                        str(event.aggregate_type),
                    ),
                )
                for event in domain_events
            ]

            manual_records = await repository.list_records()
            for record in manual_records:
                if not self._record_belongs_to_tenant(record):
                    continue
                try:
                    record_request_id = _procurement_request_for_record(store, record)
                except DomainError:
                    continue
                if record_request_id != procurement_request_id:
                    continue
                supplier_name = self._supplier_name(record.supplier_id)
                for activity in await repository.list_activities(record.external_id):
                    timeline.append(
                        ExecutionEvidenceTimelineItem(
                            occurred_at=activity.occurred_at,
                            event_type=activity.action.value,
                            actor_display_name=_actor_label(activity.actor_id, actor),
                            source="MANUAL_DELIVERY",
                            detail=(
                                f"{supplier_name}: "
                                f"{_activity_detail(activity.action, activity.channel)}"
                            ),
                        )
                    )

            latest_approval = _latest(approvals, "requested_at")
            latest_award = _latest(awards, "updated_at")
            clarification_events = [
                event
                for event in domain_events
                if str(event.event_type) == "QUOTE_NEEDS_CLARIFICATION"
            ]
            resolved_clarifications = [
                event
                for event in domain_events
                if str(event.event_type) == "CLARIFICATION_ANSWERED"
                and event.payload.get("resolved") is True
            ]
            final_status = store.procurement_status.get(procurement_request_id)
            require(
                isinstance(final_status, str),
                ErrorCode.NOT_FOUND,
                "procurement request status not found",
            )
            return ExecutionEvidencePage(
                procurement_request_id=procurement_request_id,
                final_status=final_status,
                confirmed_delivery_count=sum(
                    str(record.get("status")) == "DELIVERED" for record in recipients
                ),
                delivery_count=len(recipients),
                valid_quote_count=sum(
                    str(quote.status) == "FINAL" and bool(quote.eligible)
                    for quote in quotes
                ),
                quote_count=len(quotes),
                clarification_count=len(clarification_events),
                resolved_clarification_count=len(resolved_clarifications),
                approval_status=(
                    str(latest_approval.status) if latest_approval is not None else None
                ),
                approval_actor_display_name=(
                    _approval_actor(latest_approval, actor)
                    if latest_approval is not None
                    else None
                ),
                award_status=(
                    str(latest_award.status) if latest_award is not None else None
                ),
                reservation_status=(
                    str(latest_award.reservation_status)
                    if latest_award is not None
                    else None
                ),
                comparison_ids=tuple(
                    str(comparison.comparison_id)
                    for comparison in sorted(
                        comparisons,
                        key=lambda item: item.created_at.timestamp(),
                    )
                ),
                timeline=tuple(sorted(timeline, key=_timeline_sort_key)),
            )

    async def get_comparison(
        self,
        comparison_id: str,
        *,
        actor: HumanActor,
        evidence: RequestEvidence,
    ) -> ComparisonPage:
        """Project a deterministic comparison without refreshing or committing it."""

        del evidence
        self._require_actor_tenant(actor)
        with self._runtime.uow_factory() as uow:
            store = _store(uow)
            comparison = _comparison(store, comparison_id)
            round_record = _required_mapping_item(
                store.rounds,
                comparison.rfq_round_id,
                "RFQ round",
            )
            require(
                _round_belongs_to_request(
                    round_record,
                    procurement_request_id=comparison.procurement_request_id,
                    tenant_id=self._tenant_id,
                ),
                ErrorCode.NOT_FOUND,
                "comparison not found",
            )
            requirements = round_record.get("requirements")
            policy = round_record.get("policy")
            require(
                requirements is not None and policy is not None,
                ErrorCode.DEPENDENCY_UNAVAILABLE,
                "comparison snapshots are unavailable",
            )

            candidates: list[ComparisonCandidatePage] = []
            for candidate in comparison.candidates:
                quote = _matching_comparison_quote(
                    store,
                    comparison.rfq_round_id,
                    candidate.quote_id,
                    candidate.quote_version,
                    candidate.supplier_id,
                )
                quote_risks = tuple(getattr(quote, "validation_warnings", ()))
                if quote is None:
                    quote_risks = ("QUOTE_VERSION_DETAILS_UNAVAILABLE",)
                quote_errors = tuple(getattr(quote, "validation_errors", ()))
                candidates.append(
                    ComparisonCandidatePage(
                        quote_id=candidate.quote_id,
                        quote_version=candidate.quote_version,
                        supplier_id=candidate.supplier_id,
                        supplier_name=self._supplier_name(candidate.supplier_id),
                        eligible=bool(candidate.eligible),
                        total_cents=candidate.total_cents,
                        currency=candidate.currency,
                        price_per_person_cents=candidate.price_per_person_cents,
                        invoice_available=getattr(quote, "invoice_available", None),
                        valid_until=getattr(quote, "valid_until", None),
                        availability_confirmed=getattr(
                            quote,
                            "availability_confirmed",
                            None,
                        ),
                        no_single_use_plastic_confirmed=getattr(
                            quote,
                            "no_single_use_plastic_confirmed",
                            None,
                        ),
                        vegetarian_status=_optional_string_attribute(
                            quote,
                            "vegetarian_status",
                        ),
                        vegan_status=_optional_string_attribute(quote, "vegan_status"),
                        gluten_free_status=_optional_string_attribute(
                            quote,
                            "gluten_free_status",
                        ),
                        included_items=tuple(getattr(quote, "included_items", ())),
                        substitutions=tuple(getattr(quote, "substitutions", ())),
                        score_basis_points=candidate.score_basis_points,
                        score_components=tuple(
                            ComparisonScoreComponentPage(
                                criterion=component.criterion,
                                weight_percent=component.weight,
                                normalized_score_basis_points=(
                                    component.normalized_score_basis_points
                                ),
                                points_basis_points=component.points_basis_points,
                                reason=component.reason,
                                evidence_refs=tuple(component.evidence_refs),
                            )
                            for component in candidate.score_components
                        ),
                        disqualification_reasons=tuple(
                            dict.fromkeys(
                                (*candidate.disqualification_reasons, *quote_errors)
                            )
                        ),
                        risks=tuple(
                            dict.fromkeys((*candidate.risks, *quote_risks))
                        ),
                        evidence_refs=tuple(candidate.evidence_refs),
                    )
                )

            recommended = comparison.recommended_quote
            return ComparisonPage(
                comparison_id=comparison.comparison_id,
                comparison_version=comparison.comparison_version,
                procurement_request_id=comparison.procurement_request_id,
                rfq_round_id=comparison.rfq_round_id,
                quote_collection_version=comparison.quote_collection_version,
                status=str(comparison.status),
                recommended_quote_id=(recommended.quote_id if recommended else None),
                recommended_quote_version=(
                    recommended.quote_version if recommended else None
                ),
                created_at=comparison.created_at,
                requirements=_comparison_requirements(requirements),
                ranking_weights=dict(policy.ranking_weights),
                candidates=tuple(candidates),
            )

    async def _rfq_binding(
        self,
        capability: str,
        uow: SqlAlchemyExecutionUnitOfWork,
        repository: SqlAlchemyManualLinkDeliveryRepository,
        service: ProcurementExecutionService,
    ):
        record = await self._manual_record(repository, capability, expected_kind="rfq")
        recipient = _required_mapping_item(
            _store(uow).recipients,
            record.recipient_id,
            "RFQ recipient",
        )
        internal_token = recipient.get("response_token")
        if not isinstance(internal_token, str):
            raise DomainError(ErrorCode.INVALID_RESPONSE_TOKEN, "RFQ token is unavailable")
        _require_token_digest(record, internal_token)
        context = service.get_response_context(internal_token)
        require(
            context.supplier_id == record.supplier_id,
            ErrorCode.INVALID_RESPONSE_TOKEN,
            "RFQ capability supplier does not match",
        )
        return record, internal_token, context

    async def _award_binding(
        self,
        capability: str,
        uow: SqlAlchemyExecutionUnitOfWork,
        repository: SqlAlchemyManualLinkDeliveryRepository,
        service: ProcurementExecutionService,
    ):
        record = await self._manual_record(repository, capability, expected_kind="award")
        award_id = record.metadata.get("award_id")
        if not isinstance(award_id, str):
            raise DomainError(ErrorCode.INVALID_RESPONSE_TOKEN, "award binding is missing")
        award_record = _required_mapping_item(_store(uow).awards, award_id, "award")
        internal_token = award_record.get("response_token")
        award = award_record.get("dto")
        if not isinstance(internal_token, str) or award is None:
            raise DomainError(ErrorCode.INVALID_RESPONSE_TOKEN, "award token is unavailable")
        _require_token_digest(record, internal_token)
        try:
            claims = self._runtime.token_service.validate(
                internal_token,
                purpose="award_response",
                subject=award_id,
                now=self._runtime.clock.now(),
            )
        except TokenValidationError as error:
            raise DomainError(ErrorCode.INVALID_RESPONSE_TOKEN, str(error)) from error
        require(
            claims.metadata.get("tenant_id") == self._tenant_id
            and claims.metadata.get("supplier_id") == record.supplier_id,
            ErrorCode.INVALID_RESPONSE_TOKEN,
            "award capability claims do not match",
        )
        return record, internal_token, award

    async def _manual_record(
        self,
        repository: SqlAlchemyManualLinkDeliveryRepository,
        external_id: str,
        *,
        expected_kind: str | None = None,
    ) -> ManualLinkDeliveryRecord:
        record = await repository.get_by_external_id(external_id)
        if record is None or not self._record_belongs_to_tenant(record):
            raise GatewayMessageNotFound("manual delivery was not found")
        if expected_kind is not None and record.message_type != expected_kind:
            raise GatewayMessageNotFound("manual delivery was not found")
        return record

    def _record_belongs_to_tenant(self, record: ManualLinkDeliveryRecord) -> bool:
        return hmac.compare_digest(
            str(record.metadata.get("tenant_id", "")),
            self._tenant_id,
        )

    def _require_actor_tenant(self, actor: HumanActor) -> None:
        require(
            hmac.compare_digest(actor.tenant_id, self._tenant_id),
            ErrorCode.POLICY_DENIED,
            "actor belongs to another tenant",
        )

    def _operation(self, suffix: str) -> str:
        return f"{self._tenant_id}:live.{suffix}"


def _store(uow: SqlAlchemyExecutionUnitOfWork) -> ExecutionStore:
    if uow.store is None:
        raise RuntimeError("live unit of work is not active")
    return uow.store


def _required_mapping_item(
    mapping: Mapping[str, Any],
    item_id: str,
    label: str,
) -> Mapping[str, Any]:
    item = mapping.get(item_id)
    if not isinstance(item, Mapping):
        raise DomainError(ErrorCode.NOT_FOUND, f"{label} not found")
    return item


def _round_belongs_to_request(
    record: object,
    *,
    procurement_request_id: str,
    tenant_id: str,
) -> bool:
    if not isinstance(record, Mapping):
        return False
    dto = record.get("dto")
    return (
        getattr(dto, "procurement_request_id", None) == procurement_request_id
        and hmac.compare_digest(str(record.get("tenant_id", "")), tenant_id)
    )


def _latest(items: list[Any], timestamp_attribute: str) -> Any | None:
    if not items:
        return None
    return max(items, key=lambda item: getattr(item, timestamp_attribute).timestamp())


def _actor_label(actor_id: object, authenticated_actor: HumanActor) -> str:
    value = str(actor_id)
    if hmac.compare_digest(value, authenticated_actor.user_id):
        return authenticated_actor.display_name
    return value


def _approval_actor(approval: Any, authenticated_actor: HumanActor) -> str:
    actor_id = approval.decided_by_user_id or approval.approver_user_id
    return _actor_label(actor_id, authenticated_actor)


def _timeline_sort_key(item: ExecutionEvidenceTimelineItem) -> tuple[float, str, str]:
    return (item.occurred_at.timestamp(), item.source, item.event_type)


def _audit_event_detail(event_type: str, aggregate_type: str) -> str:
    descriptions = {
        "RFQ_ROUND_CREATED": "Rodada de cotação criada",
        "RFQ_ROUND_SENT": "Links de cotação preparados para envio",
        "RFQ_DELIVERY_CONFIRMED": "Recebimento da cotação confirmado",
        "QUOTE_SUBMITTED": "Proposta submetida pelo fornecedor",
        "QUOTE_VALIDATED": "Proposta validada pelas regras determinísticas",
        "QUOTE_NEEDS_CLARIFICATION": "Proposta solicitou esclarecimento",
        "CLARIFICATION_ANSWERED": "Esclarecimento respondido pelo fornecedor",
        "QUOTE_COMPARISON_CREATED": "Comparação determinística criada",
        "APPROVAL_REQUESTED": "Aprovação humana solicitada",
        "APPROVAL_GRANTED": "Aprovação humana concedida",
        "APPROVAL_REJECTED": "Aprovação humana rejeitada",
        "AWARD_CREATED": "Award preparado para o fornecedor selecionado",
        "AWARD_DELIVERY_CONFIRMED": "Recebimento do award confirmado",
        "SUPPLIER_ACCEPTED_AWARD": "Award aceito pelo fornecedor",
        "SUPPLIER_DECLINED_AWARD": "Award recusado pelo fornecedor",
        "CAPACITY_RESERVED": "Capacidade e data reservadas",
        "PROCUREMENT_READY_FOR_CONTRACTING": "Execução pronta para contratação",
    }
    return descriptions.get(event_type, f"Evento de {aggregate_type} registrado")


def _quote(store: ExecutionStore, quote_id: str):
    record = _required_mapping_item(store.quotes, quote_id, "quote")
    quote = record.get("dto")
    if quote is None:
        raise DomainError(ErrorCode.NOT_FOUND, "quote not found")
    return quote


def _comparison(store: ExecutionStore, comparison_id: str):
    record = _required_mapping_item(store.comparisons, comparison_id, "comparison")
    comparison = record.get("dto")
    if comparison is None:
        raise DomainError(ErrorCode.NOT_FOUND, "comparison not found")
    return comparison


def _frozen_award_terms(store: ExecutionStore, award: Any) -> FrozenAwardTerms:
    award_record = _required_mapping_item(store.awards, award.award_id, "award")
    raw_snapshot = award_record.get("terms_snapshot")
    require(
        isinstance(raw_snapshot, Mapping),
        ErrorCode.DEPENDENCY_UNAVAILABLE,
        "award terms snapshot is unavailable",
    )
    require(
        hmac.compare_digest(
            payload_hash(dict(raw_snapshot)),
            str(award.terms_snapshot_hash),
        ),
        ErrorCode.DEPENDENCY_UNAVAILABLE,
        "award terms snapshot integrity check failed",
    )

    quote_id = _snapshot_string(raw_snapshot, "quote_id")
    quote_version = _snapshot_integer(raw_snapshot, "quote_version")
    supplier_id = _snapshot_string(raw_snapshot, "supplier_id")
    total_cents = _snapshot_integer(raw_snapshot, "total_cents")
    currency = _snapshot_string(raw_snapshot, "currency")
    included_items = _snapshot_strings(raw_snapshot, "included_items")
    substitutions = _snapshot_strings(raw_snapshot, "substitutions")
    cancellation_terms = _snapshot_string(raw_snapshot, "cancellation_terms")
    event_date = raw_snapshot.get("event_date")
    delivery_time = raw_snapshot.get("delivery_time")
    people_count = _snapshot_integer(raw_snapshot, "people_count")
    require(
        type(event_date) is date and isinstance(delivery_time, time),
        ErrorCode.DEPENDENCY_UNAVAILABLE,
        "award terms snapshot has invalid scheduling fields",
    )
    require(
        quote_id == award.approved_quote.quote_id
        and quote_version == award.approved_quote.quote_version
        and supplier_id == award.supplier_id
        and total_cents == award.approved_total_cents
        and currency == award.currency,
        ErrorCode.DEPENDENCY_UNAVAILABLE,
        "award terms snapshot does not match the approved award",
    )
    return FrozenAwardTerms(
        quote_id=quote_id,
        quote_version=quote_version,
        supplier_id=supplier_id,
        total_cents=total_cents,
        currency=currency,
        included_items=included_items,
        substitutions=substitutions,
        cancellation_terms=cancellation_terms,
        event_date=event_date,
        delivery_time=delivery_time,
        people_count=people_count,
    )


def _snapshot_string(snapshot: Mapping[str, Any], key: str) -> str:
    value = snapshot.get(key)
    require(
        isinstance(value, str) and bool(value),
        ErrorCode.DEPENDENCY_UNAVAILABLE,
        f"award terms snapshot field {key} is invalid",
    )
    return value


def _snapshot_integer(snapshot: Mapping[str, Any], key: str) -> int:
    value = snapshot.get(key)
    require(
        isinstance(value, int) and not isinstance(value, bool) and value >= 0,
        ErrorCode.DEPENDENCY_UNAVAILABLE,
        f"award terms snapshot field {key} is invalid",
    )
    return value


def _snapshot_strings(snapshot: Mapping[str, Any], key: str) -> tuple[str, ...]:
    value = snapshot.get(key)
    require(
        isinstance(value, (list, tuple))
        and all(isinstance(item, str) and bool(item) for item in value),
        ErrorCode.DEPENDENCY_UNAVAILABLE,
        f"award terms snapshot field {key} is invalid",
    )
    return tuple(value)


def _matching_comparison_quote(
    store: ExecutionStore,
    rfq_round_id: str,
    quote_id: str,
    quote_version: int,
    supplier_id: str,
) -> Any | None:
    record = store.quotes.get(quote_id)
    if not isinstance(record, Mapping):
        return None
    quote = record.get("dto")
    if (
        getattr(quote, "quote_version", None) != quote_version
        or getattr(quote, "rfq_round_id", None) != rfq_round_id
        or getattr(quote, "supplier_id", None) != supplier_id
    ):
        return None
    return quote


def _optional_string_attribute(value: object, attribute: str) -> str | None:
    attribute_value = getattr(value, attribute, None)
    return str(attribute_value) if attribute_value is not None else None


def _comparison_requirements(requirements: Any) -> dict[str, object]:
    """Return only procurement fields relevant to an authenticated comparison."""

    return {
        "Descrição": requirements.description,
        "Categoria": requirements.category,
        "Data": requirements.event_date.isoformat(),
        "Horário": requirements.delivery_time.isoformat(),
        "Fuso horário": requirements.timezone,
        "Cidade": requirements.location_city,
        "Bairro": requirements.location_district,
        "Endereço": requirements.full_address,
        "Pessoas": requirements.people_count,
        "Teto total": requirements.maximum_total_cents,
        "Moeda": requirements.currency,
        "Vegetarianos": requirements.vegetarian_count,
        "Veganos": requirements.vegan_count,
        "Sem glúten": requirements.gluten_free_count,
        "Nota fiscal obrigatória": requirements.invoice_required,
        "Sem plástico de uso único": requirements.no_single_use_plastic,
        "Requisitos obrigatórios": tuple(requirements.mandatory_requirements),
    }


def _round_for_quote(store: ExecutionStore, quote: object) -> Mapping[str, Any]:
    rfq_round_id = getattr(quote, "rfq_round_id", None)
    if not isinstance(rfq_round_id, str):
        raise DomainError(ErrorCode.NOT_FOUND, "quote round not found")
    return _required_mapping_item(store.rounds, rfq_round_id, "RFQ round")


def _procurement_request_for_record(
    store: ExecutionStore,
    record: ManualLinkDeliveryRecord,
) -> str:
    if record.message_type == "rfq":
        round_id = record.metadata.get("rfq_round_id")
        if isinstance(round_id, str):
            round_record = _required_mapping_item(store.rounds, round_id, "RFQ round")
            return str(round_record["dto"].procurement_request_id)
    if record.message_type == "award":
        award_id = record.metadata.get("award_id")
        if isinstance(award_id, str):
            award_record = _required_mapping_item(store.awards, award_id, "award")
            return str(award_record["dto"].procurement_request_id)
    raise DomainError(ErrorCode.NOT_FOUND, "delivery procurement request not found")


def _require_token_digest(record: ManualLinkDeliveryRecord, internal_token: str) -> None:
    actual = hashlib.sha256(internal_token.encode("utf-8")).hexdigest()
    require(
        hmac.compare_digest(actual, record.response_token_digest),
        ErrorCode.INVALID_RESPONSE_TOKEN,
        "delivery token binding failed",
    )


def _facade_replay(
    store: ExecutionStore,
    operation: str,
    idempotency_key: str,
    payload: object,
) -> dict[str, Any] | None:
    existing = store.idempotency.get((operation, idempotency_key))
    if existing is None:
        return None
    fingerprint, result = existing
    require(
        fingerprint == payload_hash(payload),
        ErrorCode.IDEMPOTENCY_CONFLICT,
        "idempotency key is already bound to another live action",
    )
    if not isinstance(result, dict):
        raise DomainError(
            ErrorCode.DEPENDENCY_UNAVAILABLE,
            "persisted live action receipt is malformed",
        )
    return deepcopy(result)


def _remember_facade(
    store: ExecutionStore,
    operation: str,
    idempotency_key: str,
    payload: object,
    result: dict[str, Any],
) -> None:
    store.idempotency[(operation, idempotency_key)] = (
        payload_hash(payload),
        deepcopy(result),
    )


def _receipt_dict(receipt: ActionReceipt) -> dict[str, str]:
    return {
        "resource_id": receipt.resource_id,
        "status": receipt.status,
        "message": receipt.message,
    }


def _action_receipt(value: Mapping[str, Any]) -> ActionReceipt:
    return ActionReceipt(
        resource_id=str(value["resource_id"]),
        status=str(value["status"]),
        message=str(value["message"]),
    )


def _activity_detail(action: ManualDeliveryAction, channel: str) -> str:
    descriptions = {
        ManualDeliveryAction.LINK_CREATED: "Link individual preparado",
        ManualDeliveryAction.LINK_COPIED: "Link revelado ao operador",
        ManualDeliveryAction.SEND_RECORDED: f"Envio manual registrado via {channel}",
        ManualDeliveryAction.SUPPLIER_OPENED: "Fornecedor confirmou a abertura",
    }
    return descriptions[action]


def _clarification_message(code: str) -> str:
    messages = {
        "AVAILABILITY_NOT_CONFIRMED": "Confirme a disponibilidade para a data solicitada.",
        "BUDGET_LIMIT_EXCEEDED": "Revise o valor para respeitar o orçamento máximo.",
        "INVOICE_REQUIREMENT_NOT_MET": "Confirme a emissão de nota fiscal.",
        "VEGETARIAN_REQUIREMENT_NOT_CONFIRMED": (
            "Confirme o atendimento das opções vegetarianas."
        ),
        "VEGAN_REQUIREMENT_NOT_CONFIRMED": "Confirme o atendimento das opções veganas.",
        "GLUTEN_FREE_REQUIREMENT_NOT_CONFIRMED": (
            "Confirme o atendimento das opções sem glúten."
        ),
        "NO_SINGLE_USE_PLASTIC_REQUIREMENT_NOT_MET": (
            "Confirme que a entrega não usará plástico descartável."
        ),
        "CROSS_CONTAMINATION_INFORMATION_MISSING": (
            "Informe o risco de contaminação cruzada."
        ),
    }
    return messages.get(code, f"Revise o item indicado: {code}")


__all__ = [
    "DurableLiveProcurementFacade",
    "DurableProcurementExecutionPort",
    "LiveExecutionRuntime",
]
