from __future__ import annotations

from collections.abc import Awaitable, Callable

from fastapi import APIRouter, Depends, Response, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from ..application.review_profile import (
    CreateSupplierProfileCommand,
    SupplierProfileDTO,
    SupplierProfileError,
    SupplierProfileService,
)

TenantResolver = Callable[..., str | Awaitable[str]]


class CreateSupplierRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    legal_name: str = Field(min_length=1)
    trade_name: str = Field(min_length=1)
    cnpj: str | None = None
    contact_name: str = Field(min_length=1)
    contact_email: str = Field(min_length=1)
    contact_phone: str = Field(min_length=1)


def create_supplier_profile_router(
    *,
    service: SupplierProfileService,
    resolve_tenant: TenantResolver,
) -> APIRouter:
    """Build create/get routes without touching the central application router."""

    router = APIRouter(prefix="/api/v1/suppliers", tags=["suppliers"])

    @router.post("", response_model=SupplierProfileDTO, status_code=status.HTTP_201_CREATED)
    async def create_supplier_profile(
        payload: CreateSupplierRequest,
        tenant_id: str = Depends(resolve_tenant),
    ) -> SupplierProfileDTO | Response:
        try:
            return await service.create(
                CreateSupplierProfileCommand(
                    organization_id=tenant_id,
                    **payload.model_dump(),
                )
            )
        except SupplierProfileError as error:
            return _error_response(error)

    @router.get("/{supplier_id}", response_model=SupplierProfileDTO)
    async def get_supplier_profile(
        supplier_id: str,
        tenant_id: str = Depends(resolve_tenant),
    ) -> SupplierProfileDTO | Response:
        try:
            return await service.get(tenant_id=tenant_id, supplier_id=supplier_id)
        except SupplierProfileError as error:
            return _error_response(error)

    return router


def _error_response(error: SupplierProfileError) -> JSONResponse:
    status_code = {
        "NOT_FOUND": status.HTTP_404_NOT_FOUND,
        "CONFLICT": status.HTTP_409_CONFLICT,
    }.get(error.code, status.HTTP_400_BAD_REQUEST)
    return JSONResponse(
        status_code=status_code,
        content={
            "error": {
                "code": error.code,
                "message": str(error),
                "details": {},
            }
        },
    )
