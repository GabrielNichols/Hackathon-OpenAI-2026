"""Transactional outbox records and repository."""

from .records import OutboxItem
from .repository import OutboxRepository

__all__ = ["OutboxItem", "OutboxRepository"]
