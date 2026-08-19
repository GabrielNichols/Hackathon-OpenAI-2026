from __future__ import annotations

from collections.abc import Awaitable, Callable

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict

from app.modules.suppliers.application.extraction_service import (
    ExtractionExecutionDTO,
    ExtractionQueueEmptyError,
    ExtractionResultNotFoundError,
    SupplierExtractionService,
)

TenantResolver = Callable[..., str | Awaitable[str]]


class ExtractionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    document_id: str | None = None


def _error(code: str, message: str, *, status_code: int) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={
            "error": {
                "code": code,
                "message": message,
                "details": {},
            }
        },
    )


def create_extraction_router(
    *,
    service: SupplierExtractionService,
    resolve_tenant: TenantResolver,
) -> APIRouter:
    router = APIRouter(prefix="/api/v1", tags=["supplier-extraction"])

    @router.post(
        "/suppliers/{supplier_id}/extractions",
        response_model=ExtractionExecutionDTO,
    )
    async def process_extraction(
        supplier_id: str,
        payload: ExtractionRequest | None = None,
        tenant_id: str = Depends(resolve_tenant),
    ) -> ExtractionExecutionDTO | JSONResponse:
        try:
            return await service.process_next(
                tenant_id=tenant_id,
                supplier_id=supplier_id,
                document_id=payload.document_id if payload is not None else None,
            )
        except ExtractionQueueEmptyError as error:
            return _error("EXTRACTION_NOT_QUEUED", str(error), status_code=409)

    @router.get(
        "/suppliers/{supplier_id}/extractions/{document_id}",
        response_model=ExtractionExecutionDTO,
    )
    async def extraction_status(
        supplier_id: str,
        document_id: str,
        tenant_id: str = Depends(resolve_tenant),
    ) -> ExtractionExecutionDTO | JSONResponse:
        try:
            return await service.get_status(
                tenant_id=tenant_id,
                supplier_id=supplier_id,
                document_id=document_id,
            )
        except ExtractionResultNotFoundError as error:
            return _error("EXTRACTION_NOT_FOUND", str(error), status_code=404)

    return router
