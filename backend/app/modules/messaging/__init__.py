"""Messaging adapters used by procurement execution."""

from .gateway import (
    DeliveryGateway,
    DeliveryRecord,
    DeliveryState,
    FakeDeliveryGateway,
    GatewayDeliveryStatus,
    GatewayIdempotencyConflict,
    GatewayMessageNotFound,
    GatewaySendResult,
    OutboundMessage,
)
from .manual_link import (
    ManualDeliveryAction,
    ManualDeliveryChannel,
    ManualLinkDeliveryAdapter,
)

__all__ = [
    "DeliveryGateway",
    "DeliveryRecord",
    "DeliveryState",
    "FakeDeliveryGateway",
    "GatewayDeliveryStatus",
    "GatewayIdempotencyConflict",
    "GatewayMessageNotFound",
    "GatewaySendResult",
    "ManualDeliveryAction",
    "ManualDeliveryChannel",
    "ManualLinkDeliveryAdapter",
    "OutboundMessage",
]
