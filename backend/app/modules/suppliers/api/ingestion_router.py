from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, ConfigDict, ValidationError
from starlette.datastructures import UploadFile

from app.modules.suppliers.application.ingestion import (
    IngestionError,
    MaterialEmptyError,
    MaterialNotFoundError,
    MaterialTooLargeError,
    MaterialValidationError,
    SupplierMaterialIngestionService,
)

TenantResolver = Callable[..., str | Awaitable[str]]


class TextMaterialRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str
    filename: str = "whatsapp.txt"


class MaterialUploadResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    document_id: str
    supplier_id: str
    media_type: str
    sanitized_filename: str
    size_bytes: int
    sha256: str
    status: str
    blob_reused: bool


def _error_response(error: IngestionError) -> JSONResponse:
    if isinstance(error, MaterialTooLargeError):
        status_code = 413
    elif isinstance(error, MaterialNotFoundError):
        status_code = 404
    elif isinstance(error, MaterialEmptyError):
        status_code = 422
    elif isinstance(error, MaterialValidationError):
        status_code = 415
    else:
        status_code = 409
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


async def _read_upload(upload: UploadFile, *, maximum: int) -> bytes:
    chunks: list[bytes] = []
    size = 0
    while chunk := await upload.read(64 * 1024):
        size += len(chunk)
        if size > maximum:
            raise MaterialTooLargeError("material exceeds configured size limit")
        chunks.append(chunk)
    return b"".join(chunks)


async def _read_request_body(request: Request, *, maximum: int) -> bytes:
    chunks: list[bytes] = []
    size = 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > maximum:
            raise MaterialTooLargeError("material exceeds configured size limit")
        chunks.append(chunk)
    return b"".join(chunks)


def _response_payload(result: Any) -> MaterialUploadResponse:
    document = result.document
    return MaterialUploadResponse(
        document_id=document.document_id,
        supplier_id=document.supplier_id,
        media_type=document.media_type,
        sanitized_filename=document.sanitized_filename,
        size_bytes=document.size_bytes,
        sha256=document.sha256,
        status=document.status.value,
        blob_reused=result.blob_reused,
    )


def create_ingestion_router(
    *,
    service: SupplierMaterialIngestionService,
    resolve_tenant: TenantResolver,
) -> APIRouter:
    """Build the feature router without touching the application router."""

    router = APIRouter(prefix="/api/v1", tags=["supplier-materials"])

    @router.post(
        "/suppliers/{supplier_id}/materials",
        response_model=MaterialUploadResponse,
        status_code=201,
    )
    async def upload_material(
        supplier_id: str,
        request: Request,
        tenant_id: str = Depends(resolve_tenant),  # noqa: B008
    ) -> MaterialUploadResponse | JSONResponse:
        content_type = request.headers.get("content-type", "").casefold()
        try:
            if content_type.startswith("multipart/form-data"):
                form = await request.form()
                upload = form.get("file")
                text = form.get("text")
                if isinstance(upload, UploadFile) and text is None:
                    content = await _read_upload(upload, maximum=service.max_size_bytes)
                    result = await service.ingest_file(
                        tenant_id=tenant_id,
                        supplier_id=supplier_id,
                        original_filename=upload.filename or "",
                        declared_media_type=upload.content_type or "",
                        content=content,
                    )
                elif isinstance(text, str) and upload is None:
                    filename = form.get("filename")
                    result = await service.ingest_text(
                        tenant_id=tenant_id,
                        supplier_id=supplier_id,
                        text=text,
                        original_filename=(
                            filename if isinstance(filename, str) else "whatsapp.txt"
                        ),
                    )
                else:
                    raise MaterialValidationError(
                        "provide exactly one file or copied text material"
                    )
            elif content_type.startswith("application/json"):
                body = await _read_request_body(request, maximum=service.max_size_bytes)
                try:
                    payload = TextMaterialRequest.model_validate(json.loads(body))
                except (json.JSONDecodeError, UnicodeDecodeError, ValidationError) as error:
                    raise MaterialValidationError("invalid text material request") from error
                result = await service.ingest_text(
                    tenant_id=tenant_id,
                    supplier_id=supplier_id,
                    text=payload.text,
                    original_filename=payload.filename,
                )
            elif content_type.startswith("text/plain"):
                body = await _read_request_body(request, maximum=service.max_size_bytes)
                try:
                    text = body.decode("utf-8")
                except UnicodeDecodeError as error:
                    raise MaterialValidationError("text material must be UTF-8") from error
                result = await service.ingest_text(
                    tenant_id=tenant_id,
                    supplier_id=supplier_id,
                    text=text,
                    original_filename=request.headers.get("x-filename", "whatsapp.txt"),
                )
            else:
                raise MaterialValidationError("unsupported request content type")
        except IngestionError as error:
            return _error_response(error)
        return _response_payload(result)

    @router.get("/suppliers/{supplier_id}/materials/{document_id}")
    async def download_material(
        supplier_id: str,
        document_id: str,
        tenant_id: str = Depends(resolve_tenant),  # noqa: B008
    ) -> Response:
        try:
            document, content = await service.get_material(
                tenant_id=tenant_id,
                supplier_id=supplier_id,
                document_id=document_id,
            )
        except IngestionError as error:
            return _error_response(error)
        ascii_filename = document.sanitized_filename.encode("ascii", "ignore").decode()
        if not ascii_filename:
            ascii_filename = "material"
        return Response(
            content=content,
            media_type=document.media_type,
            headers={
                "Content-Disposition": f'attachment; filename="{ascii_filename}"',
                "ETag": f'"{document.sha256}"',
                "X-Document-Status": document.status.value,
            },
        )

    return router
