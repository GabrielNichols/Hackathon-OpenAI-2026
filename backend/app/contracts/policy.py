"""Authorization request/decision contracts for deterministic policy checks."""

from __future__ import annotations

from typing import Any

from pydantic import Field

from .common import (
    ActionName,
    ActorType,
    AggregateType,
    ContractModel,
    ContractString,
    EntityId,
    StableCode,
)


class AuthorizationRequest(ContractModel):
    actor_type: ActorType
    actor_id: EntityId | None
    actor_tenant_id: EntityId
    resource_tenant_id: EntityId
    action: ActionName
    aggregate_type: AggregateType
    aggregate_id: EntityId
    current_state: ContractString
    arguments: dict[str, Any]
    procurement_policy: dict[str, Any]


class AuthorizationDecision(ContractModel):
    allowed: bool
    reason_code: StableCode
    reason: ContractString
    constraints: dict[str, Any] = Field(default_factory=dict)
    requires_human_review: bool = False
