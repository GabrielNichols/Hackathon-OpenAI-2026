from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated, Protocol, cast

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, Field

from app.modules.procurement_agent.orchestrator import (
    InvalidWorkflowStateError,
    ProcurementAgentOrchestrator,
    ProcurementNotFoundError,
    RequestIdempotencyConflictError,
)
from app.modules.procurement_agent.workflow import ProcurementWorkflowView
from app.modules.procurement_requests import MAX_BUYER_MESSAGE_CHARS
from app.modules.procurement_requests.ports import InterpretationProviderError

router = APIRouter(prefix="/api/v1/procurement-requests", tags=["buyer-workflow"])


class BuyerMessageInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    message: str = Field(min_length=1, max_length=MAX_BUYER_MESSAGE_CHARS)
    request_id: str | None = None


@dataclass(frozen=True, slots=True)
class BuyerIdentity:
    tenant_id: str
    user_id: str


class BuyerIdentityProvider(Protocol):
    async def __call__(self, request: Request) -> BuyerIdentity: ...


class RFQDispatcher(Protocol):
    async def __call__(
        self,
        procurement_request_id: str,
        rfq_round_id: str,
        identity: BuyerIdentity,
    ) -> None: ...


def get_orchestrator(request: Request) -> ProcurementAgentOrchestrator:
    return cast(ProcurementAgentOrchestrator, request.app.state.demo_container.orchestrator)


async def get_buyer_identity(request: Request) -> BuyerIdentity:
    provider = getattr(request.app.state, "buyer_identity_provider", None)
    if provider is None:
        return BuyerIdentity(tenant_id="org_demo", user_id="buyer_gabriel")
    return await cast(BuyerIdentityProvider, provider)(request)


OrchestratorDependency = Annotated[
    ProcurementAgentOrchestrator,
    Depends(get_orchestrator),
]
IdempotencyKeyHeader = Annotated[
    str | None,
    Header(alias="Idempotency-Key", min_length=8, max_length=200),
]
BuyerIdentityDependency = Annotated[BuyerIdentity, Depends(get_buyer_identity)]


@router.post("/messages", response_model=ProcurementWorkflowView)
async def receive_message(
    payload: BuyerMessageInput,
    orchestrator: OrchestratorDependency,
    identity: BuyerIdentityDependency,
    idempotency_key: IdempotencyKeyHeader = None,
) -> ProcurementWorkflowView:
    if payload.request_id is None and idempotency_key is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Idempotency-Key header is required when creating a request",
        )
    try:
        return await orchestrator.receive_message(
            payload.message,
            request_id=payload.request_id,
            tenant_id=identity.tenant_id,
            buyer_user_id=identity.user_id,
            idempotency_key=idempotency_key,
        )
    except ProcurementNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Request not found"
        ) from exc
    except InvalidWorkflowStateError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="INVALID_BUYER_MESSAGE",
        ) from exc
    except RequestIdempotencyConflictError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except InterpretationProviderError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=exc.reason_code,
        ) from exc


@router.post("/{request_id}/plan/confirm", response_model=ProcurementWorkflowView)
async def confirm_plan(
    request_id: str,
    request: Request,
    orchestrator: OrchestratorDependency,
    identity: BuyerIdentityDependency,
) -> ProcurementWorkflowView:
    try:
        result = await orchestrator.confirm_plan(request_id)
        dispatcher = getattr(request.app.state, "rfq_dispatcher", None)
        if result.rfq_round_id is not None and dispatcher is not None:
            await cast(RFQDispatcher, dispatcher)(
                result.request_id,
                result.rfq_round_id,
                identity,
            )
        return result
    except ProcurementNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Request not found"
        ) from exc
    except InvalidWorkflowStateError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc


@router.get("/{request_id}", response_model=ProcurementWorkflowView)
async def get_request(
    request_id: str,
    orchestrator: OrchestratorDependency,
) -> ProcurementWorkflowView:
    try:
        return await orchestrator.get(request_id)
    except ProcurementNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Request not found"
        ) from exc


__all__ = [
    "BuyerIdentity",
    "BuyerIdentityProvider",
    "RFQDispatcher",
    "router",
]
