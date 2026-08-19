from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class AgentStopReason(StrEnum):
    NEEDS_CLARIFICATION = "NEEDS_CLARIFICATION"
    AWAITING_PLAN_CONFIRMATION = "AWAITING_PLAN_CONFIRMATION"
    NO_ELIGIBLE_SUPPLIERS = "NO_ELIGIBLE_SUPPLIERS"
    AWAITING_EXTERNAL_RESPONSE = "AWAITING_EXTERNAL_RESPONSE"
    AWAITING_APPROVAL = "AWAITING_APPROVAL"
    TERMINAL = "TERMINAL"
    ACTION_BLOCKED = "ACTION_BLOCKED"
    MAX_STEPS_REACHED = "MAX_STEPS_REACHED"


class AgentRunStatus(StrEnum):
    RUNNING = "RUNNING"
    PAUSED = "PAUSED"
    COMPLETED = "COMPLETED"
    BLOCKED = "BLOCKED"


class AgentRun(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: str
    procurement_request_id: str
    correlation_id: str
    status: AgentRunStatus = AgentRunStatus.RUNNING
    started_at: datetime
    finished_at: datetime | None = None
    stop_reason: AgentStopReason | None = None
    step_count: int = 0
    max_steps: int = 16
    decision_summary: str | None = None


class AuthorizationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    actor_type: str
    actor_id: str | None
    action: str
    aggregate_type: str
    aggregate_id: str
    current_state: str
    arguments: dict[str, Any]
    procurement_policy: dict[str, Any]


class AuthorizationDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    allowed: bool
    reason_code: str
    reason: str
    constraints: dict[str, Any] = Field(default_factory=dict)


class CommandContext(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tenant_id: str
    actor_type: Literal["human", "agent", "system"]
    actor_id: str | None
    correlation_id: str
    causation_id: str | None = None
    agent_run_id: str | None = None
    idempotency_key: str


class CreateRFQRoundCommand(BaseModel):
    model_config = ConfigDict(extra="forbid")

    procurement_request_id: str
    request_version: int = Field(ge=1)
    plan_version: int = Field(ge=1)
    sourcing_run_id: str
    recipient_supplier_ids: list[str] = Field(min_length=1)
    response_deadline: datetime
    requirements_snapshot: dict[str, Any]
    policy_snapshot: dict[str, Any]
    context: CommandContext


class RFQRoundDTO(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rfq_round_id: str
    procurement_request_id: str
    status: Literal["DRAFT"] = "DRAFT"
    recipient_supplier_ids: list[str]
    created_at: datetime
    version: int = 1


class ToolExecutionContext(BaseModel):
    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)

    aggregate_id: str
    aggregate_state: str
    actor_id: str | None
    correlation_id: str
    agent_run_id: str
    procurement_policy: dict[str, Any]


class ToolResultEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tool_name: str
    output: dict[str, Any]
    audit_event_type: str
