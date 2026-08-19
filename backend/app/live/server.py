"""Fail-closed FastAPI composition root for the real Dev 4 demo."""

from __future__ import annotations

from collections.abc import Callable
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from sqlalchemy import text
from starlette.middleware.trustedhost import TrustedHostMiddleware

from app.live.application import (
    DurableLiveProcurementFacade,
    DurableProcurementExecutionPort,
    LiveExecutionRuntime,
)
from app.live.auth import (
    ConfiguredActorAuthenticator,
    LiveAuthenticationError,
    LiveRole,
)
from app.live.config import LiveSettings
from app.live.database import (
    create_database_engine,
    create_schema,
    create_session_factory,
)
from app.live.facade import HumanActor
from app.live.protection import AesGcmStateProtector
from app.live.router import create_live_router
from app.live.security import CsrfProtector
from app.live.uow import SqlAlchemyExecutionUnitOfWorkFactory
from app.modules.messaging.gateway import GatewayError, GatewayMessageNotFound
from app.modules.rfq.dev3_adapter import Dev3RFQExecutionAdapter
from app.shared.errors import DomainError, ErrorCode


def create_live_app(
    settings: LiveSettings,
    *,
    create_schema_on_start: bool = True,
    supplier_name_resolver: Callable[[str], str] | None = None,
) -> FastAPI:
    """Build the only runtime intended for a real, non-simulated demo."""

    engine = create_database_engine(settings.database_url)
    if create_schema_on_start:
        create_schema(engine)
    sessions = create_session_factory(engine)
    uow_factory = SqlAlchemyExecutionUnitOfWorkFactory(
        sessions,
        snapshot_id=settings.tenant_id,
        state_protector=AesGcmStateProtector(settings.token_secret),
    )
    runtime = LiveExecutionRuntime(settings=settings, uow_factory=uow_factory)
    execution_port = DurableProcurementExecutionPort(runtime)
    dev3_execution_adapter = Dev3RFQExecutionAdapter(execution_port)
    facade = DurableLiveProcurementFacade(
        runtime,
        supplier_name_resolver=supplier_name_resolver,
    )
    actor_authenticator = ConfiguredActorAuthenticator(settings)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        yield
        engine.dispose()

    app = FastAPI(
        title="Canal Agente — execução real",
        version="0.1.0-live",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan,
    )
    app.add_middleware(
        TrustedHostMiddleware,
        allowed_hosts=[_host_from_public_url(settings.public_base_url)],
    )

    async def authenticate_approver(request: object) -> HumanActor:
        return _authenticate_request(
            request,
            authenticator=actor_authenticator,
            role=LiveRole.APPROVER,
        )

    async def authenticate_operator(request: object) -> HumanActor:
        return _authenticate_request(
            request,
            authenticator=actor_authenticator,
            role=LiveRole.OPERATOR,
        )

    app.include_router(
        create_live_router(
            facade=facade,
            authenticate_approver=authenticate_approver,
            authenticate_operator=authenticate_operator,
            csrf=CsrfProtector(settings.csrf_secret, clock=runtime.clock),
            require_https=not settings.allow_insecure_http,
        )
    )

    @app.get("/health/live", include_in_schema=False)
    async def live_health() -> dict[str, str]:
        return {"status": "live", "mode": "real"}

    @app.get("/health/ready", include_in_schema=False)
    async def ready_health() -> dict[str, str]:
        try:
            with sessions() as session:
                session.execute(text("SELECT 1"))
        except Exception as error:
            raise HTTPException(status_code=503, detail="database unavailable") from error
        return {"status": "ready", "database": "connected"}

    @app.exception_handler(DomainError)
    async def handle_domain_error(_request: Request, error: DomainError) -> JSONResponse:
        status = _domain_status(error.code)
        public_message = error.message
        if error.code in {ErrorCode.NOT_FOUND, ErrorCode.INVALID_RESPONSE_TOKEN}:
            public_message = "Recurso indisponível ou link inválido"
        return JSONResponse(
            status_code=status,
            content={"error": {"code": error.code, "message": public_message}},
            headers={"Cache-Control": "no-store"},
        )

    @app.exception_handler(GatewayMessageNotFound)
    async def handle_missing_gateway_message(
        _request: Request,
        _error: GatewayMessageNotFound,
    ) -> JSONResponse:
        return JSONResponse(
            status_code=404,
            content={
                "error": {
                    "code": "NOT_FOUND",
                    "message": "Recurso indisponível ou link inválido",
                }
            },
            headers={"Cache-Control": "no-store"},
        )

    @app.exception_handler(GatewayError)
    async def handle_gateway_error(
        _request: Request,
        _error: GatewayError,
    ) -> JSONResponse:
        return JSONResponse(
            status_code=409,
            content={
                "error": {
                    "code": "DELIVERY_STATE_CONFLICT",
                    "message": "A entrega não pode avançar neste estado",
                }
            },
            headers={"Cache-Control": "no-store"},
        )

    app.state.live_settings = settings
    app.state.live_runtime = runtime
    app.state.execution_port = execution_port
    app.state.dev3_execution_adapter = dev3_execution_adapter
    app.state.live_facade = facade
    app.state.database_engine = engine
    return app


def _authenticate_request(
    request: object,
    *,
    authenticator: ConfiguredActorAuthenticator,
    role: LiveRole,
) -> HumanActor:
    if not isinstance(request, Request):
        raise HTTPException(status_code=401, detail="authentication required")
    try:
        actor = authenticator.authenticate(
            request.headers.get("authorization"),
            required_role=role,
        )
    except LiveAuthenticationError:
        raise HTTPException(
            status_code=401,
            detail="authentication required",
            headers={"WWW-Authenticate": 'Basic realm="Canal Agente"'},
        ) from None
    return HumanActor(
        tenant_id=actor.tenant_id,
        user_id=actor.user_id,
        display_name=actor.user_id,
    )


def _domain_status(code: str) -> int:
    statuses = {
        ErrorCode.VALIDATION_ERROR: 422,
        ErrorCode.QUOTE_TOTAL_MISMATCH: 422,
        ErrorCode.QUOTE_EXPIRED: 409,
        ErrorCode.NOT_FOUND: 404,
        ErrorCode.INVALID_RESPONSE_TOKEN: 404,
        ErrorCode.POLICY_DENIED: 403,
        ErrorCode.STALE_VERSION: 409,
        ErrorCode.IDEMPOTENCY_CONFLICT: 409,
        ErrorCode.INVALID_STATE: 409,
        ErrorCode.DEPENDENCY_UNAVAILABLE: 503,
    }
    return statuses.get(code, 400)


def _host_from_public_url(public_base_url: str) -> str:
    from urllib.parse import urlsplit

    parsed = urlsplit(public_base_url)
    if not parsed.hostname:
        raise ValueError("public_base_url must contain a hostname")
    return parsed.hostname


__all__ = ["create_live_app"]
