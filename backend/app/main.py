from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app.api.buyer_requests import router as buyer_requests_router
from app.modules.procurement_agent.demo import DemoContainer, create_runtime_container


def create_app(container: DemoContainer | None = None) -> FastAPI:
    app = FastAPI(
        title="Canal Agente",
        version="0.1.0",
        description="Vertical slice do workflow comprador e sourcing do Dev 3.",
    )
    app.state.demo_container = container or create_runtime_container()

    @app.exception_handler(RequestValidationError)
    async def sanitized_request_validation_error(
        _request: Request,
        exc: RequestValidationError,
    ) -> JSONResponse:
        errors: list[dict[str, Any]] = []
        for error in exc.errors():
            sanitized = dict(error)
            sanitized.pop("input", None)
            sanitized.pop("ctx", None)
            errors.append(sanitized)
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            content={"detail": errors},
        )

    app.include_router(buyer_requests_router)

    project_root = Path(__file__).resolve().parents[2]
    frontend = project_root / "frontend"
    brand_assets = project_root / "web" / "public" / "assets"
    app.mount("/assets", StaticFiles(directory=frontend), name="assets")
    app.mount("/brand-assets", StaticFiles(directory=brand_assets), name="brand-assets")

    @app.get("/health", tags=["system"])
    async def health() -> dict[str, str]:
        return {"status": "ok", "mode": app.state.demo_container.mode}

    @app.get("/", include_in_schema=False)
    async def index() -> FileResponse:
        return FileResponse(frontend / "index.html")

    return app


app = create_app()


__all__ = ["app", "create_app"]
