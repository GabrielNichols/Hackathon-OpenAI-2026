from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ValidationError

from app.modules.buyer_timeline.audit import ActorType, AuditEvent, AuditPort
from app.modules.procurement_agent.models import (
    AuthorizationRequest,
    ToolExecutionContext,
    ToolResultEnvelope,
)
from app.modules.procurement_agent.ports import Clock, IdGenerator, PolicyPort


class ToolExecutionError(RuntimeError):
    code = "TOOL_EXECUTION_ERROR"


class ToolNotFoundError(ToolExecutionError):
    code = "TOOL_NOT_FOUND"


class ToolStateError(ToolExecutionError):
    code = "TOOL_NOT_ALLOWED_IN_STATE"


class ToolInputError(ToolExecutionError):
    code = "VALIDATION_ERROR"


class ToolPolicyDeniedError(ToolExecutionError):
    code = "POLICY_DENIED"


ToolHandler = Callable[[BaseModel], Awaitable[BaseModel]]


@dataclass(frozen=True, slots=True)
class ToolSpec:
    name: str
    input_model: type[BaseModel]
    output_model: type[BaseModel]
    allowed_states: frozenset[str]
    policy_action: str
    audit_event_type: str
    timeout_seconds: float
    idempotent: bool
    handler: ToolHandler


class ToolRegistry:
    """Validates state, schema and policy before any typed side effect is called."""

    def __init__(
        self,
        *,
        policy: PolicyPort,
        audit: AuditPort,
        clock: Clock,
        ids: IdGenerator,
    ) -> None:
        self._policy = policy
        self._audit = audit
        self._clock = clock
        self._ids = ids
        self._specs: dict[str, ToolSpec] = {}

    def register(self, spec: ToolSpec) -> None:
        if spec.name in self._specs:
            raise ValueError(f"Tool already registered: {spec.name}")
        self._specs[spec.name] = spec

    async def execute(
        self,
        name: str,
        raw_arguments: dict[str, Any],
        context: ToolExecutionContext,
    ) -> ToolResultEnvelope:
        spec = self._specs.get(name)
        if spec is None:
            raise ToolNotFoundError(name)

        if context.aggregate_state not in spec.allowed_states:
            await self._append_blocked(name, context, "TOOL_NOT_ALLOWED_IN_STATE")
            raise ToolStateError(
                f"{name} is not allowed while request is {context.aggregate_state}"
            )

        try:
            typed_input = spec.input_model.model_validate(raw_arguments)
        except ValidationError as exc:
            await self._append_blocked(name, context, "VALIDATION_ERROR")
            raise ToolInputError(str(exc)) from exc

        authorization = await self._policy.authorize(
            AuthorizationRequest(
                actor_type="agent",
                actor_id=context.actor_id,
                action=spec.policy_action,
                aggregate_type="procurement_request",
                aggregate_id=context.aggregate_id,
                current_state=context.aggregate_state,
                arguments=typed_input.model_dump(mode="json"),
                procurement_policy=context.procurement_policy,
            )
        )
        if not authorization.allowed:
            await self._append_blocked(name, context, authorization.reason_code)
            raise ToolPolicyDeniedError(authorization.reason)

        await self._audit.append(
            [
                self._event(
                    event_type="AGENT_TOOL_AUTHORIZED",
                    context=context,
                    payload={"tool": name, "policy_reason": authorization.reason_code},
                )
            ]
        )

        try:
            raw_output = await asyncio.wait_for(
                spec.handler(typed_input), timeout=spec.timeout_seconds
            )
            typed_output = spec.output_model.model_validate(raw_output)
        except TimeoutError as exc:
            await self._append_failed(name, context, "TOOL_TIMEOUT")
            raise ToolExecutionError(f"Tool timed out: {name}") from exc
        except Exception as exc:
            await self._append_failed(name, context, type(exc).__name__)
            raise ToolExecutionError(f"Tool failed: {name} ({type(exc).__name__})") from exc

        await self._audit.append(
            [
                self._event(
                    event_type=spec.audit_event_type,
                    context=context,
                    payload={"tool": name, "idempotent": spec.idempotent},
                ),
                self._event(
                    event_type="AGENT_TOOL_EXECUTED",
                    context=context,
                    payload={"tool": name},
                ),
            ]
        )
        return ToolResultEnvelope(
            tool_name=name,
            output=typed_output.model_dump(mode="json"),
            audit_event_type=spec.audit_event_type,
        )

    async def _append_blocked(
        self, name: str, context: ToolExecutionContext, reason_code: str
    ) -> None:
        await self._audit.append(
            [
                self._event(
                    event_type="AGENT_ACTION_BLOCKED",
                    context=context,
                    payload={"tool": name, "reason_code": reason_code},
                )
            ]
        )

    async def _append_failed(
        self, name: str, context: ToolExecutionContext, reason_code: str
    ) -> None:
        await self._audit.append(
            [
                self._event(
                    event_type="AGENT_TOOL_FAILED",
                    context=context,
                    payload={"tool": name, "reason_code": reason_code},
                )
            ]
        )

    def _event(
        self,
        *,
        event_type: str,
        context: ToolExecutionContext,
        payload: dict[str, Any],
    ) -> AuditEvent:
        return AuditEvent(
            event_id=self._ids.new("evt"),
            event_type=event_type,
            aggregate_type="procurement_request",
            aggregate_id=context.aggregate_id,
            actor_type=ActorType.AGENT,
            actor_id=context.actor_id,
            occurred_at=self._clock.now(),
            correlation_id=context.correlation_id,
            agent_run_id=context.agent_run_id,
            payload=payload,
        )
