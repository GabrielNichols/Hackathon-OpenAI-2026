import pytest
from app.main import create_app
from app.modules.procurement_agent.demo import create_demo_container
from app.modules.procurement_requests import MAX_BUYER_MESSAGE_CHARS
from httpx import ASGITransport, AsyncClient

from .procurement_agent.test_workflow import COMPLETE_MESSAGE


async def test_health_and_buyer_workflow_api() -> None:
    container = create_demo_container()
    async with AsyncClient(
        transport=ASGITransport(app=create_app(container)),
        base_url="http://test",
    ) as client:
        health = await client.get("/health")
        assert health.status_code == 200
        assert health.json() == {"status": "ok", "mode": "demo_fake"}

        index = await client.get("/")
        javascript = await client.get("/assets/app.js")
        assert index.status_code == 200
        assert "Canal Agente" in index.text
        assert javascript.status_code == 200
        assert "Idempotency-Key" in javascript.text

        created = await client.post(
            "/api/v1/procurement-requests/messages",
            json={"message": COMPLETE_MESSAGE},
            headers={"Idempotency-Key": "create-request-demo"},
        )
        assert created.status_code == 200
        request_view = created.json()
        assert request_view["status"] == "READY"
        assert request_view["stop_reason"] == "AWAITING_PLAN_CONFIRMATION"

        confirmed = await client.post(
            f"/api/v1/procurement-requests/{request_view['request_id']}/plan/confirm",
            json={},
        )
        assert confirmed.status_code == 200
        sourced_view = confirmed.json()
        assert sourced_view["status"] == "SOURCING"
        assert sourced_view["rfq_round_id"].startswith("rfq_")

        fetched = await client.get(f"/api/v1/procurement-requests/{request_view['request_id']}")
        assert fetched.status_code == 200
        assert fetched.json()["rfq_round_id"] == sourced_view["rfq_round_id"]


async def test_unknown_request_returns_404() -> None:
    async with AsyncClient(
        transport=ASGITransport(app=create_app(create_demo_container())),
        base_url="http://test",
    ) as client:
        response = await client.get("/api/v1/procurement-requests/pr_missing")

        assert response.status_code == 404


async def test_blank_message_returns_validation_error_instead_of_500() -> None:
    async with AsyncClient(
        transport=ASGITransport(app=create_app(create_demo_container())),
        base_url="http://test",
    ) as client:
        response = await client.post(
            "/api/v1/procurement-requests/messages",
            json={"message": "   "},
            headers={"Idempotency-Key": "blank-message-demo"},
        )

        assert response.status_code == 422


async def test_buyer_message_accepts_exact_maximum_length() -> None:
    container = create_demo_container()
    message = "x" * MAX_BUYER_MESSAGE_CHARS
    async with AsyncClient(
        transport=ASGITransport(app=create_app(container)),
        base_url="http://test",
    ) as client:
        response = await client.post(
            "/api/v1/procurement-requests/messages",
            json={"message": message},
            headers={"Idempotency-Key": "maximum-message-length"},
        )

    assert response.status_code == 200
    assert response.json()["draft"]["description"] == message


async def test_oversized_message_is_rejected_before_audit_or_provider_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    container = create_demo_container()
    sentinel = "PRIVATE_BUYER_MESSAGE_SENTINEL"
    message = sentinel + "x" * (MAX_BUYER_MESSAGE_CHARS + 1 - len(sentinel))
    provider_calls = 0
    original_interpret = container.interpreter.interpret

    async def counted_interpret(*args, **kwargs):
        nonlocal provider_calls
        provider_calls += 1
        return await original_interpret(*args, **kwargs)

    monkeypatch.setattr(container.interpreter, "interpret", counted_interpret)
    async with AsyncClient(
        transport=ASGITransport(app=create_app(container)),
        base_url="http://test",
    ) as client:
        response = await client.post(
            "/api/v1/procurement-requests/messages",
            json={"message": message},
            headers={"Idempotency-Key": "oversized-message"},
        )

    body = response.json()
    assert response.status_code == 422
    assert sentinel not in response.text
    assert provider_calls == 0
    assert container.audit.events == ()
    assert all("input" not in error and "ctx" not in error for error in body["detail"])


async def test_domain_value_error_is_returned_as_stable_detail(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    container = create_demo_container()
    sentinel = "PRIVATE_DOMAIN_ERROR_SENTINEL"

    async def fail_with_private_detail(*args, **kwargs):
        raise ValueError(sentinel)

    monkeypatch.setattr(container.orchestrator, "receive_message", fail_with_private_detail)
    async with AsyncClient(
        transport=ASGITransport(app=create_app(container)),
        base_url="http://test",
    ) as client:
        response = await client.post(
            "/api/v1/procurement-requests/messages",
            json={"message": "Coffee break para dez pessoas."},
            headers={"Idempotency-Key": "stable-domain-error"},
        )

    assert response.status_code == 422
    assert response.json() == {"detail": "INVALID_BUYER_MESSAGE"}
    assert sentinel not in response.text


async def test_request_creation_requires_and_honors_idempotency_key() -> None:
    container = create_demo_container()
    async with AsyncClient(
        transport=ASGITransport(app=create_app(container)),
        base_url="http://test",
    ) as client:
        missing_key = await client.post(
            "/api/v1/procurement-requests/messages",
            json={"message": COMPLETE_MESSAGE},
        )
        first = await client.post(
            "/api/v1/procurement-requests/messages",
            json={"message": COMPLETE_MESSAGE},
            headers={"Idempotency-Key": "same-create-key"},
        )
        retry = await client.post(
            "/api/v1/procurement-requests/messages",
            json={"message": COMPLETE_MESSAGE},
            headers={"Idempotency-Key": "same-create-key"},
        )
        conflict = await client.post(
            "/api/v1/procurement-requests/messages",
            json={"message": COMPLETE_MESSAGE + " Alterado."},
            headers={"Idempotency-Key": "same-create-key"},
        )

        assert missing_key.status_code == 422
        assert first.status_code == 200
        assert retry.status_code == 200
        assert retry.json()["request_id"] == first.json()["request_id"]
        assert conflict.status_code == 409
