from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import core_router
from app.contracts import ErrorCode
from app.platform.http import CoreServiceError, install_core_error_handler


def app_for_test() -> FastAPI:
    app = FastAPI()
    app.include_router(core_router)
    install_core_error_handler(app)

    @app.get("/failure")
    async def failure() -> None:
        raise CoreServiceError(ErrorCode.POLICY_DENIED, "blocked", details={"action": "award"})

    return app


def test_core_health_exports_contract_version() -> None:
    response = TestClient(app_for_test()).get("/core/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "contract_version": "0.1.0"}


def test_core_error_handler_uses_stable_envelope() -> None:
    response = TestClient(app_for_test()).get(
        "/failure", headers={"x-correlation-id": "cor_test"}
    )
    assert response.status_code == 403
    assert response.json() == {
        "error": {
            "code": "POLICY_DENIED",
            "message": "blocked",
            "details": {"action": "award"},
            "correlation_id": "cor_test",
        }
    }
