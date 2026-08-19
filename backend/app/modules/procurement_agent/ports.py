from __future__ import annotations

from datetime import datetime
from typing import Protocol

from app.modules.buyer_timeline.audit import AuditPort
from app.modules.procurement_agent.models import (
    AuthorizationDecision,
    AuthorizationRequest,
    CreateRFQRoundCommand,
    RFQRoundDTO,
)


class Clock(Protocol):
    def now(self) -> datetime: ...


class IdGenerator(Protocol):
    def new(self, prefix: str) -> str: ...


class PolicyPort(Protocol):
    async def authorize(self, request: AuthorizationRequest) -> AuthorizationDecision: ...


class RFQExecutionPort(Protocol):
    async def create_round(self, command: CreateRFQRoundCommand) -> RFQRoundDTO: ...


__all__ = [
    "AuditPort",
    "Clock",
    "IdGenerator",
    "PolicyPort",
    "RFQExecutionPort",
]
