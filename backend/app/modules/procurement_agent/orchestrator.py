from __future__ import annotations

import asyncio
import hashlib
import re
import unicodedata
from datetime import datetime
from zoneinfo import ZoneInfo

from app.modules.buyer_timeline.audit import ActorType, AuditEvent, AuditPort
from app.modules.procurement_agent.adapters import InMemoryAgentRunRepository
from app.modules.procurement_agent.models import (
    AgentRun,
    AgentRunStatus,
    AgentStopReason,
    AuthorizationRequest,
    CommandContext,
    CreateRFQRoundCommand,
    RFQRoundDTO,
    ToolExecutionContext,
)
from app.modules.procurement_agent.ports import Clock, IdGenerator, PolicyPort, RFQExecutionPort
from app.modules.procurement_agent.sourcing_tools import (
    EvaluateSupplierOutput,
    SearchSuppliersOutput,
    SelectRecipientsOutput,
    register_sourcing_tools,
)
from app.modules.procurement_agent.tool_registry import (
    ToolExecutionError,
    ToolRegistry,
)
from app.modules.procurement_agent.workflow import (
    EligibilityResultView,
    InMemoryProcurementProcessRepository,
    ProcurementProcess,
    ProcurementWorkflowView,
)
from app.modules.procurement_requests.ports import ProcurementInterpretationPort
from app.modules.procurement_requests.schemas import (
    MAX_BUYER_MESSAGE_CHARS,
    ProcurementRequestDraft,
    ProcurementRequestPatch,
    ProcurementRequestReady,
    ProcurementRequestStatus,
)
from app.modules.procurement_requests.service import ProcurementRequestService
from app.modules.sourcing.eligibility import SupplierEligibilityEngine
from app.modules.sourcing.models import SupplierSearchCriteria
from app.modules.sourcing.ports import SupplierDirectoryPort


class ProcurementNotFoundError(LookupError):
    pass


class InvalidWorkflowStateError(RuntimeError):
    pass


class RequestIdempotencyConflictError(RuntimeError):
    pass


def _explicitly_confirms_candidate(message: str, candidate_value: object) -> bool:
    """Bind a positive confirmation to this value and reject negated confirmations."""

    if isinstance(candidate_value, bool) or candidate_value is None:
        return False
    normalized_message = _normalize_confirmation_text(message)
    for clause in re.split(r"[.;\n]+", normalized_message):
        has_positive_marker = re.search(
            r"\b(?:confirmo|confirmamos|considere|valor correto)\b",
            clause,
        )
        if not has_positive_marker:
            continue
        # Natural-language confirmation is fail-closed: any negation in the same
        # clause makes it ambiguous, regardless of whether it precedes the marker.
        if re.search(r"\b(?:nao|nunca|jamais)\b", clause):
            continue
        if isinstance(candidate_value, int):
            if re.search(rf"(?<!\d){candidate_value}(?!\d)", clause):
                return True
            continue
        candidate_text = _normalize_confirmation_text(str(candidate_value))
        if candidate_text and candidate_text in clause:
            return True
    return False


def _normalize_confirmation_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value).casefold()
    normalized = "".join(
        character for character in normalized if not unicodedata.combining(character)
    )
    return re.sub(r"\s+", " ", normalized).strip()


class ProcurementAgentOrchestrator:
    """Bounded workflow: the model interprets facts; code owns every transition."""

    def __init__(
        self,
        *,
        requests: InMemoryProcurementProcessRepository,
        request_service: ProcurementRequestService,
        interpreter: ProcurementInterpretationPort,
        directory: SupplierDirectoryPort,
        eligibility: SupplierEligibilityEngine,
        policy: PolicyPort,
        rfq: RFQExecutionPort,
        audit: AuditPort,
        runs: InMemoryAgentRunRepository,
        clock: Clock,
        ids: IdGenerator,
        max_steps: int = 16,
        mode: str = "demo_fake",
    ) -> None:
        self._requests = requests
        self._request_service = request_service
        self._interpreter = interpreter
        self._directory = directory
        self._policy = policy
        self._rfq = rfq
        self._audit = audit
        self._runs = runs
        self._clock = clock
        self._ids = ids
        self._max_steps = max_steps
        self._mode = mode
        # The in-memory prototype uses one mutation lock as its unit of work.
        # A database adapter must replace this with optimistic locking/transactions.
        self._mutation_lock = asyncio.Lock()
        self._request_creation_keys: dict[tuple[str, str], tuple[str, str]] = {}
        self._registry = ToolRegistry(policy=policy, audit=audit, clock=clock, ids=ids)
        register_sourcing_tools(
            self._registry,
            directory=directory,
            eligibility=eligibility,
            rfq=rfq,
        )

    async def receive_message(
        self,
        message: str,
        *,
        request_id: str | None = None,
        tenant_id: str = "org_demo",
        buyer_user_id: str = "buyer_gabriel",
        idempotency_key: str | None = None,
    ) -> ProcurementWorkflowView:
        async with self._mutation_lock:
            return await self._receive_message_unlocked(
                message,
                request_id=request_id,
                tenant_id=tenant_id,
                buyer_user_id=buyer_user_id,
                idempotency_key=idempotency_key,
            )

    async def _receive_message_unlocked(
        self,
        message: str,
        *,
        request_id: str | None = None,
        tenant_id: str = "org_demo",
        buyer_user_id: str = "buyer_gabriel",
        idempotency_key: str | None = None,
    ) -> ProcurementWorkflowView:
        normalized_message = message.strip()
        if not normalized_message:
            raise ValueError("message cannot be empty")
        if len(normalized_message) > MAX_BUYER_MESSAGE_CHARS:
            raise ValueError(f"message cannot exceed {MAX_BUYER_MESSAGE_CHARS} characters")

        creation_key: tuple[str, str] | None = None
        creation_hash: str | None = None
        if request_id is None and idempotency_key:
            creation_key = (tenant_id, idempotency_key)
            creation_hash = hashlib.sha256(message.strip().encode()).hexdigest()
            existing_creation = self._request_creation_keys.get(creation_key)
            if existing_creation is not None:
                existing_hash, existing_request_id = existing_creation
                if existing_hash != creation_hash:
                    raise RequestIdempotencyConflictError(
                        "Idempotency key was already used with a different message"
                    )
                existing_process = await self._get_process(existing_request_id)
                if (
                    existing_process.stop_reason is not AgentStopReason.ACTION_BLOCKED
                    or not isinstance(existing_process.request, ProcurementRequestDraft)
                ):
                    return await self._view(existing_process)
                request_id = existing_request_id

        process = await self._requests.get(request_id) if request_id else None
        is_new = process is None
        if request_id and process is None:
            raise ProcurementNotFoundError(request_id)
        if process is None:
            request_id = self._ids.new("pr")
            draft = ProcurementRequestDraft(request_id=request_id)
            process = ProcurementProcess(
                request_id=request_id,
                tenant_id=tenant_id,
                buyer_user_id=buyer_user_id,
                request=draft,
                mode=self._mode,
            )
            if creation_key is not None and creation_hash is not None:
                # Reserve the aggregate while holding the unit-of-work lock. A provider
                # retry with the same key resumes this request instead of creating an orphan.
                await self._requests.save(process)
                self._request_creation_keys[creation_key] = (
                    creation_hash,
                    process.request_id,
                )

        if not isinstance(process.request, ProcurementRequestDraft):
            raise InvalidWorkflowStateError(
                "The plan already exists; edit support is outside this initial prototype."
            )

        run = await self._start_run(process)
        events: list[AuditEvent] = []
        if is_new:
            events.append(
                self._event(
                    process,
                    run,
                    "PROCUREMENT_REQUEST_CREATED",
                    actor_type=ActorType.HUMAN,
                    actor_id=buyer_user_id,
                    previous_state=None,
                    new_state=ProcurementRequestStatus.DRAFT,
                    payload={"mode": process.mode},
                )
            )
        events.append(
            self._event(
                process,
                run,
                "PROCUREMENT_MESSAGE_RECEIVED",
                actor_type=ActorType.HUMAN,
                actor_id=buyer_user_id,
                payload={"message_length": len(message)},
            )
        )
        await self._audit.append(events)

        try:
            interpretation = await self._interpreter.interpret(
                message,
                current_request=process.request,
                policy=self._request_service.default_policy,
            )
        except Exception:
            process.stop_reason = AgentStopReason.ACTION_BLOCKED
            await self._finish_run(
                run,
                process,
                AgentStopReason.ACTION_BLOCKED,
                "Request interpretation failed; the aggregate is safe to retry.",
                status=AgentRunStatus.BLOCKED,
            )
            process.last_agent_run_id = run.run_id
            await self._requests.save(process)
            raise
        run.step_count += 1
        prior_conflicts = {conflict.field: conflict for conflict in process.conflicts}
        confirmed_values: dict[str, object] = {}
        remaining_conflicts = []
        for conflict in interpretation.conflicts:
            prior = prior_conflicts.get(conflict.field)
            if (
                _explicitly_confirms_candidate(message, conflict.candidate_value)
                and prior is not None
                and prior.candidate_value == conflict.candidate_value
            ):
                confirmed_values[conflict.field] = conflict.candidate_value
            else:
                remaining_conflicts.append(conflict)

        accepted_fields = set(interpretation.extracted_fields.model_fields_set)
        accepted_fields.update(confirmed_values)
        process.evidence.update(
            {
                field: evidence
                for field, evidence in interpretation.evidence.items()
                if field in accepted_fields
            }
        )
        process.confidence_by_field.update(
            {
                field: confidence
                for field, confidence in interpretation.confidence_by_field.items()
                if field in accepted_fields
            }
        )
        process.assumptions = interpretation.assumptions
        process.ambiguities = interpretation.ambiguities
        process.conflicts = remaining_conflicts
        process.request = self._request_service.apply_interpretation(
            process.request,
            interpretation,
        )
        if confirmed_values:
            confirmed_patch = ProcurementRequestPatch.model_validate(confirmed_values)
            process.request = self._request_service.apply_patch(
                process.request,
                confirmed_patch,
                allow_overwrite_fields=confirmed_values,
            )
        assessment = self._request_service.assess(process.request)
        process.missing_fields = assessment.missing_required_fields
        await self._audit.append(
            [
                self._event(
                    process,
                    run,
                    "PROCUREMENT_INTERPRETED",
                    payload={
                        "fields": sorted(interpretation.extracted_fields.model_fields_set),
                        "conflict_count": len(remaining_conflicts),
                        "confirmed_fields": sorted(confirmed_values),
                        "provider": (
                            interpretation.provider_metadata.model_dump(mode="json")
                            if interpretation.provider_metadata is not None
                            else {"provider": "local"}
                        ),
                    },
                )
            ]
        )
        if confirmed_values:
            await self._audit.append(
                [
                    self._event(
                        process,
                        run,
                        "PROCUREMENT_FIELD_CONFIRMED",
                        actor_type=ActorType.HUMAN,
                        actor_id=buyer_user_id,
                        payload={"fields": sorted(confirmed_values)},
                    )
                ]
            )

        if remaining_conflicts:
            conflict = remaining_conflicts[0]
            process.status = ProcurementRequestStatus.NEEDS_CLARIFICATION
            process.request = process.request.model_copy(
                update={"status": ProcurementRequestStatus.NEEDS_CLARIFICATION}
            )
            process.clarification_question = (
                f"O valor de {conflict.field} mudou de {conflict.current_value} para "
                f"{conflict.candidate_value}. Qual valor devo considerar?"
            )
            process.stop_reason = AgentStopReason.NEEDS_CLARIFICATION
            await self._pause_with_clarification(process, run, "CONFLICTING_CONFIRMED_VALUE")
        elif not assessment.can_start_sourcing:
            process.status = ProcurementRequestStatus.NEEDS_CLARIFICATION
            process.request = process.request.model_copy(
                update={"status": ProcurementRequestStatus.NEEDS_CLARIFICATION}
            )
            clarification = assessment.clarifications[0] if assessment.clarifications else None
            process.clarification_question = (
                clarification.question
                if clarification
                else "Preciso confirmar uma informação bloqueante antes de continuar."
            )
            process.stop_reason = AgentStopReason.NEEDS_CLARIFICATION
            await self._pause_with_clarification(
                process,
                run,
                clarification.reason_code if clarification else "BLOCKING_ISSUE",
            )
        else:
            assert assessment.ready_request is not None
            previous = process.status
            process.request = assessment.ready_request
            process.status = ProcurementRequestStatus.READY
            process.plan = self._request_service.default_plan(assessment.ready_request)
            process.missing_fields = []
            process.clarification_question = None
            process.stop_reason = AgentStopReason.AWAITING_PLAN_CONFIRMATION
            await self._audit.append(
                [
                    self._event(
                        process,
                        run,
                        "PROCUREMENT_READY",
                        previous_state=previous,
                        new_state=ProcurementRequestStatus.READY,
                        payload={"request_version": process.request.version},
                    ),
                    self._event(
                        process,
                        run,
                        "PROCUREMENT_PLAN_CREATED",
                        payload={"plan_version": process.plan.version},
                    ),
                ]
            )
            await self._finish_run(
                run,
                process,
                AgentStopReason.AWAITING_PLAN_CONFIRMATION,
                "Briefing ready; buyer must confirm the plan.",
            )

        process.last_agent_run_id = run.run_id
        await self._requests.save(process)
        return await self._view(process)

    async def confirm_plan(self, request_id: str) -> ProcurementWorkflowView:
        async with self._mutation_lock:
            return await self._confirm_plan_unlocked(request_id)

    async def _confirm_plan_unlocked(self, request_id: str) -> ProcurementWorkflowView:
        process = await self._get_process(request_id)
        if process.rfq_round is not None:
            return await self._view(process)
        if process.status is not ProcurementRequestStatus.READY:
            raise InvalidWorkflowStateError(
                f"Cannot start sourcing while request is {process.status}"
            )
        if not isinstance(process.request, ProcurementRequestReady) or process.plan is None:
            raise InvalidWorkflowStateError("Ready request or procurement plan is missing")

        run = await self._start_run(process)
        authorization = await self._policy.authorize(
            AuthorizationRequest(
                actor_type="human",
                actor_id=process.buyer_user_id,
                action="start_sourcing",
                aggregate_type="procurement_request",
                aggregate_id=process.request_id,
                current_state=process.status,
                arguments={"plan_version": process.plan.version},
                procurement_policy=process.plan.policy_snapshot.model_dump(mode="json"),
            )
        )
        if not authorization.allowed:
            process.stop_reason = AgentStopReason.ACTION_BLOCKED
            await self._audit.append(
                [
                    self._event(
                        process,
                        run,
                        "AGENT_ACTION_BLOCKED",
                        payload={"action": "start_sourcing", "reason": authorization.reason_code},
                    )
                ]
            )
            await self._finish_run(
                run,
                process,
                AgentStopReason.ACTION_BLOCKED,
                authorization.reason,
                status=AgentRunStatus.BLOCKED,
            )
            await self._requests.save(process)
            return await self._view(process)

        previous = process.status
        process.status = ProcurementRequestStatus.SOURCING
        process.plan_confirmed_at = self._clock.now()
        await self._audit.append(
            [
                self._event(
                    process,
                    run,
                    "SOURCING_STARTED",
                    actor_type=ActorType.HUMAN,
                    actor_id=process.buyer_user_id,
                    previous_state=previous,
                    new_state=ProcurementRequestStatus.SOURCING,
                    payload={"plan_version": process.plan.version},
                )
            ]
        )

        try:
            await self._run_sourcing(process, run)
        except ToolExecutionError as exc:
            failure_previous = process.status
            if process.rfq_round is None:
                process.status = ProcurementRequestStatus.READY
                process.plan_confirmed_at = None
            process.stop_reason = AgentStopReason.ACTION_BLOCKED
            await self._audit.append(
                [
                    self._event(
                        process,
                        run,
                        "SOURCING_FAILED",
                        previous_state=failure_previous,
                        new_state=process.status,
                        payload={"error_type": type(exc).__name__},
                    )
                ]
            )
            await self._finish_run(
                run,
                process,
                AgentStopReason.ACTION_BLOCKED,
                str(exc),
                status=AgentRunStatus.BLOCKED,
            )

        process.last_agent_run_id = run.run_id
        await self._requests.save(process)
        return await self._view(process)

    async def get(self, request_id: str) -> ProcurementWorkflowView:
        async with self._mutation_lock:
            return await self._view(await self._get_process(request_id))

    async def _run_sourcing(self, process: ProcurementProcess, run: AgentRun) -> None:
        assert isinstance(process.request, ProcurementRequestReady)
        assert process.plan is not None
        criteria = self._search_criteria(process)
        context = self._tool_context(process, run)

        searched = await self._execute_step(
            run,
            process,
            "search_suppliers",
            {"criteria": criteria.model_dump(mode="json")},
            context,
        )
        if searched is None:
            return
        search_output = SearchSuppliersOutput.model_validate(searched)
        process.supplier_candidates = search_output.candidates

        results = []
        for candidate in search_output.candidates:
            evaluated = await self._execute_step(
                run,
                process,
                "evaluate_supplier_eligibility",
                {
                    "candidate": candidate.model_dump(mode="json"),
                    "criteria": criteria.model_dump(mode="json"),
                    "as_of": self._clock.now().isoformat(),
                },
                context,
            )
            if evaluated is None:
                return
            results.append(EvaluateSupplierOutput.model_validate(evaluated).result)
        process.eligibility_results = results

        selected = await self._execute_step(
            run,
            process,
            "select_rfq_recipients",
            {
                "results": [result.model_dump(mode="json") for result in results],
                "candidate_supplier_ids": [
                    candidate.supplier_id for candidate in search_output.candidates
                ],
                "limit": process.plan.target_supplier_count,
            },
            context,
        )
        if selected is None:
            return
        process.selected_supplier_ids = SelectRecipientsOutput.model_validate(selected).supplier_ids

        if not process.selected_supplier_ids:
            previous = process.status
            process.status = ProcurementRequestStatus.NO_ELIGIBLE_SUPPLIERS
            process.stop_reason = AgentStopReason.NO_ELIGIBLE_SUPPLIERS
            await self._audit.append(
                [
                    self._event(
                        process,
                        run,
                        "PROCUREMENT_NO_ELIGIBLE_SUPPLIERS",
                        previous_state=previous,
                        new_state=ProcurementRequestStatus.NO_ELIGIBLE_SUPPLIERS,
                        payload={"stop_reason": AgentStopReason.NO_ELIGIBLE_SUPPLIERS},
                    )
                ]
            )
            await self._finish_run(
                run,
                process,
                AgentStopReason.NO_ELIGIBLE_SUPPLIERS,
                "No profile-eligible suppliers were found.",
            )
            return

        idempotency_key = (
            f"rfq:create:{process.request_id}:v{process.request.version}:"
            f"plan:{process.plan.version}"
        )
        command = CreateRFQRoundCommand(
            procurement_request_id=process.request_id,
            request_version=process.request.version,
            plan_version=process.plan.version,
            sourcing_run_id=run.run_id,
            recipient_supplier_ids=sorted(process.selected_supplier_ids),
            response_deadline=process.plan.response_deadline,
            requirements_snapshot=process.request.model_dump(mode="json"),
            policy_snapshot=process.plan.policy_snapshot.model_dump(mode="json"),
            context=CommandContext(
                tenant_id=process.tenant_id,
                actor_type="agent",
                actor_id="procurement_agent",
                correlation_id=run.correlation_id,
                agent_run_id=run.run_id,
                idempotency_key=idempotency_key,
            ),
        )
        created = await self._execute_step(
            run,
            process,
            "create_rfq_round",
            command.model_dump(mode="json"),
            context,
        )
        if created is None:
            return
        process.rfq_round = RFQRoundDTO.model_validate(created)
        process.stop_reason = AgentStopReason.AWAITING_EXTERNAL_RESPONSE
        await self._finish_run(
            run,
            process,
            AgentStopReason.AWAITING_EXTERNAL_RESPONSE,
            "RFQ draft created; delivery still depends on Dev 4 acknowledgement.",
        )

    async def _execute_step(
        self,
        run: AgentRun,
        process: ProcurementProcess,
        tool_name: str,
        arguments: dict[str, object],
        context: ToolExecutionContext,
    ) -> dict[str, object] | None:
        if run.step_count >= run.max_steps:
            previous = process.status
            process.status = ProcurementRequestStatus.READY
            process.plan_confirmed_at = None
            process.stop_reason = AgentStopReason.MAX_STEPS_REACHED
            await self._audit.append(
                [
                    self._event(
                        process,
                        run,
                        "AGENT_MAX_STEPS_REACHED",
                        previous_state=previous,
                        new_state=ProcurementRequestStatus.READY,
                        payload={"max_steps": run.max_steps},
                    )
                ]
            )
            await self._finish_run(
                run,
                process,
                AgentStopReason.MAX_STEPS_REACHED,
                "Maximum number of typed tool steps reached.",
                status=AgentRunStatus.BLOCKED,
            )
            return None
        result = await self._registry.execute(tool_name, arguments, context)
        run.step_count += 1
        return result.output

    def _search_criteria(self, process: ProcurementProcess) -> SupplierSearchCriteria:
        request = process.request
        assert isinstance(request, ProcurementRequestReady)
        assert process.plan is not None
        city = request.location_city or process.plan.policy_snapshot.default_location_city
        if not city:
            raise InvalidWorkflowStateError(
                "location_city is required by SupplierDirectoryPort or procurement policy"
            )
        local_tz = ZoneInfo("America/Sao_Paulo")
        available_hours = None
        if request.delivery_time is not None:
            event_at = datetime.combine(
                request.event_date,
                request.delivery_time,
                tzinfo=local_tz,
            )
            available_hours = max(
                0,
                int((event_at - self._clock.now().astimezone(local_tz)).total_seconds() // 3600),
            )
        return SupplierSearchCriteria(
            tenant_id=process.tenant_id,
            category=request.category,
            city=city,
            district=request.location_district,
            event_date=request.event_date,
            delivery_time=request.delivery_time,
            people_count=request.people_count,
            invoice_required=request.invoice_required is True,
            dietary_requirements={
                "vegetarian": request.vegetarian_count,
                "vegan": request.vegan_count,
                "gluten_free": request.gluten_free_count,
            },
            mandatory_tags=(["no_single_use_plastic"] if request.no_single_use_plastic else []),
            maximum_lead_time_hours=available_hours,
        )

    async def _start_run(self, process: ProcurementProcess) -> AgentRun:
        run = AgentRun(
            run_id=self._ids.new("run"),
            procurement_request_id=process.request_id,
            correlation_id=self._ids.new("cor"),
            started_at=self._clock.now(),
            max_steps=self._max_steps,
        )
        await self._runs.save(run)
        await self._audit.append(
            [self._event(process, run, "AGENT_RUN_STARTED", payload={"max_steps": run.max_steps})]
        )
        return run

    async def _pause_with_clarification(
        self, process: ProcurementProcess, run: AgentRun, reason_code: str
    ) -> None:
        await self._audit.append(
            [
                self._event(
                    process,
                    run,
                    "PROCUREMENT_CLARIFICATION_REQUESTED",
                    new_state=ProcurementRequestStatus.NEEDS_CLARIFICATION,
                    payload={"reason_code": reason_code},
                )
            ]
        )
        await self._finish_run(
            run,
            process,
            AgentStopReason.NEEDS_CLARIFICATION,
            "A blocking fact needs buyer clarification.",
        )

    async def _finish_run(
        self,
        run: AgentRun,
        process: ProcurementProcess,
        reason: AgentStopReason,
        summary: str,
        *,
        status: AgentRunStatus = AgentRunStatus.PAUSED,
    ) -> None:
        if run.status is not AgentRunStatus.RUNNING:
            return
        run.status = status
        run.stop_reason = reason
        run.decision_summary = summary
        run.finished_at = self._clock.now()
        await self._runs.save(run)
        await self._audit.append(
            [
                self._event(
                    process,
                    run,
                    "AGENT_RUN_PAUSED"
                    if status is AgentRunStatus.PAUSED
                    else "AGENT_RUN_COMPLETED",
                    payload={"stop_reason": reason, "step_count": run.step_count},
                )
            ]
        )

    def _tool_context(self, process: ProcurementProcess, run: AgentRun) -> ToolExecutionContext:
        assert process.plan is not None
        return ToolExecutionContext(
            aggregate_id=process.request_id,
            aggregate_state=process.status,
            actor_id="procurement_agent",
            correlation_id=run.correlation_id,
            agent_run_id=run.run_id,
            procurement_policy=process.plan.policy_snapshot.model_dump(mode="json"),
        )

    async def _get_process(self, request_id: str) -> ProcurementProcess:
        process = await self._requests.get(request_id)
        if process is None:
            raise ProcurementNotFoundError(request_id)
        return process

    async def _view(self, process: ProcurementProcess) -> ProcurementWorkflowView:
        names = {
            candidate.supplier_id: candidate.display_name
            for candidate in process.supplier_candidates
        }
        results = [
            EligibilityResultView(
                supplier_id=result.supplier_id,
                display_name=names.get(result.supplier_id, result.supplier_id),
                decision=result.decision,
                checks=[check.model_dump(mode="json") for check in result.checks],
                evidence_refs=result.evidence_refs,
            )
            for result in process.eligibility_results
        ]
        return ProcurementWorkflowView(
            request_id=process.request_id,
            status=process.status,
            stop_reason=process.stop_reason,
            draft=process.request.model_dump(mode="json", exclude={"status"}),
            evidence=process.evidence,
            missing_fields=process.missing_fields,
            clarification_question=process.clarification_question,
            plan=process.plan,
            eligibility_results=results,
            selected_supplier_ids=process.selected_supplier_ids,
            rfq_round_id=(process.rfq_round.rfq_round_id if process.rfq_round else None),
            timeline=await self._audit.list_for_aggregate(process.request_id),
            mode=process.mode,
        )

    def _event(
        self,
        process: ProcurementProcess,
        run: AgentRun,
        event_type: str,
        *,
        actor_type: ActorType = ActorType.AGENT,
        actor_id: str | None = "procurement_agent",
        previous_state: ProcurementRequestStatus | None = None,
        new_state: ProcurementRequestStatus | None = None,
        payload: dict[str, object] | None = None,
    ) -> AuditEvent:
        return AuditEvent(
            event_id=self._ids.new("evt"),
            event_type=event_type,
            aggregate_type="procurement_request",
            aggregate_id=process.request_id,
            actor_type=actor_type,
            actor_id=actor_id,
            occurred_at=self._clock.now(),
            previous_state=previous_state,
            new_state=new_state,
            correlation_id=run.correlation_id,
            agent_run_id=run.run_id,
            payload=payload or {},
        )


__all__ = [
    "InvalidWorkflowStateError",
    "ProcurementAgentOrchestrator",
    "ProcurementNotFoundError",
    "RequestIdempotencyConflictError",
]
