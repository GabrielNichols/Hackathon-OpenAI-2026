from __future__ import annotations

import httpx
import pytest
from app.live.config import LiveSettings
from app.live.server import create_live_app
from app.modules.rfq.dev3_adapter import Dev3RFQExecutionAdapter

COMPLETE_MESSAGE = (
    "Preciso de um coffee break para 80 pessoas em 21/08/2026, entregue às 8h30 "
    "na Vila Olímpia, São Paulo. Serão 12 vegetarianos, 4 veganos e 3 sem glúten. "
    "Orçamento máximo de R$ 4.500, com nota fiscal obrigatória e sem plásticos "
    "descartáveis. Quero 3 cotações, respostas até 20/08/2026 às 18h. "
    "Aprovador: approver_demo."
)


def _settings(database_path: str) -> LiveSettings:
    return LiveSettings(
        database_url=f"sqlite:///{database_path}",
        public_base_url="https://integrated.canal-agente.test",
        token_secret="integrated-token-secret-with-at-least-32-bytes",
        csrf_secret="integrated-csrf-secret-different-and-at-least-32-bytes",
        pii_hash_secret="integrated-pii-secret-different-and-at-least-32-bytes",
        operator_user_id="buyer_operator",
        operator_access_token="integrated-operator-access-token-123456",
        approver_user_id="approver_demo",
        approver_access_token="integrated-approver-access-token-123456",
        tenant_id="tenant-integrated",
        allow_test_database=True,
    )


@pytest.mark.asyncio
async def test_authenticated_dev3_workflow_creates_durable_dev4_round(tmp_path) -> None:
    settings = _settings((tmp_path / "integrated.sqlite3").as_posix())
    app = create_live_app(settings)
    transport = httpx.ASGITransport(app=app)
    endpoint = "/api/v1/procurement-requests/messages"

    async with httpx.AsyncClient(
        transport=transport,
        base_url=settings.public_base_url,
    ) as client:
        unauthorized = await client.post(
            endpoint,
            json={"message": COMPLETE_MESSAGE},
            headers={"Idempotency-Key": "integrated-request-create"},
        )
        assert unauthorized.status_code == 401

        headers = {
            "Authorization": f"Bearer {settings.operator_access_token}",
            "Idempotency-Key": "integrated-request-create",
        }
        created = await client.post(
            endpoint,
            json={"message": COMPLETE_MESSAGE},
            headers=headers,
        )
        assert created.status_code == 200
        request_view = created.json()
        assert request_view["status"] == "READY"
        assert request_view["mode"] == "live_integrated_local_interpreter"

        confirmed = await client.post(
            f"/api/v1/procurement-requests/{request_view['request_id']}/plan/confirm",
            json={},
            headers={"Authorization": f"Bearer {settings.operator_access_token}"},
        )
        assert confirmed.status_code == 200
        sourced = confirmed.json()
        assert sourced["rfq_round_id"] is not None
        assert sourced["stop_reason"] == "AWAITING_EXTERNAL_RESPONSE"

        retry = await client.post(
            f"/api/v1/procurement-requests/{request_view['request_id']}/plan/confirm",
            json={},
            headers={"Authorization": f"Bearer {settings.operator_access_token}"},
        )
        assert retry.status_code == 200
        assert retry.json()["rfq_round_id"] == sourced["rfq_round_id"]

    assert isinstance(app.state.dev3_execution_adapter, Dev3RFQExecutionAdapter)
    assert app.state.agent_container.rfq is app.state.dev3_execution_adapter
    with app.state.live_runtime.uow_factory() as uow:
        assert uow.store is not None
        round_record = uow.store.rounds[sourced["rfq_round_id"]]
        assert round_record["tenant_id"] == settings.tenant_id
        assert (
            round_record["dto"].procurement_request_id
            == request_view["request_id"]
        )
        assert len(round_record["recipient_ids"]) >= 2
        recipients = [
            uow.store.recipients[recipient_id]
            for recipient_id in round_record["recipient_ids"]
        ]
        assert all(item["status"] == "SENT_TO_GATEWAY" for item in recipients)
        assert all(item["external_id"] is not None for item in recipients)
    app.state.database_engine.dispose()
