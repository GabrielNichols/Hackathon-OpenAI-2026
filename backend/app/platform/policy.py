from typing import Any, Protocol

from app.contracts import (
    ActorType,
    AuditEventDTO,
    AuditPort,
    AuthorizationDecision,
    AuthorizationRequest,
    Clock,
    IdGenerator,
)


def _deny(
    reason_code: str,
    reason: str,
    *,
    human_review: bool = True,
    constraints: dict[str, Any] | None = None,
) -> AuthorizationDecision:
    return AuthorizationDecision(
        allowed=False,
        reason_code=reason_code,
        reason=reason,
        constraints=constraints or {},
        requires_human_review=human_review,
    )


class PersistedHumanApprovalVerifier(Protocol):
    """Trusted boundary that verifies an approval against persisted state."""

    async def is_approved(
        self,
        *,
        tenant_id: str,
        approval_id: str,
        action: str,
        aggregate_type: str,
        aggregate_id: str,
    ) -> bool: ...


class DeterministicPolicyEngine:
    def __init__(
        self,
        approval_verifier: PersistedHumanApprovalVerifier | None = None,
    ) -> None:
        self._approval_verifier = approval_verifier

    async def authorize(self, request: AuthorizationRequest) -> AuthorizationDecision:
        policy = request.procurement_policy
        arguments = request.arguments

        if request.actor_tenant_id != request.resource_tenant_id:
            return _deny("CROSS_TENANT_ACCESS", "Actor and resource tenants do not match")

        allowed_actions = policy.get("allowed_actions_by_state", {}).get(request.current_state)
        if allowed_actions is not None and request.action not in allowed_actions:
            return _deny(
                "ACTION_NOT_ALLOWED_IN_STATE",
                f"Action {request.action} is not allowed from {request.current_state}",
            )

        if request.actor_type == ActorType.AGENT and request.action in {
            "approve_spend",
            "approve_procurement",
            "grant_approval",
        }:
            return _deny("AGENT_CANNOT_APPROVE_SPEND", "A human must approve spend")

        if request.action in {"disclose_competitor_data", "share_competing_quote"}:
            return _deny(
                "COMPETITOR_DATA_DISCLOSURE_FORBIDDEN",
                "Supplier data cannot be disclosed to a competitor",
                human_review=False,
            )

        changes = arguments.get("changes", {})
        if request.actor_type == ActorType.AGENT and (
            request.action == "change_mandatory_requirement"
            or (
                "mandatory_requirements" in changes
                and changes["mandatory_requirements"]
                != policy.get("mandatory_requirements", changes["mandatory_requirements"])
            )
        ):
            return _deny(
                "MANDATORY_REQUIREMENT_CHANGE_FORBIDDEN",
                "An agent cannot change an eliminatory requirement",
            )

        maximum_budget = policy.get("maximum_total_cents")
        if maximum_budget is not None:
            if not isinstance(maximum_budget, int) or isinstance(maximum_budget, bool):
                return _deny("INVALID_POLICY", "maximum_total_cents must be integer cents")
            for key in ("amount_cents", "total_cents", "maximum_total_cents"):
                amount = arguments.get(key)
                if amount is None:
                    continue
                if not isinstance(amount, int) or isinstance(amount, bool) or amount < 0:
                    return _deny("INVALID_MONEY_ARGUMENT", f"{key} must be integer cents")
                if amount > maximum_budget:
                    return _deny(
                        "BUDGET_EXCEEDED",
                        "Requested amount exceeds the authorized budget",
                        constraints={"maximum_total_cents": maximum_budget},
                    )

        if request.action in {"start_sourcing", "create_rfq_round", "send_rfq"}:
            required = set(policy.get("required_fields_before_sourcing", []))
            present = set(arguments.get("present_fields", []))
            missing = sorted(required - present)
            if missing:
                return _deny(
                    "MISSING_REQUIRED_FIELDS",
                    "Required fields are missing before sourcing",
                    constraints={"missing_fields": missing},
                )

        if request.action == "run_negotiation_round":
            if arguments.get("round_number", 1) > policy.get("maximum_negotiation_rounds", 0):
                return _deny("NEGOTIATION_ROUND_LIMIT", "Negotiation round limit exceeded")
            if arguments.get("topic") not in policy.get("allowed_negotiation_topics", []):
                return _deny("NEGOTIATION_TOPIC_NOT_ALLOWED", "Negotiation topic is forbidden")

        if request.action == "send_award":
            approval_id = arguments.get("approval_id")
            approval_verified = False
            if (
                isinstance(approval_id, str)
                and approval_id.strip()
                and self._approval_verifier is not None
            ):
                approval_verified = await self._approval_verifier.is_approved(
                    tenant_id=request.resource_tenant_id,
                    approval_id=approval_id,
                    action=request.action,
                    aggregate_type=request.aggregate_type,
                    aggregate_id=request.aggregate_id,
                )
            if not approval_verified:
                return _deny(
                    "AWARD_REQUIRES_HUMAN_APPROVAL",
                    "Award requires a verified persisted human approval",
                )

        if request.action == "send_supplier_follow_up" and arguments.get(
            "follow_up_count", 0
        ) >= policy.get("maximum_follow_ups", 0):
            return _deny("FOLLOW_UP_LIMIT", "Maximum follow-up count reached")

        contact_hour = arguments.get("contact_hour")
        contact_hours = policy.get("contact_hours")
        if contact_hour is not None and contact_hours is not None:  # noqa: SIM102
            if not contact_hours["start"] <= contact_hour < contact_hours["end"]:
                return _deny("OUTSIDE_CONTACT_HOURS", "External contact is outside allowed hours")

        return AuthorizationDecision(
            allowed=True,
            reason_code="ALLOWED",
            reason="Action is authorized",
            constraints={},
            requires_human_review=False,
        )


class AuditedPolicyEngine:
    def __init__(
        self,
        engine: DeterministicPolicyEngine,
        audit: AuditPort,
        clock: Clock,
        ids: IdGenerator,
    ) -> None:
        self._engine = engine
        self._audit = audit
        self._clock = clock
        self._ids = ids

    async def authorize(self, request: AuthorizationRequest) -> AuthorizationDecision:
        decision = await self._engine.authorize(request)
        if decision.allowed:
            return decision
        event = AuditEventDTO(
            event_id=self._ids.new("evt"),
            event_type=(
                "AGENT_ACTION_BLOCKED"
                if request.actor_type == ActorType.AGENT
                else "USER_ACTION_BLOCKED"
            ),
            aggregate_type=request.aggregate_type,
            aggregate_id=request.aggregate_id,
            actor_type=request.actor_type,
            actor_id=request.actor_id,
            occurred_at=self._clock.now(),
            previous_state=request.current_state,
            new_state=request.current_state,
            correlation_id=str(request.arguments.get("correlation_id", "cor_policy")),
            causation_id=request.arguments.get("causation_id"),
            agent_run_id=request.arguments.get("agent_run_id"),
            idempotency_key=request.arguments.get("idempotency_key"),
            payload={
                "action": request.action,
                "reason_code": decision.reason_code,
                "requires_human_review": decision.requires_human_review,
            },
        )
        await self._audit.append([event])
        return decision
