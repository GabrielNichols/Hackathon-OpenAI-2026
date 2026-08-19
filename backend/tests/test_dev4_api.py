from __future__ import annotations

import httpx
import pytest

from backend.app.main import create_app
from backend.app.shared.errors import DomainError, ErrorCode


async def request(app, method: str, path: str) -> httpx.Response:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://testserver",
    ) as client:
        return await client.request(method, path)


@pytest.mark.asyncio
async def test_health_exposes_a_small_operational_contract():
    response = await request(create_app(), "GET", "/health")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "service": "canal-agente-dev4",
        "prototype": True,
    }


@pytest.mark.asyncio
async def test_demo_endpoint_runs_full_path_and_returns_auditable_timeline():
    response = await request(create_app(), "POST", "/api/v1/demo/run")

    assert response.status_code == 200
    result = response.json()
    assert result["mode"] == "FAKE_DEMO"
    assert result["simulated_external_actions"] is True
    assert result["status"] == "READY_FOR_CONTRACTING"
    assert result["ready_for_contracting"] is True
    assert result["delivery"]["confirmed_count"] == 2
    assert result["delivery"]["activation_criteria_met"] is True
    assert [quote["total_cents"] for quote in result["quotes"]] == [420_000, 435_000]
    assert result["comparison"]["candidates"][0]["supplier_id"] == "supplier_alpha"
    assert result["approval"]["status"] == "APPROVED"
    assert result["award"]["status"] == "ACCEPTED"
    assert result["award"]["reservation_status"] == "CONFIRMED"

    event_types = [event["event_type"] for event in result["timeline"]]
    assert event_types[0] == "RFQ_ROUND_CREATED"
    assert "QUOTE_COMPARISON_CREATED" in event_types
    assert "APPROVAL_GRANTED" in event_types
    assert "AWARD_DELIVERY_CONFIRMED" in event_types
    assert "SUPPLIER_ACCEPTED_AWARD" in event_types
    assert event_types[-1] == "PROCUREMENT_READY_FOR_CONTRACTING"
    assert "response_token" not in response.text


@pytest.mark.asyncio
async def test_domain_errors_have_stable_safe_http_shape():
    async def blocked_demo():
        raise DomainError(
            ErrorCode.POLICY_DENIED,
            "only a human can approve",
            details={"actor_type": "agent"},
        )

    response = await request(
        create_app(demo_runner=blocked_demo),
        "POST",
        "/api/v1/demo/run",
    )

    assert response.status_code == 403
    assert response.json() == {
        "error": {
            "code": "POLICY_DENIED",
            "message": "only a human can approve",
            "retryable": False,
            "details": {"actor_type": "agent"},
        }
    }
