import pytest

from app.contracts import ErrorCode
from app.platform.idempotency import (
    IdempotencyConflictError,
    InMemoryIdempotencyRegistry,
    canonical_payload_hash,
)


@pytest.mark.asyncio
async def test_same_idempotency_key_and_payload_returns_original_result() -> None:
    registry = InMemoryIdempotencyRegistry()
    calls = 0

    async def operation() -> dict[str, str]:
        nonlocal calls
        calls += 1
        return {"id": "rfq_1"}

    first = await registry.execute(
        tenant_id="org_demo",
        operation="create_rfq",
        key="create-rfq-1",
        payload={"people_count": 80},
        handler=operation,
    )
    second = await registry.execute(
        tenant_id="org_demo",
        operation="create_rfq",
        key="create-rfq-1",
        payload={"people_count": 80},
        handler=operation,
    )
    assert first == second == {"id": "rfq_1"}
    assert calls == 1


@pytest.mark.asyncio
async def test_same_idempotency_key_with_different_payload_is_rejected() -> None:
    registry = InMemoryIdempotencyRegistry()
    await registry.execute(
        tenant_id="org_demo",
        operation="create_rfq",
        key="create-rfq-1",
        payload={"people_count": 80},
        handler=lambda: {"id": "rfq_1"},
    )
    with pytest.raises(IdempotencyConflictError) as raised:
        await registry.execute(
            tenant_id="org_demo",
            operation="create_rfq",
            key="create-rfq-1",
            payload={"people_count": 81},
            handler=lambda: {"id": "rfq_2"},
        )
    assert raised.value.code == ErrorCode.IDEMPOTENCY_CONFLICT


def test_canonical_hash_is_order_independent_and_rejects_float() -> None:
    assert canonical_payload_hash({"a": 1, "b": 2}) == canonical_payload_hash({"b": 2, "a": 1})
    with pytest.raises(ValueError, match="Floats"):
        canonical_payload_hash({"amount": 10.5})
