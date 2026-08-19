from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from itertools import count
from uuid import uuid4

from app.modules.procurement_agent.models import (
    AgentRun,
    AuthorizationDecision,
    AuthorizationRequest,
    CreateRFQRoundCommand,
    RFQRoundDTO,
)


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(UTC)


class FixedClock:
    def __init__(self, value: datetime) -> None:
        if value.tzinfo is None:
            raise ValueError("FixedClock requires a timezone-aware datetime")
        self._value = value

    def now(self) -> datetime:
        return self._value


class UUIDIdGenerator:
    def new(self, prefix: str) -> str:
        return f"{prefix}_{uuid4().hex}"


class SequentialIdGenerator:
    def __init__(self) -> None:
        self._counter = count(1)

    def new(self, prefix: str) -> str:
        return f"{prefix}_{next(self._counter):04d}"


class PrototypePolicy:
    """Small deterministic adapter; it can be replaced by Dev 1's PolicyPort."""

    _allowed_actions: dict[str, frozenset[str]] = {
        "READY": frozenset({"start_sourcing"}),
        "SOURCING": frozenset(
            {
                "search_suppliers",
                "evaluate_supplier_eligibility",
                "select_rfq_recipients",
                "create_rfq_round",
            }
        ),
    }

    def __init__(self, denied_actions: set[str] | None = None) -> None:
        self._denied_actions = denied_actions or set()

    async def authorize(self, request: AuthorizationRequest) -> AuthorizationDecision:
        if request.action in self._denied_actions:
            return AuthorizationDecision(
                allowed=False,
                reason_code="ACTION_DENIED_BY_TEST_POLICY",
                reason=f"Action denied by configured policy: {request.action}",
            )

        allowed = self._allowed_actions.get(request.current_state, frozenset())
        if request.action not in allowed:
            return AuthorizationDecision(
                allowed=False,
                reason_code="ACTION_NOT_ALLOWED_IN_STATE",
                reason=(
                    f"Action {request.action} is not allowed while request is "
                    f"{request.current_state}"
                ),
            )

        return AuthorizationDecision(
            allowed=True,
            reason_code="POLICY_ALLOWED",
            reason="Action is allowed by the prototype policy snapshot.",
        )


class IdempotencyConflictError(RuntimeError):
    pass


class InMemoryRFQExecutionAdapter:
    """Creates RFQ drafts only. It does not claim delivery or mutate RFQ_ACTIVE."""

    def __init__(
        self, *, clock: SystemClock | FixedClock, ids: UUIDIdGenerator | SequentialIdGenerator
    ) -> None:
        self._clock = clock
        self._ids = ids
        self._by_key: dict[str, tuple[str, RFQRoundDTO]] = {}
        self.create_call_count = 0

    async def create_round(self, command: CreateRFQRoundCommand) -> RFQRoundDTO:
        payload = command.model_dump(mode="json")
        # Retries may be performed by a different agent run/correlation. Those
        # trace identifiers are intentionally excluded from the semantic hash;
        # the immutable request, plan, recipients and policy remain protected.
        payload.pop("sourcing_run_id", None)
        context = payload.get("context", {})
        if isinstance(context, dict):
            context.pop("correlation_id", None)
            context.pop("causation_id", None)
            context.pop("agent_run_id", None)
        payload_hash = hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        key = command.context.idempotency_key
        existing = self._by_key.get(key)
        if existing is not None:
            existing_hash, existing_round = existing
            if existing_hash != payload_hash:
                raise IdempotencyConflictError(
                    f"Different payload supplied for idempotency key {key}"
                )
            return existing_round

        self.create_call_count += 1
        round_dto = RFQRoundDTO(
            rfq_round_id=self._ids.new("rfq"),
            procurement_request_id=command.procurement_request_id,
            recipient_supplier_ids=sorted(command.recipient_supplier_ids),
            created_at=self._clock.now(),
        )
        self._by_key[key] = (payload_hash, round_dto)
        return round_dto


class InMemoryAgentRunRepository:
    def __init__(self) -> None:
        self._runs: dict[str, AgentRun] = {}

    async def save(self, run: AgentRun) -> None:
        self._runs[run.run_id] = run.model_copy(deep=True)

    async def get(self, run_id: str) -> AgentRun | None:
        run = self._runs.get(run_id)
        return run.model_copy(deep=True) if run else None

    async def list_for_request(self, request_id: str) -> list[AgentRun]:
        return [
            run.model_copy(deep=True)
            for run in self._runs.values()
            if run.procurement_request_id == request_id
        ]
