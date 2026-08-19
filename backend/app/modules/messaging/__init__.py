"""Messaging adapters used by procurement execution."""

from .gateway import (
    DeliveryRecord,
    DeliveryState,
    FakeDeliveryGateway,
    GatewayDeliveryStatus,
    GatewayIdempotencyConflict,
    GatewayMessageNotFound,
    GatewaySendResult,
    OutboundMessage,
)

__all__ = [
    "DeliveryRecord",
    "DeliveryState",
    "FakeDeliveryGateway",
    "GatewayDeliveryStatus",
    "GatewayIdempotencyConflict",
    "GatewayMessageNotFound",
    "GatewaySendResult",
    "OutboundMessage",
]
