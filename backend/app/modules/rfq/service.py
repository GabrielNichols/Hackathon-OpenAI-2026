from __future__ import annotations

from copy import deepcopy
from datetime import date
from typing import Any

from app.modules.comparison.scoring import score_quotes
from app.modules.messaging.gateway import (
    DeliveryState,
    FakeDeliveryGateway,
    GatewayIdempotencyConflict,
    OutboundMessage,
)
from app.modules.quotes.rules import validate_quote_submission
from app.modules.rfq.contracts import (
    ActorType,
    ApprovalDTO,
    ApprovalStatus,
    AuditEventDTO,
    AwardDTO,
    AwardStatus,
    CommandContextDTO,
    CompareQuotesCommand,
    ComparisonStatus,
    CreateRFQRoundCommand,
    DeliveryBatchDTO,
    DeliveryDTO,
    DeliveryStatus,
    QuoteCandidateDTO,
    QuoteCollectionStatusDTO,
    QuoteComparisonDTO,
    QuoteDTO,
    QuoteRefDTO,
    QuoteStatus,
    QuoteSubmissionDTO,
    RequestApprovalCommand,
    ReservationStatus,
    RFQResponseContextDTO,
    RFQRoundDTO,
    RFQRoundStatus,
    ScoreComponentDTO,
    SendAwardCommand,
    SendRFQRoundCommand,
)
from app.modules.rfq.store import InMemoryExecutionStore
from app.shared.errors import DomainError, ErrorCode, require
from app.shared.runtime import Clock, payload_hash
from app.shared.tokens import SignedTokenService, TokenValidationError


class ProcurementExecutionService:
    """In-memory Dev 4 prototype implementing the Dev 3 execution ports.

    The service owns workflow rules but not infrastructure. Its store, clock,
    token service and delivery gateway are injected so production adapters can
    replace all prototype dependencies after the contract freeze.
    """

    def __init__(
        self,
        *,
        store: InMemoryExecutionStore,
        clock: Clock,
        token_service: SignedTokenService,
        delivery_gateway: FakeDeliveryGateway,
    ) -> None:
        self.store = store
        self.clock = clock
        self.token_service = token_service
        self.delivery_gateway = delivery_gateway
        self._id_counters: dict[str, int] = {}

    @property
    def audit_events(self) -> list[AuditEventDTO]:
        return self.store.audit_events

    async def create_round(self, command: CreateRFQRoundCommand) -> RFQRoundDTO:
        replay = self._idempotent_replay("rfq.create", command.context, command)
        if replay is not None:
            return replay
        require(
            command.response_deadline > self.clock.now(),
            ErrorCode.VALIDATION_ERROR,
            "response_deadline must be in the future",
        )

        round_id = self._new_id("rfq")
        requirements = command.requirements.model_copy(deep=True)
        policy = command.execution_policy.model_copy(deep=True)
        dto = RFQRoundDTO(
            rfq_round_id=round_id,
            procurement_request_id=command.procurement_request_id,
            request_version=command.request_version,
            round_version=1,
            status=RFQRoundStatus.DRAFT,
            recipient_count=len(command.recipient_supplier_ids),
            response_deadline=command.response_deadline,
            requirements_snapshot_hash=payload_hash(requirements),
            policy_snapshot_hash=payload_hash(policy),
            created_at=self.clock.now(),
        )
        recipient_ids: list[str] = []
        for supplier_id in command.recipient_supplier_ids:
            recipient_id = self._new_id("recipient")
            recipient_ids.append(recipient_id)
            self.store.recipients[recipient_id] = {
                "recipient_id": recipient_id,
                "rfq_round_id": round_id,
                "supplier_id": supplier_id,
                "channel": "manual_link",
                "status": DeliveryStatus.PENDING,
                "external_id": None,
                "delivered_at": None,
                "failure_code": None,
                "response_token": None,
                "quote_id": None,
                "delivery_event_emitted": False,
            }
        self.store.rounds[round_id] = {
            "dto": dto,
            "tenant_id": command.context.tenant_id,
            "requirements": requirements,
            "policy": policy,
            "recipient_ids": recipient_ids,
            "collection_version": 0,
        }
        self.store.procurement_status[command.procurement_request_id] = "RFQ_DRAFT"
        self._audit(
            "RFQ_ROUND_CREATED",
            "rfq_round",
            round_id,
            command.context,
            {"recipient_count": len(recipient_ids)},
        )
        self._remember_idempotency("rfq.create", command.context, command, dto)
        return dto

    async def send_round(self, command: SendRFQRoundCommand) -> DeliveryBatchDTO:
        replay = self._idempotent_replay("rfq.send", command.context, command)
        if replay is not None:
            return replay
        round_record = self._round(command.rfq_round_id)
        round_dto: RFQRoundDTO = round_record["dto"]
        require(
            round_dto.round_version == command.expected_round_version,
            ErrorCode.STALE_VERSION,
            "RFQ round version changed",
        )
        require(
            round_dto.status not in {RFQRoundStatus.CLOSED, RFQRoundStatus.FAILED},
            ErrorCode.INVALID_STATE,
            "closed or failed RFQ rounds cannot be sent",
        )

        for recipient_id in round_record["recipient_ids"]:
            recipient = self.store.recipients[recipient_id]
            token = recipient["response_token"]
            if token is None:
                token = self.token_service.issue(
                    "rfq_response",
                    recipient_id,
                    expires_at=round_dto.response_deadline,
                    metadata={
                        "rfq_round_id": round_dto.rfq_round_id,
                        "supplier_id": recipient["supplier_id"],
                        "tenant_id": round_record["tenant_id"],
                    },
                )
                recipient["response_token"] = token
            try:
                result = await self.delivery_gateway.send(
                    OutboundMessage(
                        idempotency_key=(f"rfq:{round_dto.rfq_round_id}:recipient:{recipient_id}"),
                        recipient_id=recipient_id,
                        supplier_id=recipient["supplier_id"],
                        channel=str(command.channel),
                        message_type="rfq",
                        body=f"RFQ {round_dto.rfq_round_id} response link",
                        response_token=token,
                        metadata={
                            "rfq_round_id": round_dto.rfq_round_id,
                            "tenant_id": round_record["tenant_id"],
                        },
                    )
                )
            except GatewayIdempotencyConflict as error:
                raise DomainError(
                    ErrorCode.IDEMPOTENCY_CONFLICT,
                    "RFQ delivery is already bound to another payload",
                ) from error
            recipient["external_id"] = result.external_id
            recipient["channel"] = str(command.channel)
            recipient["status"] = str(result.status)
            if result.status == DeliveryState.DELIVERED:
                recipient["delivered_at"] = self.clock.now()

        batch = await self._refresh_delivery_batch(round_dto.rfq_round_id)
        self._remember_idempotency("rfq.send", command.context, command, batch)
        return batch

    async def get_delivery_status(self, rfq_round_id: str) -> DeliveryBatchDTO:
        return await self._refresh_delivery_batch(rfq_round_id)

    def get_round_requirements(self, rfq_round_id: str) -> dict[str, Any]:
        return deepcopy(self._round(rfq_round_id)["requirements"].model_dump(mode="python"))

    def get_response_context(self, token: str) -> RFQResponseContextDTO:
        claims = self._validate_token(token, purpose="rfq_response")
        recipient = self._recipient(claims.subject)
        round_record = self._round(recipient["rfq_round_id"])
        require(
            claims.metadata.get("tenant_id") == round_record["tenant_id"],
            ErrorCode.INVALID_RESPONSE_TOKEN,
            "response token tenant does not match the RFQ",
        )
        return RFQResponseContextDTO(
            rfq_round_id=recipient["rfq_round_id"],
            recipient_id=recipient["recipient_id"],
            supplier_id=recipient["supplier_id"],
            requirements=round_record["requirements"],
            response_deadline=round_record["dto"].response_deadline,
        )

    async def submit_quote(self, token: str, submission: QuoteSubmissionDTO) -> QuoteDTO:
        context = self.get_response_context(token)
        recipient = self._recipient(context.recipient_id)
        require(
            recipient["external_id"] is not None,
            ErrorCode.INVALID_STATE,
            "RFQ must be sent before a quote can be submitted",
        )
        delivery = await self.delivery_gateway.get_status(recipient["external_id"])
        require(
            delivery.status == DeliveryState.DELIVERED,
            ErrorCode.INVALID_STATE,
            "RFQ delivery must be confirmed before a quote can be submitted",
        )
        require(
            context.response_deadline > self.clock.now(),
            ErrorCode.QUOTE_EXPIRED,
            "RFQ response deadline expired",
        )
        round_record = self._round(context.rfq_round_id)
        requirements = round_record["requirements"]
        policy_maximum = round_record["policy"].maximum_total_cents
        effective_requirements = requirements.model_copy(
            update={
                "maximum_total_cents": (
                    policy_maximum
                    if policy_maximum is not None
                    else requirements.maximum_total_cents
                )
            }
        )
        validation = validate_quote_submission(
            submission,
            effective_requirements,
            self.clock.now(),
        )
        disqualifying_codes = {
            "AVAILABILITY_NOT_CONFIRMED",
            "BUDGET_LIMIT_EXCEEDED",
            "INVOICE_REQUIREMENT_NOT_MET",
            "VEGETARIAN_REQUIREMENT_NOT_CONFIRMED",
            "VEGAN_REQUIREMENT_NOT_CONFIRMED",
            "GLUTEN_FREE_REQUIREMENT_NOT_CONFIRMED",
        }
        errors = [risk for risk in validation.risks if risk in disqualifying_codes]
        warnings = [risk for risk in validation.risks if risk not in disqualifying_codes]

        existing_quote = (
            self.store.quotes.get(recipient["quote_id"]) if recipient["quote_id"] else None
        )
        submission_fingerprint = payload_hash(submission)
        if (
            existing_quote is not None
            and existing_quote.get("submission_hash") == submission_fingerprint
        ):
            return existing_quote["dto"].model_copy(deep=True)
        quote_id = existing_quote["dto"].quote_id if existing_quote else self._new_id("quote")
        quote_version = existing_quote["dto"].quote_version + 1 if existing_quote else 1
        submission_payload = submission.model_dump()
        submission_payload["total_cents"] = validation.total_cents
        dto = QuoteDTO(
            **submission_payload,
            quote_id=quote_id,
            quote_version=quote_version,
            rfq_round_id=context.rfq_round_id,
            recipient_id=context.recipient_id,
            supplier_id=context.supplier_id,
            status=(QuoteStatus.FINAL if validation.eligible else QuoteStatus.NEEDS_CLARIFICATION),
            price_per_person_cents=validation.price_per_person_cents,
            eligible=validation.eligible,
            validation_errors=errors,
            validation_warnings=warnings,
            submitted_at=self.clock.now(),
        )
        self.store.quotes[quote_id] = {
            "dto": dto,
            "submission_hash": submission_fingerprint,
        }
        self.store.quote_versions[(quote_id, quote_version)] = dto.model_copy(deep=True)
        recipient["quote_id"] = quote_id
        round_record["collection_version"] += 1
        audit_context = CommandContextDTO(
            tenant_id=round_record["tenant_id"],
            idempotency_key=f"quote:{quote_id}:v{quote_version}",
            correlation_id=f"cor:{context.rfq_round_id}",
            actor_type=ActorType.HUMAN,
            actor_id=context.supplier_id,
        )
        self._audit("QUOTE_SUBMITTED", "quote", quote_id, audit_context, {"version": quote_version})
        self._audit(
            "QUOTE_VALIDATED" if dto.eligible else "QUOTE_NEEDS_CLARIFICATION",
            "quote",
            quote_id,
            audit_context,
            {"errors": errors},
        )
        return dto

    async def get_quote_status(self, rfq_round_id: str) -> QuoteCollectionStatusDTO:
        round_record = self._round(rfq_round_id)
        self._expire_quotes(round_record)
        quotes = self._quotes_for_round(rfq_round_id)
        valid_count = sum(quote.status == QuoteStatus.FINAL and quote.eligible for quote in quotes)
        clarification_count = sum(
            quote.status == QuoteStatus.NEEDS_CLARIFICATION for quote in quotes
        )
        declined_count = sum(quote.status == QuoteStatus.DECLINED for quote in quotes)
        expected = len(round_record["recipient_ids"])
        submitted = len(quotes)
        return QuoteCollectionStatusDTO(
            rfq_round_id=rfq_round_id,
            collection_version=round_record["collection_version"],
            response_deadline=round_record["dto"].response_deadline,
            expected_count=expected,
            submitted_count=submitted,
            valid_count=valid_count,
            needs_clarification_count=clarification_count,
            declined_count=declined_count,
            pending_count=max(0, expected - submitted),
            ready_for_comparison=valid_count > 0,
            updated_at=self.clock.now(),
        )

    async def compare(self, command: CompareQuotesCommand) -> QuoteComparisonDTO:
        replay = self._idempotent_replay("quotes.compare", command.context, command)
        if replay is not None:
            return replay
        round_record = self._round(command.rfq_round_id)
        self._expire_quotes(round_record)
        require(
            round_record["dto"].procurement_request_id == command.procurement_request_id,
            ErrorCode.VALIDATION_ERROR,
            "RFQ round belongs to another procurement request",
        )
        require(
            round_record["collection_version"] == command.expected_quote_collection_version,
            ErrorCode.STALE_VERSION,
            "quote collection changed",
        )
        quotes = self._quotes_for_round(command.rfq_round_id)
        require(
            any(quote.eligible for quote in quotes),
            ErrorCode.INVALID_STATE,
            "no eligible quotes to compare",
        )
        policy = round_record["policy"]
        scored_quotes = score_quotes(quotes, policy)
        candidates = []
        for scored in scored_quotes:
            candidates.append(
                QuoteCandidateDTO(
                    quote_id=scored.quote_id,
                    quote_version=scored.quote_version,
                    supplier_id=scored.supplier_id,
                    eligible=scored.eligible,
                    total_cents=scored.total_cents,
                    price_per_person_cents=scored.price_per_person_cents,
                    score_basis_points=scored.score_basis_points,
                    score_components=[
                        ScoreComponentDTO(
                            criterion=component.criterion,
                            weight=component.weight,
                            normalized_score_basis_points=(component.normalized_score_basis_points),
                            points_basis_points=component.points_basis_points,
                            reason=component.reason,
                            evidence_refs=list(component.evidence_refs),
                        )
                        for component in scored.components
                    ],
                    disqualification_reasons=list(scored.disqualification_reasons),
                    risks=list(scored.risks),
                    evidence_refs=[f"quote:{scored.quote_id}:v{scored.quote_version}"],
                )
            )
        comparison_id = self._new_id("comparison")
        selected = next(candidate for candidate in candidates if candidate.eligible)
        dto = QuoteComparisonDTO(
            comparison_id=comparison_id,
            comparison_version=1,
            procurement_request_id=command.procurement_request_id,
            rfq_round_id=command.rfq_round_id,
            quote_collection_version=round_record["collection_version"],
            status=ComparisonStatus.READY,
            candidates=candidates,
            recommended_quote=QuoteRefDTO(
                quote_id=selected.quote_id,
                quote_version=selected.quote_version,
            ),
            input_hash=payload_hash(
                sorted((quote.quote_id, quote.quote_version) for quote in quotes)
            ),
            created_at=self.clock.now(),
        )
        self.store.comparisons[comparison_id] = {"dto": dto}
        self.store.procurement_status[command.procurement_request_id] = "AWAITING_APPROVAL"
        self._audit(
            "QUOTE_COMPARISON_CREATED",
            "comparison",
            comparison_id,
            command.context,
            {"recommended_quote_id": selected.quote_id},
        )
        self._remember_idempotency("quotes.compare", command.context, command, dto)
        return dto

    async def request_approval(self, command: RequestApprovalCommand) -> ApprovalDTO:
        replay = self._idempotent_replay("approval.request", command.context, command)
        if replay is not None:
            return replay
        comparison = self._comparison(command.comparison_id)
        require(
            comparison.procurement_request_id == command.procurement_request_id,
            ErrorCode.VALIDATION_ERROR,
            "comparison belongs to another procurement request",
        )
        require(
            comparison.comparison_version == command.comparison_version,
            ErrorCode.STALE_VERSION,
            "comparison version changed",
        )
        selected_candidate = next(
            (
                candidate
                for candidate in comparison.candidates
                if candidate.quote_id == command.selected_quote.quote_id
                and candidate.quote_version == command.selected_quote.quote_version
            ),
            None,
        )
        require(
            selected_candidate is not None and selected_candidate.eligible,
            ErrorCode.VALIDATION_ERROR,
            "selected quote is not an eligible comparison candidate",
        )
        current_quote = self._quote(command.selected_quote.quote_id)
        require(
            current_quote.quote_version == command.selected_quote.quote_version,
            ErrorCode.STALE_VERSION,
            "selected quote changed after the comparison",
        )
        require(
            current_quote.eligible
            and current_quote.status == QuoteStatus.FINAL
            and current_quote.valid_until > self.clock.now(),
            ErrorCode.QUOTE_EXPIRED,
            "selected quote is no longer valid",
        )
        policy = self._round(comparison.rfq_round_id)["policy"]
        require(
            command.approver_user_id == policy.approver_user_id,
            ErrorCode.POLICY_DENIED,
            "approver must match the frozen execution policy",
        )
        approval_id = self._new_id("approval")
        dto = ApprovalDTO(
            approval_id=approval_id,
            approval_version=1,
            status=ApprovalStatus.REQUESTED,
            procurement_request_id=command.procurement_request_id,
            comparison_id=comparison.comparison_id,
            comparison_version=comparison.comparison_version,
            selected_quote=command.selected_quote,
            approver_user_id=command.approver_user_id,
            requested_at=self.clock.now(),
        )
        self.store.approvals[approval_id] = {"dto": dto}
        self._audit("APPROVAL_REQUESTED", "approval", approval_id, command.context, {})
        self._remember_idempotency("approval.request", command.context, command, dto)
        return dto

    async def get_approval_status(self, approval_id: str) -> ApprovalDTO:
        return self._approval(approval_id)

    async def decide_approval(
        self,
        approval_id: str,
        *,
        actor_type: str,
        actor_id: str,
        approve: bool,
        idempotency_key: str,
        reason: str | None = None,
    ) -> ApprovalDTO:
        operation = "approval.decide"
        payload = {
            "approval_id": approval_id,
            "actor_type": actor_type,
            "actor_id": actor_id,
            "approve": approve,
            "reason": reason,
        }
        replay = self._raw_idempotent_replay(operation, idempotency_key, payload)
        if replay is not None:
            return replay
        require(actor_type == "human", ErrorCode.POLICY_DENIED, "only a human can approve")
        current = self._approval(approval_id)
        require(
            current.approver_user_id == actor_id,
            ErrorCode.POLICY_DENIED,
            "actor is not the authorized approver",
        )
        require(
            current.status == ApprovalStatus.REQUESTED,
            ErrorCode.INVALID_STATE,
            "approval is already decided",
        )
        selected_quote = self._quote(current.selected_quote.quote_id)
        require(
            selected_quote.quote_version == current.selected_quote.quote_version,
            ErrorCode.STALE_VERSION,
            "selected quote changed after approval was requested",
        )
        require(
            selected_quote.eligible
            and selected_quote.status == QuoteStatus.FINAL
            and selected_quote.valid_until > self.clock.now(),
            ErrorCode.QUOTE_EXPIRED,
            "selected quote is no longer valid",
        )
        updated = current.model_copy(
            update={
                "approval_version": current.approval_version + 1,
                "status": ApprovalStatus.APPROVED if approve else ApprovalStatus.REJECTED,
                "decided_by_user_id": actor_id,
                "reason": reason,
                "decided_at": self.clock.now(),
            }
        )
        self.store.approvals[approval_id]["dto"] = updated
        context = CommandContextDTO(
            tenant_id=self._tenant_for_request(current.procurement_request_id),
            idempotency_key=idempotency_key,
            correlation_id=f"cor:{current.procurement_request_id}",
            actor_type=ActorType.HUMAN,
            actor_id=actor_id,
        )
        self._audit(
            "APPROVAL_GRANTED" if approve else "APPROVAL_REJECTED",
            "approval",
            approval_id,
            context,
            {"selected_quote": current.selected_quote.model_dump()},
        )
        if approve:
            self.store.procurement_status[current.procurement_request_id] = "APPROVED"
        self._remember_raw_idempotency(operation, idempotency_key, payload, updated)
        return updated

    async def send_award(self, command: SendAwardCommand) -> AwardDTO:
        replay = self._idempotent_replay("award.send", command.context, command)
        if replay is not None:
            return replay
        approval = self._approval(command.approval_id)
        require(
            approval.procurement_request_id == command.procurement_request_id,
            ErrorCode.VALIDATION_ERROR,
            "approval belongs to another procurement request",
        )
        require(
            approval.approval_version == command.expected_approval_version,
            ErrorCode.STALE_VERSION,
            "approval version changed",
        )
        require(
            approval.status == ApprovalStatus.APPROVED,
            ErrorCode.INVALID_STATE,
            "award requires an approved quote",
        )
        quote = self._quote(approval.selected_quote.quote_id)
        require(
            quote.quote_version == approval.selected_quote.quote_version,
            ErrorCode.STALE_VERSION,
            "approved quote version is stale",
        )
        require(
            quote.eligible
            and quote.status == QuoteStatus.FINAL
            and quote.valid_until > self.clock.now(),
            ErrorCode.QUOTE_EXPIRED,
            "approved quote is no longer valid",
        )
        existing_award_id = self.store.award_by_approval_id.get(approval.approval_id)
        if existing_award_id is not None:
            existing = self._award_record(existing_award_id)["dto"].model_copy(
                update={"idempotent_replay": True}
            )
            self._remember_idempotency("award.send", command.context, command, existing)
            return existing
        award_id = self._new_id("award")
        token = self.token_service.issue(
            "award_response",
            award_id,
            metadata={
                "supplier_id": quote.supplier_id,
                "tenant_id": self._tenant_for_request(approval.procurement_request_id),
            },
        )
        try:
            result = await self.delivery_gateway.send(
                OutboundMessage(
                    idempotency_key=(
                        f"award:approval:{approval.approval_id}:v{approval.approval_version}"
                    ),
                    recipient_id=quote.recipient_id,
                    supplier_id=quote.supplier_id,
                    channel="manual_link",
                    message_type="award",
                    body=f"Award {award_id} acceptance link",
                    response_token=token,
                    metadata={"approval_id": approval.approval_id},
                )
            )
        except GatewayIdempotencyConflict as error:
            raise DomainError(
                ErrorCode.IDEMPOTENCY_CONFLICT,
                "award delivery is already bound to another payload",
            ) from error
        status = (
            AwardStatus.DELIVERED
            if result.status == DeliveryState.DELIVERED
            else AwardStatus.SENT_TO_GATEWAY
        )
        dto = AwardDTO(
            award_id=award_id,
            award_version=1,
            procurement_request_id=command.procurement_request_id,
            supplier_id=quote.supplier_id,
            approved_quote=approval.selected_quote,
            approval_id=approval.approval_id,
            approved_total_cents=quote.total_cents,
            status=status,
            delivered_at=self.clock.now() if status == AwardStatus.DELIVERED else None,
            updated_at=self.clock.now(),
        )
        self.store.awards[award_id] = {
            "dto": dto,
            "external_id": result.external_id,
            "response_token": token,
            "delivery_event_emitted": status == AwardStatus.DELIVERED,
            "accepted_by": None,
        }
        self.store.award_by_approval_id[approval.approval_id] = award_id
        self._audit("AWARD_CREATED", "award", award_id, command.context, {})
        if status == AwardStatus.DELIVERED:
            self._audit("AWARD_DELIVERY_CONFIRMED", "award", award_id, command.context, {})
            self.store.procurement_status[command.procurement_request_id] = "AWARD_SENT"
        self._remember_idempotency("award.send", command.context, command, dto)
        return dto

    async def get_award_status(self, award_id: str) -> AwardDTO:
        record = self._award_record(award_id)
        current: AwardDTO = record["dto"]
        status = await self.delivery_gateway.get_status(record["external_id"])
        if (
            status.status == DeliveryState.DELIVERED
            and current.status == AwardStatus.SENT_TO_GATEWAY
        ):
            current = current.model_copy(
                update={
                    "award_version": current.award_version + 1,
                    "status": AwardStatus.DELIVERED,
                    "delivered_at": status.delivered_at or self.clock.now(),
                    "updated_at": self.clock.now(),
                }
            )
            record["dto"] = current
            self.store.procurement_status[current.procurement_request_id] = "AWARD_SENT"
            if not record["delivery_event_emitted"]:
                self._audit(
                    "AWARD_DELIVERY_CONFIRMED",
                    "award",
                    award_id,
                    self._system_context(current.procurement_request_id),
                    {"external_id": record["external_id"]},
                )
                record["delivery_event_emitted"] = True
        elif (
            status.status == DeliveryState.FAILED and current.status == AwardStatus.SENT_TO_GATEWAY
        ):
            current = current.model_copy(
                update={
                    "award_version": current.award_version + 1,
                    "status": AwardStatus.FAILED,
                    "updated_at": self.clock.now(),
                }
            )
            record["dto"] = current
            self._audit(
                "AWARD_DELIVERY_FAILED",
                "award",
                award_id,
                self._system_context(current.procurement_request_id),
                {
                    "external_id": record["external_id"],
                    "reason": status.failure_reason,
                },
            )
        return current

    async def accept_award(
        self,
        token: str,
        *,
        respondent_name: str,
        idempotency_key: str,
    ) -> AwardDTO:
        claims = self._validate_token(token, purpose="award_response")
        award_id = claims.subject
        payload = {"award_id": award_id, "respondent_name": respondent_name}
        replay = self._raw_idempotent_replay("award.accept", idempotency_key, payload)
        if replay is not None:
            return replay
        current = await self.get_award_status(award_id)
        require(
            claims.metadata.get("tenant_id")
            == self._tenant_for_request(current.procurement_request_id),
            ErrorCode.INVALID_RESPONSE_TOKEN,
            "award token tenant does not match the procurement request",
        )
        require(
            current.status == AwardStatus.DELIVERED,
            ErrorCode.INVALID_STATE,
            "award must be delivered before acceptance",
        )
        updated = current.model_copy(
            update={
                "award_version": current.award_version + 1,
                "status": AwardStatus.ACCEPTED,
                "responded_at": self.clock.now(),
                "updated_at": self.clock.now(),
            }
        )
        self.store.awards[award_id]["dto"] = updated
        self.store.awards[award_id]["accepted_by"] = respondent_name
        self.store.procurement_status[updated.procurement_request_id] = "SUPPLIER_ACCEPTED"
        context = CommandContextDTO(
            tenant_id=self._tenant_for_request(updated.procurement_request_id),
            idempotency_key=idempotency_key,
            correlation_id=f"cor:{updated.procurement_request_id}",
            actor_type=ActorType.HUMAN,
            actor_id=respondent_name,
        )
        self._audit("SUPPLIER_ACCEPTED_AWARD", "award", award_id, context, {})
        self._remember_raw_idempotency("award.accept", idempotency_key, payload, updated)
        return updated

    async def confirm_reservation(
        self,
        award_id: str,
        *,
        event_date: str | date,
        delivery_window: str,
        people_count: int,
        confirmed_by: str,
        idempotency_key: str,
    ) -> AwardDTO:
        payload = {
            "award_id": award_id,
            "event_date": str(event_date),
            "delivery_window": delivery_window,
            "people_count": people_count,
            "confirmed_by": confirmed_by,
        }
        replay = self._raw_idempotent_replay("reservation.confirm", idempotency_key, payload)
        if replay is not None:
            return replay
        current = self._award_record(award_id)["dto"]
        require(
            current.status == AwardStatus.ACCEPTED,
            ErrorCode.INVALID_STATE,
            "reservation requires supplier acceptance",
        )
        require(people_count > 0, ErrorCode.VALIDATION_ERROR, "people_count must be positive")
        require(
            bool(delivery_window.strip()),
            ErrorCode.VALIDATION_ERROR,
            "delivery_window is required",
        )
        require(bool(confirmed_by.strip()), ErrorCode.VALIDATION_ERROR, "confirmed_by is required")
        try:
            parsed_date = (
                date.fromisoformat(event_date) if isinstance(event_date, str) else event_date
            )
        except ValueError as error:
            raise DomainError(
                ErrorCode.VALIDATION_ERROR,
                "event_date must be an ISO date",
            ) from error
        award_record = self._award_record(award_id)
        require(
            award_record["accepted_by"] == confirmed_by,
            ErrorCode.POLICY_DENIED,
            "reservation must be confirmed by the award respondent",
        )
        quote = self._quote(current.approved_quote.quote_id)
        requirements = self._round(quote.rfq_round_id)["requirements"]
        expected_window = requirements.delivery_time.strftime("%H:%M")
        require(
            parsed_date == requirements.event_date
            and delivery_window == expected_window
            and people_count == requirements.people_count,
            ErrorCode.VALIDATION_ERROR,
            "reservation terms must match the frozen RFQ requirements",
            details={
                "expected_event_date": requirements.event_date.isoformat(),
                "expected_delivery_window": expected_window,
                "expected_people_count": requirements.people_count,
            },
        )
        existing_reservation_id = self.store.reservation_by_award_id.get(award_id)
        if existing_reservation_id is not None:
            existing = self.store.reservations[existing_reservation_id]
            same_terms = (
                existing["event_date"] == parsed_date
                and existing["delivery_window"] == delivery_window
                and existing["people_count"] == people_count
                and existing["confirmed_by"] == confirmed_by
            )
            require(
                same_terms,
                ErrorCode.IDEMPOTENCY_CONFLICT,
                "award already has a reservation with different terms",
            )
            replay_result = current.model_copy(update={"idempotent_replay": True})
            self._remember_raw_idempotency(
                "reservation.confirm", idempotency_key, payload, replay_result
            )
            return replay_result
        reservation_id = self._new_id("reservation")
        self.store.reservations[reservation_id] = {
            "reservation_id": reservation_id,
            "award_id": award_id,
            "supplier_id": current.supplier_id,
            "procurement_request_id": current.procurement_request_id,
            "event_date": parsed_date,
            "delivery_window": delivery_window,
            "people_count": people_count,
            "status": ReservationStatus.CONFIRMED,
            "confirmed_by": confirmed_by,
            "confirmed_at": self.clock.now(),
        }
        self.store.reservation_by_award_id[award_id] = reservation_id
        completed = current.model_copy(
            update={
                "award_version": current.award_version + 1,
                "reservation_status": ReservationStatus.CONFIRMED,
                "ready_for_contracting": True,
                "updated_at": self.clock.now(),
            }
        )
        self.store.awards[award_id]["dto"] = completed
        self.store.procurement_status[current.procurement_request_id] = "READY_FOR_CONTRACTING"
        context = CommandContextDTO(
            tenant_id=self._tenant_for_request(current.procurement_request_id),
            idempotency_key=idempotency_key,
            correlation_id=f"cor:{current.procurement_request_id}",
            actor_type=ActorType.HUMAN,
            actor_id=confirmed_by,
        )
        self._audit("CAPACITY_RESERVED", "reservation", reservation_id, context, {})
        self._audit(
            "PROCUREMENT_READY_FOR_CONTRACTING",
            "procurement_request",
            current.procurement_request_id,
            context,
            {"award_id": award_id, "reservation_id": reservation_id},
        )
        self._remember_raw_idempotency("reservation.confirm", idempotency_key, payload, completed)
        return completed

    def get_procurement_status(self, procurement_request_id: str) -> str:
        try:
            return self.store.procurement_status[procurement_request_id]
        except KeyError as error:
            raise DomainError(ErrorCode.NOT_FOUND, "procurement request not found") from error

    async def _refresh_delivery_batch(self, rfq_round_id: str) -> DeliveryBatchDTO:
        round_record = self._round(rfq_round_id)
        round_dto: RFQRoundDTO = round_record["dto"]
        deliveries: list[DeliveryDTO] = []
        for recipient_id in round_record["recipient_ids"]:
            recipient = self._recipient(recipient_id)
            external_id = recipient["external_id"]
            if external_id:
                provider = await self.delivery_gateway.get_status(external_id)
                recipient["status"] = str(provider.status)
                recipient["delivered_at"] = provider.delivered_at
                recipient["failure_code"] = provider.failure_reason
                if (
                    provider.status == DeliveryState.DELIVERED
                    and not recipient["delivery_event_emitted"]
                ):
                    self._audit(
                        "RFQ_DELIVERY_CONFIRMED",
                        "rfq_recipient",
                        recipient_id,
                        self._system_context(round_dto.procurement_request_id),
                        {"external_id": external_id},
                    )
                    recipient["delivery_event_emitted"] = True
            deliveries.append(
                DeliveryDTO(
                    recipient_id=recipient_id,
                    rfq_round_id=rfq_round_id,
                    supplier_id=recipient["supplier_id"],
                    channel=recipient["channel"],
                    status=recipient["status"],
                    external_id=external_id,
                    delivered_at=recipient["delivered_at"],
                    failure_code=recipient["failure_code"],
                )
            )
        confirmed = sum(delivery.status == DeliveryStatus.DELIVERED for delivery in deliveries)
        activation = confirmed >= round_record["policy"].minimum_confirmed_deliveries
        has_started_delivery = any(delivery.external_id is not None for delivery in deliveries)
        new_status = (
            RFQRoundStatus.ACTIVE
            if activation
            else RFQRoundStatus.DISPATCHING
            if has_started_delivery
            else round_dto.status
        )
        if round_dto.status != new_status:
            previous_status = round_dto.status
            round_dto = round_dto.model_copy(
                update={"round_version": round_dto.round_version + 1, "status": new_status}
            )
            round_record["dto"] = round_dto
            self._audit(
                "RFQ_ROUND_ACTIVATED"
                if new_status == RFQRoundStatus.ACTIVE
                else "RFQ_DISPATCH_STARTED",
                "rfq_round",
                rfq_round_id,
                self._system_context(round_dto.procurement_request_id),
                {"previous_status": previous_status, "new_status": new_status},
            )
        if activation:
            self.store.procurement_status[round_dto.procurement_request_id] = "RFQ_ACTIVE"
        return DeliveryBatchDTO(
            rfq_round_id=rfq_round_id,
            round_version=round_dto.round_version,
            deliveries=deliveries,
            confirmed_count=confirmed,
            all_confirmed=confirmed == len(deliveries),
            activation_criteria_met=activation,
            updated_at=self.clock.now(),
        )

    def _expire_quotes(self, round_record: dict[str, Any]) -> None:
        expired_count = 0
        for quote in self._quotes_for_round(round_record["dto"].rfq_round_id):
            if (
                quote.status in {QuoteStatus.FINAL, QuoteStatus.NEEDS_CLARIFICATION}
                and quote.valid_until <= self.clock.now()
            ):
                updated = quote.model_copy(
                    update={
                        "status": QuoteStatus.EXPIRED,
                        "eligible": False,
                        "validation_errors": list(
                            dict.fromkeys([*quote.validation_errors, "QUOTE_EXPIRED"])
                        ),
                    }
                )
                self.store.quotes[quote.quote_id]["dto"] = updated
                self._audit(
                    "QUOTE_EXPIRED",
                    "quote",
                    quote.quote_id,
                    self._system_context(round_record["dto"].procurement_request_id),
                    {"quote_version": quote.quote_version},
                )
                expired_count += 1
        if expired_count:
            round_record["collection_version"] += expired_count

    def _system_context(self, procurement_request_id: str) -> CommandContextDTO:
        return CommandContextDTO(
            tenant_id=self._tenant_for_request(procurement_request_id),
            idempotency_key=f"system:{procurement_request_id}:{len(self.audit_events)}",
            correlation_id=f"cor:{procurement_request_id}",
            actor_type=ActorType.SYSTEM,
            actor_id="dev4_execution_service",
        )

    def _tenant_for_request(self, procurement_request_id: str) -> str:
        for round_record in self.store.rounds.values():
            if round_record["dto"].procurement_request_id == procurement_request_id:
                return str(round_record["tenant_id"])
        raise DomainError(ErrorCode.NOT_FOUND, "procurement request tenant not found")

    def _round(self, rfq_round_id: str) -> dict[str, Any]:
        try:
            return self.store.rounds[rfq_round_id]
        except KeyError as error:
            raise DomainError(ErrorCode.NOT_FOUND, "RFQ round not found") from error

    def _recipient(self, recipient_id: str) -> dict[str, Any]:
        try:
            return self.store.recipients[recipient_id]
        except KeyError as error:
            raise DomainError(ErrorCode.NOT_FOUND, "RFQ recipient not found") from error

    def _quote(self, quote_id: str) -> QuoteDTO:
        try:
            return self.store.quotes[quote_id]["dto"]
        except KeyError as error:
            raise DomainError(ErrorCode.NOT_FOUND, "quote not found") from error

    def _quotes_for_round(self, rfq_round_id: str) -> list[QuoteDTO]:
        return [
            record["dto"]
            for record in self.store.quotes.values()
            if record["dto"].rfq_round_id == rfq_round_id
        ]

    def _comparison(self, comparison_id: str) -> QuoteComparisonDTO:
        try:
            return self.store.comparisons[comparison_id]["dto"]
        except KeyError as error:
            raise DomainError(ErrorCode.NOT_FOUND, "comparison not found") from error

    def _approval(self, approval_id: str) -> ApprovalDTO:
        try:
            return self.store.approvals[approval_id]["dto"]
        except KeyError as error:
            raise DomainError(ErrorCode.NOT_FOUND, "approval not found") from error

    def _award_record(self, award_id: str) -> dict[str, Any]:
        try:
            return self.store.awards[award_id]
        except KeyError as error:
            raise DomainError(ErrorCode.NOT_FOUND, "award not found") from error

    def _validate_token(self, token: str, *, purpose: str):
        try:
            return self.token_service.validate(token, purpose=purpose, now=self.clock.now())
        except TokenValidationError as error:
            raise DomainError(ErrorCode.INVALID_RESPONSE_TOKEN, str(error)) from error

    def _new_id(self, prefix: str) -> str:
        number = self._id_counters.get(prefix, 0) + 1
        self._id_counters[prefix] = number
        return f"{prefix}_{number:04d}"

    def _audit(
        self,
        event_type: str,
        aggregate_type: str,
        aggregate_id: str,
        context: CommandContextDTO,
        payload: dict[str, Any],
    ) -> None:
        self.store.audit_events.append(
            AuditEventDTO(
                event_id=self._new_id("event"),
                tenant_id=context.tenant_id,
                event_type=event_type,
                aggregate_type=aggregate_type,
                aggregate_id=aggregate_id,
                occurred_at=self.clock.now(),
                correlation_id=context.correlation_id,
                causation_id=context.causation_id,
                actor_type=context.actor_type,
                actor_id=context.actor_id,
                payload=payload,
            )
        )

    def _idempotent_replay(
        self, operation: str, context: CommandContextDTO, payload: Any
    ) -> Any | None:
        return self._raw_idempotent_replay(operation, context.idempotency_key, payload)

    def _raw_idempotent_replay(
        self, operation: str, idempotency_key: str, payload: Any
    ) -> Any | None:
        existing = self.store.idempotency.get((operation, idempotency_key))
        if existing is None:
            return None
        fingerprint, result = existing
        require(
            fingerprint == self._idempotency_payload_hash(payload),
            ErrorCode.IDEMPOTENCY_CONFLICT,
            "idempotency key is already bound to another payload",
        )
        if hasattr(result, "model_copy"):
            return result.model_copy(update={"idempotent_replay": True})
        return deepcopy(result)

    def _remember_idempotency(
        self,
        operation: str,
        context: CommandContextDTO,
        payload: Any,
        result: Any,
    ) -> None:
        self._remember_raw_idempotency(operation, context.idempotency_key, payload, result)

    def _remember_raw_idempotency(
        self,
        operation: str,
        idempotency_key: str,
        payload: Any,
        result: Any,
    ) -> None:
        self.store.idempotency[(operation, idempotency_key)] = (
            self._idempotency_payload_hash(payload),
            result.model_copy(deep=True) if hasattr(result, "model_copy") else deepcopy(result),
        )

    @staticmethod
    def _idempotency_payload_hash(payload: Any) -> str:
        normalized = (
            payload.model_dump(mode="python")
            if hasattr(payload, "model_dump")
            else deepcopy(payload)
        )
        if isinstance(normalized, dict):
            normalized.pop("sourcing_run_id", None)
            context = normalized.get("context")
            if isinstance(context, dict):
                for tracing_field in (
                    "correlation_id",
                    "causation_id",
                    "agent_run_id",
                ):
                    context.pop(tracing_field, None)
        return payload_hash(normalized)
