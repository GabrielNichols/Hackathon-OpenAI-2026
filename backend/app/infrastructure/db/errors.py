"""Infrastructure errors mapped to stable public error codes at the API boundary."""


class PersistenceError(RuntimeError):
    """Base class for deterministic persistence failures."""


class TenantScopeViolation(PersistenceError):
    """Raised when a record is used from a differently scoped unit of work."""

    def __init__(self, expected_tenant_id: str, actual_tenant_id: str) -> None:
        super().__init__(
            f"tenant scope mismatch: expected {expected_tenant_id!r}, got {actual_tenant_id!r}"
        )
        self.expected_tenant_id = expected_tenant_id
        self.actual_tenant_id = actual_tenant_id


class RecordNotFound(PersistenceError):
    """Raised when a required persistence record does not exist."""

    def __init__(self, record_type: str, record_id: str) -> None:
        super().__init__(f"{record_type} {record_id!r} was not found")
        self.record_type = record_type
        self.record_id = record_id


class OptimisticLockConflict(PersistenceError):
    """Raised when an aggregate was changed after it was read."""

    def __init__(self, aggregate_id: str, expected_version: int) -> None:
        super().__init__(
            f"aggregate {aggregate_id!r} is not at expected version {expected_version}"
        )
        self.aggregate_id = aggregate_id
        self.expected_version = expected_version


class IdempotencyConflict(PersistenceError):
    """Raised when a key is reused with a different canonical payload."""

    def __init__(self, key: str) -> None:
        super().__init__(f"idempotency key {key!r} was reused with a different payload")
        self.key = key


class IdempotencyInProgress(PersistenceError):
    """Raised for an incomplete reservation visible to the same transaction."""

    def __init__(self, key: str) -> None:
        super().__init__(f"idempotency key {key!r} is still in progress")
        self.key = key


class OutboxStateConflict(PersistenceError):
    """Raised when an outbox transition is invalid for its persisted status."""

    def __init__(self, item_id: str, status: str, requested_status: str) -> None:
        super().__init__(
            f"outbox item {item_id!r} cannot move from {status!r} to {requested_status!r}"
        )
        self.item_id = item_id
        self.status = status
        self.requested_status = requested_status
