"""Compatibility boundary from the current Dev 3 prototype into Dev 4.

Dev 3 intentionally owns agent-facing DTOs while Dev 4 owns execution DTOs.
This adapter keeps those contracts explicit instead of relying on two
homonymous Pydantic models being structurally compatible.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from app.modules.rfq.contracts import (
    CommandContextDTO,
    CreateRFQRoundCommand,
    ExecutionPolicySnapshotDTO,
    RFQRequirementsSnapshotDTO,
)
from app.modules.rfq.service import ProcurementExecutionService
from app.shared.errors import DomainError, ErrorCode


class Dev3RFQExecutionAdapter:
    """Map Dev 3's draft-RFQ port to the richer Dev 4 execution service."""

    def __init__(
        self,
        service: ProcurementExecutionService,
        *,
        default_timezone: str = "America/Sao_Paulo",
    ) -> None:
        self._service = service
        self._default_timezone = default_timezone

    async def create_round(self, command: object) -> Any:
        raw = _dump(command, field="command")
        requirements_raw = _dump(
            _required(raw, "requirements_snapshot"),
            field="requirements_snapshot",
        )
        policy_raw = _dump(
            _required(raw, "policy_snapshot"),
            field="policy_snapshot",
        )
        context_raw = _dump(_required(raw, "context"), field="context")

        recipients = list(_required(raw, "recipient_supplier_ids"))
        execution_command = CreateRFQRoundCommand(
            context=self._map_context(
                context_raw,
                sourcing_run_id=raw.get("sourcing_run_id"),
            ),
            procurement_request_id=_required(raw, "procurement_request_id"),
            request_version=_required(raw, "request_version"),
            plan_version=_required(raw, "plan_version"),
            recipient_supplier_ids=recipients,
            response_deadline=_required(raw, "response_deadline"),
            requirements=self._map_requirements(requirements_raw, policy_raw),
            execution_policy=self._map_policy(requirements_raw, policy_raw),
        )
        created = await self._service.create_round(execution_command)

        # Dev 3 validates this result with its own strict RFQRoundDTO.
        return {
            "rfq_round_id": created.rfq_round_id,
            "procurement_request_id": created.procurement_request_id,
            "status": "DRAFT",
            "recipient_supplier_ids": sorted(recipients),
            "created_at": created.created_at,
            "version": created.round_version,
        }

    def _map_context(
        self,
        raw: Mapping[str, Any],
        *,
        sourcing_run_id: object,
    ) -> CommandContextDTO:
        actor_id = raw.get("actor_id")
        if not isinstance(actor_id, str) or not actor_id.strip():
            raise DomainError(
                ErrorCode.VALIDATION_ERROR,
                "Dev 3 must provide an authenticated actor_id",
            )
        return CommandContextDTO(
            tenant_id=_required(raw, "tenant_id"),
            idempotency_key=_required(raw, "idempotency_key"),
            correlation_id=_required(raw, "correlation_id"),
            causation_id=raw.get("causation_id"),
            actor_type=_required(raw, "actor_type"),
            actor_id=actor_id,
            agent_run_id=raw.get("agent_run_id") or sourcing_run_id,
        )

    def _map_requirements(
        self,
        raw: Mapping[str, Any],
        policy: Mapping[str, Any],
    ) -> RFQRequirementsSnapshotDTO:
        mandatory = {str(item) for item in raw.get("mandatory_requirements", ())}
        if raw.get("invoice_required") is True:
            mandatory.add("invoice")
        if any(
            int(raw.get(field, 0) or 0) > 0
            for field in (
                "vegetarian_count",
                "vegan_count",
                "gluten_free_count",
            )
        ):
            mandatory.add("dietary_restrictions")
        if raw.get("no_single_use_plastic") is True:
            mandatory.add("no_single_use_plastic")

        location_city = raw.get("location_city") or policy.get("default_location_city")
        if not location_city:
            raise DomainError(
                ErrorCode.VALIDATION_ERROR,
                "Dev 3 requirements must resolve location_city",
            )

        return RFQRequirementsSnapshotDTO(
            description=_required(raw, "description"),
            category=_enum_value(_required(raw, "category")),
            event_date=_required(raw, "event_date"),
            delivery_time=_required(raw, "delivery_time"),
            timezone=raw.get("timezone") or self._default_timezone,
            location_city=location_city,
            location_district=raw.get("location_district"),
            full_address=raw.get("full_address"),
            people_count=_required(raw, "people_count"),
            maximum_total_cents=raw.get("maximum_total_cents"),
            currency=_enum_value(raw.get("currency", "BRL")),
            vegetarian_count=raw.get("vegetarian_count", 0),
            vegan_count=raw.get("vegan_count", 0),
            gluten_free_count=raw.get("gluten_free_count", 0),
            invoice_required=raw.get("invoice_required"),
            no_single_use_plastic=raw.get("no_single_use_plastic"),
            mandatory_requirements=sorted(mandatory),
        )

    def _map_policy(
        self,
        requirements: Mapping[str, Any],
        raw: Mapping[str, Any],
    ) -> ExecutionPolicySnapshotDTO:
        negotiation_enabled = bool(raw.get("negotiation_enabled", False))
        return ExecutionPolicySnapshotDTO(
            source_policy_version=raw.get("version", 1),
            minimum_confirmed_deliveries=raw.get(
                "minimum_confirmed_deliveries",
                1,
            ),
            maximum_follow_ups=raw.get("maximum_follow_ups", 0),
            maximum_total_cents=requirements.get("maximum_total_cents"),
            target_total_cents=raw.get("target_total_cents"),
            ranking_weights=_normalize_weights(_required(raw, "ranking_weights")),
            negotiation_enabled=negotiation_enabled,
            maximum_negotiation_rounds=(
                raw.get("maximum_negotiation_rounds", 0) if negotiation_enabled else 0
            ),
            allowed_negotiation_topics=list(raw.get("allowed_negotiation_topics", ())),
            approver_user_id=_required(requirements, "approver_user_id"),
        )


def _dump(value: object, *, field: str) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    if hasattr(value, "model_dump"):
        dumped = value.model_dump(mode="python")
        if isinstance(dumped, dict):
            return dumped
    raise DomainError(
        ErrorCode.VALIDATION_ERROR,
        f"{field} must be a mapping or Pydantic model",
    )


def _required(source: Mapping[str, Any], field: str) -> Any:
    value = source.get(field)
    if value is None or value == "" or value == []:
        raise DomainError(
            ErrorCode.VALIDATION_ERROR,
            f"Dev 3 handoff is missing {field}",
            details={"field": field},
        )
    return value


def _normalize_weights(value: object) -> dict[str, int]:
    if not isinstance(value, Mapping):
        raise DomainError(
            ErrorCode.VALIDATION_ERROR,
            "ranking_weights must be a mapping",
        )
    aliases = {
        "total_price": "price",
        "mandatory_requirements": "restrictions",
        "response_time": "response",
    }
    normalized: dict[str, int] = {}
    for raw_name, raw_weight in value.items():
        name = aliases.get(str(raw_name), str(raw_name))
        if isinstance(raw_weight, bool) or not isinstance(raw_weight, int):
            raise DomainError(
                ErrorCode.VALIDATION_ERROR,
                "ranking weight must be an integer",
                details={"criterion": str(raw_name)},
            )
        normalized[name] = normalized.get(name, 0) + raw_weight
    return normalized


def _enum_value(value: object) -> str:
    return str(getattr(value, "value", value))


__all__ = ["Dev3RFQExecutionAdapter"]
