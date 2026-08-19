"""Deterministic request readiness, clarification and planning rules."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from .ports import Clock
from .schemas import (
    Clarification,
    FieldConflict,
    ProcurementInterpretationResult,
    ProcurementPlan,
    ProcurementPlanPatch,
    ProcurementPolicySnapshot,
    ProcurementRequestDraft,
    ProcurementRequestPatch,
    ProcurementRequestReady,
    ProcurementRequestStatus,
    RequestAssessment,
    RequestLike,
)

_REQUEST_FIELDS = frozenset(ProcurementRequestPatch.model_fields)


class ProcurementRequestService:
    """Pure business rules for the request intake stage.

    A clock can be injected for repeatable deadline checks.  No repository,
    model provider or network access is used here.
    """

    def __init__(
        self,
        *,
        clock: Clock | None = None,
        timezone: str = "America/Sao_Paulo",
        default_policy: ProcurementPolicySnapshot | None = None,
    ) -> None:
        self._clock = clock
        self._timezone = ZoneInfo(timezone)
        self._default_policy = default_policy or ProcurementPolicySnapshot()

    @property
    def default_policy(self) -> ProcurementPolicySnapshot:
        return self._default_policy

    def missing_required_fields(
        self,
        request: RequestLike | ProcurementRequestPatch,
        policy: ProcurementPolicySnapshot | None = None,
    ) -> list[str]:
        """Return blocking absent fields in a stable, user-facing order."""

        policy = policy or self._default_policy
        missing: list[str] = []

        if request.category is None:
            missing.append("category")
        if request.description is None:
            missing.append("description")
        if request.event_date is None:
            missing.append("event_date")
        if policy.delivery_time_is_required and request.delivery_time is None:
            missing.append("delivery_time")
        if not request.full_address and not request.location_district:
            # A district is the minimum location granularity accepted for an
            # initial RFQ. A full address satisfies the same requirement.
            missing.append("location_district")
        if request.people_count is None:
            missing.append("people_count")
        if policy.budget_is_eliminatory and request.maximum_total_cents is None:
            missing.append("maximum_total_cents")
        if policy.invoice_answer_is_required and request.invoice_required is None:
            missing.append("invoice_required")
        if policy.plastic_answer_is_required and request.no_single_use_plastic is None:
            missing.append("no_single_use_plastic")
        if request.response_deadline is None:
            missing.append("response_deadline")
        if request.approver_user_id is None:
            missing.append("approver_user_id")
        return missing

    # Stable alias matching the internal tool name from the product plan.
    get_missing_required_fields = missing_required_fields

    def blocking_issues(
        self,
        request: RequestLike | ProcurementRequestPatch,
        policy: ProcurementPolicySnapshot | None = None,
    ) -> list[str]:
        """Return present-but-invalid facts which require buyer clarification."""

        del policy  # Reserved for additional policy checks without changing API.
        issues: list[str] = []

        if request.people_count is not None:
            dietary_total = sum(
                value or 0
                for value in (
                    request.vegetarian_count,
                    request.vegan_count,
                    request.gluten_free_count,
                )
            )
            if dietary_total > request.people_count:
                issues.append("DIETARY_COUNTS_EXCEED_PEOPLE_COUNT")

        now = self._now()
        if now is not None:
            local_now = now.astimezone(self._timezone)
            if request.event_date is not None and request.event_date < local_now.date():
                issues.append("EVENT_DATE_IN_PAST")
            if request.response_deadline is not None and request.response_deadline <= now:
                issues.append("RESPONSE_DEADLINE_NOT_FUTURE")

        if (
            request.event_date is not None
            and request.delivery_time is not None
            and request.response_deadline is not None
        ):
            event_at = datetime.combine(
                request.event_date,
                request.delivery_time,
                tzinfo=self._timezone,
            )
            if request.response_deadline >= event_at:
                issues.append("RESPONSE_DEADLINE_NOT_BEFORE_EVENT")

        return issues

    def clarifications_for(
        self,
        request: RequestLike | ProcurementRequestPatch,
        policy: ProcurementPolicySnapshot | None = None,
    ) -> list[Clarification]:
        """Build at most one objective question for each logical group."""

        missing = set(self.missing_required_fields(request, policy))
        issues = set(self.blocking_issues(request, policy))
        clarifications: list[Clarification] = []

        schedule_fields = [field for field in ("event_date", "delivery_time") if field in missing]
        if schedule_fields:
            if len(schedule_fields) == 2:
                question = "Qual é a data do evento e o horário desejado para a entrega?"
            elif schedule_fields[0] == "event_date":
                question = "Qual é a data do evento?"
            else:
                question = "Qual é o horário desejado para a entrega?"
            clarifications.append(
                Clarification(
                    fields=schedule_fields,
                    question=question,
                    reason_code="MISSING_EVENT_SCHEDULE",
                )
            )

        if "location_district" in missing:
            clarifications.append(
                Clarification(
                    fields=["location_district", "full_address"],
                    question=(
                        "Qual é o bairro ou o endereço completo do evento? "
                        "Preciso dele para validar cobertura e taxa de entrega."
                    ),
                    reason_code="MISSING_DELIVERY_LOCATION",
                )
            )

        scope_fields = [
            field for field in ("category", "description", "people_count") if field in missing
        ]
        if scope_fields:
            prompts = {
                "category": "o tipo de alimentação",
                "description": "uma breve descrição do que precisa",
                "people_count": "a quantidade de pessoas",
            }
            requested = ", ".join(prompts[field] for field in scope_fields)
            clarifications.append(
                Clarification(
                    fields=scope_fields,
                    question=f"Pode informar {requested}?",
                    reason_code="MISSING_REQUEST_SCOPE",
                )
            )

        commercial_fields = [
            field
            for field in (
                "maximum_total_cents",
                "response_deadline",
                "approver_user_id",
            )
            if field in missing
        ]
        if commercial_fields:
            prompts = {
                "maximum_total_cents": "o orçamento máximo",
                "response_deadline": "o prazo, com data e hora, para receber propostas",
                "approver_user_id": "quem será o aprovador",
            }
            requested = ", ".join(prompts[field] for field in commercial_fields)
            clarifications.append(
                Clarification(
                    fields=commercial_fields,
                    question=f"Para concluir o plano, preciso de {requested}.",
                    reason_code="MISSING_COMMERCIAL_RULES",
                )
            )

        requirement_fields = [
            field for field in ("invoice_required", "no_single_use_plastic") if field in missing
        ]
        if requirement_fields:
            questions: list[str] = []
            if "invoice_required" in requirement_fields:
                questions.append("a emissão de nota fiscal é obrigatória")
            if "no_single_use_plastic" in requirement_fields:
                questions.append("devemos proibir plásticos descartáveis de uso único")
            clarifications.append(
                Clarification(
                    fields=requirement_fields,
                    question="Confirme se " + " e se ".join(questions) + ".",
                    reason_code="MISSING_MANDATORY_REQUIREMENTS",
                )
            )

        if "DIETARY_COUNTS_EXCEED_PEOPLE_COUNT" in issues:
            clarifications.append(
                Clarification(
                    fields=[
                        "people_count",
                        "vegetarian_count",
                        "vegan_count",
                        "gluten_free_count",
                    ],
                    question=(
                        "As quantidades de dietas somam mais pessoas que o total do "
                        "evento. Pode confirmar as contagens?"
                    ),
                    reason_code="DIETARY_COUNTS_EXCEED_PEOPLE_COUNT",
                )
            )
        if "EVENT_DATE_IN_PAST" in issues:
            clarifications.append(
                Clarification(
                    fields=["event_date"],
                    question="A data informada já passou. Qual é a data correta do evento?",
                    reason_code="EVENT_DATE_IN_PAST",
                )
            )
        if "RESPONSE_DEADLINE_NOT_FUTURE" in issues:
            clarifications.append(
                Clarification(
                    fields=["response_deadline"],
                    question="O prazo de resposta já passou. Qual é o novo prazo?",
                    reason_code="RESPONSE_DEADLINE_NOT_FUTURE",
                )
            )
        if "RESPONSE_DEADLINE_NOT_BEFORE_EVENT" in issues:
            clarifications.append(
                Clarification(
                    fields=["response_deadline", "event_date", "delivery_time"],
                    question=(
                        "O prazo de resposta precisa ser anterior ao evento. "
                        "Qual é o prazo correto?"
                    ),
                    reason_code="RESPONSE_DEADLINE_NOT_BEFORE_EVENT",
                )
            )
        return clarifications

    def clarification_for(
        self,
        request: RequestLike | ProcurementRequestPatch,
        policy: ProcurementPolicySnapshot | None = None,
    ) -> Clarification | None:
        """Return the next question without repeating already answered fields."""

        clarifications = self.clarifications_for(request, policy)
        return clarifications[0] if clarifications else None

    def assess(
        self,
        request: ProcurementRequestDraft,
        policy: ProcurementPolicySnapshot | None = None,
    ) -> RequestAssessment:
        policy = policy or self._default_policy
        missing = self.missing_required_fields(request, policy)
        issues = self.blocking_issues(request, policy)

        if missing or issues:
            return RequestAssessment(
                status=ProcurementRequestStatus.NEEDS_CLARIFICATION,
                missing_required_fields=missing,
                blocking_issues=issues,
                clarifications=self.clarifications_for(request, policy),
                can_start_sourcing=False,
            )

        ready = self.to_ready(request, policy)
        return RequestAssessment(
            status=ProcurementRequestStatus.READY,
            can_start_sourcing=True,
            ready_request=ready,
        )

    def to_ready(
        self,
        request: ProcurementRequestDraft,
        policy: ProcurementPolicySnapshot | None = None,
    ) -> ProcurementRequestReady:
        """Promote a draft only after the same deterministic checks as assess."""

        policy = policy or self._default_policy
        missing = self.missing_required_fields(request, policy)
        issues = self.blocking_issues(request, policy)
        if missing or issues:
            details = ", ".join([*missing, *issues])
            raise ValueError(f"request is not ready: {details}")

        payload = request.model_dump(exclude={"status"})
        payload["status"] = ProcurementRequestStatus.READY
        return ProcurementRequestReady.model_validate(payload)

    def detect_conflicts(
        self,
        current: RequestLike,
        patch: ProcurementRequestPatch,
        evidence: dict[str, str] | None = None,
        *,
        allow_overwrite_fields: Iterable[str] = (),
    ) -> list[FieldConflict]:
        """Find changes to already-present facts without applying them."""

        allowed = frozenset(allow_overwrite_fields)
        evidence = evidence or {}
        conflicts: list[FieldConflict] = []
        for field in patch.model_fields_set:
            if field not in _REQUEST_FIELDS or field in allowed:
                continue
            current_value = getattr(current, field)
            candidate_value = getattr(patch, field)
            if current_value is None or current_value == candidate_value:
                continue
            # Defaults such as a dietary count of zero are not a confirmed fact
            # unless that field was explicitly supplied on the draft.
            if field not in current.model_fields_set:
                continue
            conflicts.append(
                FieldConflict(
                    field=field,
                    current_value=current_value,
                    candidate_value=candidate_value,
                    candidate_evidence=evidence.get(field, ""),
                )
            )
        return conflicts

    def apply_patch(
        self,
        current: ProcurementRequestDraft,
        patch: ProcurementRequestPatch,
        *,
        allow_overwrite_fields: Iterable[str] = (),
    ) -> ProcurementRequestDraft:
        """Apply non-conflicting facts and increment the optimistic version.

        Existing explicit facts are preserved by default.  A caller may pass a
        field in ``allow_overwrite_fields`` only after an explicit confirmation.
        """

        allowed = frozenset(allow_overwrite_fields)
        conflicts = {
            conflict.field
            for conflict in self.detect_conflicts(
                current,
                patch,
                allow_overwrite_fields=allowed,
            )
        }
        changes = {
            field: getattr(patch, field)
            for field in patch.model_fields_set
            if field in _REQUEST_FIELDS and field not in conflicts
        }
        if not changes:
            return current.model_copy(deep=True)

        # Readiness is granted only by ``assess``/``to_ready``.  A merge itself
        # cannot write a READY status. ``model_copy`` also preserves
        # ``model_fields_set``, so untouched default zeroes do not accidentally
        # become confirmed facts after the first clarification.
        return current.model_copy(
            deep=True,
            update={
                **changes,
                "version": current.version + 1,
                "status": ProcurementRequestStatus.DRAFT,
            },
        )

    def apply_interpretation(
        self,
        current: ProcurementRequestDraft,
        interpretation: ProcurementInterpretationResult,
    ) -> ProcurementRequestDraft:
        """Apply only the safe patch carried by an interpretation result."""

        # Conflicting candidates are deliberately absent from extracted_fields;
        # the conflict records remain available for an explicit confirmation.
        return self.apply_patch(
            current,
            interpretation.extracted_fields,
            allow_overwrite_fields=(),
        )

    def default_plan(
        self,
        request: ProcurementRequestReady,
        policy: ProcurementPolicySnapshot | None = None,
    ) -> ProcurementPlan:
        """Create the same reviewable plan for the same request and policy."""

        policy = policy or self._default_policy
        desired = request.desired_quote_count or policy.default_target_supplier_count
        target_supplier_count = min(desired, policy.maximum_target_supplier_count)

        criteria = [
            "supplier_active",
            "category_match",
            "service_area_match",
            "capacity_sufficient",
            "lead_time_sufficient",
            "critical_fields_current",
        ]
        if request.invoice_required:
            criteria.append("invoice_available")
        if request.no_single_use_plastic:
            criteria.append("no_single_use_plastic")
        if request.vegetarian_count:
            criteria.append("vegetarian_requirement")
        if request.vegan_count:
            criteria.append("vegan_requirement")
        if request.gluten_free_count:
            criteria.append("gluten_free_requirement")
        if policy.budget_is_eliminatory:
            criteria.append("maximum_total_cents")

        if policy.negotiation_enabled:
            rounds = policy.maximum_negotiation_rounds
            topics = list(policy.allowed_negotiation_topics)
            target_total_cents = policy.target_total_cents
            if target_total_cents is None and request.maximum_total_cents is not None:
                target_total_cents = (
                    request.maximum_total_cents * policy.target_budget_percent // 100
                )
        else:
            rounds = 0
            topics = []
            target_total_cents = None

        return ProcurementPlan(
            request_id=request.request_id,
            target_supplier_count=target_supplier_count,
            eliminatory_criteria=criteria,
            ranking_weights=dict(policy.ranking_weights),
            response_deadline=request.response_deadline,
            negotiation_enabled=policy.negotiation_enabled,
            target_total_cents=target_total_cents,
            maximum_negotiation_rounds=rounds,
            allowed_negotiation_topics=topics,
            maximum_follow_ups=policy.maximum_follow_ups,
            approval_checkpoint=policy.approval_checkpoint,
            policy_snapshot=policy.model_copy(deep=True),
            version=1,
        )

    # Alias matching the tool name in the Dev 3 plan.
    create_procurement_plan = default_plan

    def update_plan(
        self,
        plan: ProcurementPlan,
        patch: ProcurementPlanPatch,
    ) -> ProcurementPlan:
        policy = plan.policy_snapshot
        if patch.negotiation_enabled is True and not policy.negotiation_enabled:
            raise ValueError("negotiation cannot be enabled outside the policy snapshot")
        if (
            patch.maximum_negotiation_rounds is not None
            and patch.maximum_negotiation_rounds > policy.maximum_negotiation_rounds
        ):
            raise ValueError("maximum_negotiation_rounds exceeds the policy snapshot")
        if patch.allowed_negotiation_topics is not None and not set(
            patch.allowed_negotiation_topics
        ).issubset(policy.allowed_negotiation_topics):
            raise ValueError("allowed_negotiation_topics exceed the policy snapshot")
        if (
            patch.maximum_follow_ups is not None
            and patch.maximum_follow_ups > policy.maximum_follow_ups
        ):
            raise ValueError("maximum_follow_ups exceeds the policy snapshot")
        if patch.eliminatory_criteria is not None and not set(plan.eliminatory_criteria).issubset(
            patch.eliminatory_criteria
        ):
            raise ValueError("eliminatory criteria cannot be removed from the frozen policy")

        changes = {
            field: getattr(patch, field)
            for field in patch.model_fields_set
            if getattr(patch, field) is not None
        }
        if not changes:
            return plan.model_copy(deep=True)

        if changes.get("negotiation_enabled") is False:
            changes["maximum_negotiation_rounds"] = 0
            changes["allowed_negotiation_topics"] = []
            changes["target_total_cents"] = None
        elif changes.get("negotiation_enabled") is True:
            changes.setdefault(
                "maximum_negotiation_rounds",
                plan.policy_snapshot.maximum_negotiation_rounds,
            )
            changes.setdefault(
                "allowed_negotiation_topics",
                list(plan.policy_snapshot.allowed_negotiation_topics),
            )

        payload = plan.model_dump()
        payload.update(changes)
        payload["version"] = plan.version + 1
        return ProcurementPlan.model_validate(payload)

    def _now(self) -> datetime | None:
        if self._clock is None:
            return None
        now = self._clock.now()
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("clock.now() must return a timezone-aware datetime")
        return now.astimezone(UTC)


__all__ = ["ProcurementRequestService"]
