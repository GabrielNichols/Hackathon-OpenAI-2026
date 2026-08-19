from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.contracts import ActorType, AuthorizationDecision, AuthorizationRequest


def _request_payload() -> dict[str, object]:
    return {
        "actor_type": "agent",
        "actor_id": "agent_buyer",
        "actor_tenant_id": "org_demo",
        "resource_tenant_id": "org_demo",
        "action": "send_award",
        "aggregate_type": "procurement_request",
        "aggregate_id": "pr_demo",
        "current_state": "APPROVED",
        "arguments": {"approval_id": "apr_001"},
        "procurement_policy": {"approval_required": True},
    }


def test_authorization_request_requires_explicit_tenant_context() -> None:
    for missing_field in ("actor_tenant_id", "resource_tenant_id"):
        payload = _request_payload()
        payload.pop(missing_field)

        with pytest.raises(ValidationError):
            AuthorizationRequest.model_validate(payload)


def test_authorization_request_round_trips_without_orm_types() -> None:
    request = AuthorizationRequest.model_validate(_request_payload())

    assert request.actor_type is ActorType.AGENT
    assert AuthorizationRequest.model_validate_json(request.model_dump_json()) == request


def test_authorization_decision_exposes_human_review() -> None:
    decision = AuthorizationDecision(
        allowed=False,
        reason_code="AGENT_SPEND_APPROVAL_FORBIDDEN",
        reason="An agent cannot approve company spend.",
        requires_human_review=True,
    )

    assert decision.model_dump(mode="json") == {
        "allowed": False,
        "reason_code": "AGENT_SPEND_APPROVAL_FORBIDDEN",
        "reason": "An agent cannot approve company spend.",
        "constraints": {},
        "requires_human_review": True,
    }


def test_authorization_constraints_defaults_are_not_shared() -> None:
    first = AuthorizationDecision(allowed=True, reason_code="ALLOWED", reason="Allowed")
    second = AuthorizationDecision(allowed=True, reason_code="ALLOWED", reason="Allowed")

    first.constraints["maximum_total_cents"] = 450_000

    assert second.constraints == {}


def test_reason_code_must_be_upper_snake_case() -> None:
    with pytest.raises(ValidationError):
        AuthorizationDecision(allowed=False, reason_code="budget exceeded", reason="Denied")
