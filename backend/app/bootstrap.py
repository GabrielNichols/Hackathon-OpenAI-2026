from __future__ import annotations

import secrets
from datetime import datetime

from app.modules.messaging.gateway import FakeDeliveryGateway
from app.modules.rfq.service import ProcurementExecutionService
from app.modules.rfq.store import InMemoryExecutionStore
from app.shared.runtime import FixedClock, SystemClock
from app.shared.tokens import SignedTokenService


def create_execution_service(
    *,
    now: datetime | None = None,
    auto_ack: bool = False,
    token_secret: str | None = None,
) -> ProcurementExecutionService:
    clock = FixedClock(now) if now is not None else SystemClock()
    gateway = FakeDeliveryGateway(auto_ack=auto_ack, clock=clock)
    token_service = SignedTokenService(
        token_secret or secrets.token_urlsafe(32),
        clock=clock,
    )
    return ProcurementExecutionService(
        store=InMemoryExecutionStore(),
        clock=clock,
        token_service=token_service,
        delivery_gateway=gateway,
    )


__all__ = ["create_execution_service"]
