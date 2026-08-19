from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request, Response, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from ..application.core_compat import ReviewTokenError, SupplierLifecycleStatus
from ..application.review import (
    FieldReviewRevision,
    ReviewError,
    ReviewIncompleteError,
    SupplierReviewService,
    SupplierReviewSession,
    SupplierReviewSubmissionResult,
)


class ConfirmFieldRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_version: int = Field(ge=1)


class CorrectFieldRequest(ConfirmFieldRequest):
    value: Any
    normalized_value: Any


class SupplierReviewViewDTO(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    review_id: str
    supplier_id: str
    status: SupplierLifecycleStatus
    version: int
    missing_required_fields: list[str]
    fields: list[FieldReviewRevision]

    @classmethod
    def from_session(cls, session: SupplierReviewSession) -> SupplierReviewViewDTO:
        return cls(
            review_id=session.review_id,
            supplier_id=session.supplier_id,
            status=session.projected_status,
            version=session.version,
            missing_required_fields=list(session.missing_required_fields()),
            fields=[session.current_field(field_name) for field_name in session.revisions],
        )


def get_review_service(request: Request) -> SupplierReviewService:
    service = getattr(request.app.state, "supplier_review_service", None)
    if not isinstance(service, SupplierReviewService):
        raise RuntimeError("supplier review service is not configured")
    return service


ReviewServiceDependency = Annotated[SupplierReviewService, Depends(get_review_service)]

router = APIRouter(prefix="/api/v1/supplier-review", tags=["supplier-review"])


@router.get("/{token}", response_model=SupplierReviewViewDTO)
async def get_supplier_review(
    token: str,
    service: ReviewServiceDependency,
) -> SupplierReviewViewDTO | Response:
    try:
        session = await service.get_review(token)
        return SupplierReviewViewDTO.from_session(session)
    except (ReviewTokenError, ReviewError) as error:
        return _error_response(error)


@router.post("/{token}/fields/{field_name}/confirm", response_model=FieldReviewRevision)
async def confirm_supplier_field(
    token: str,
    field_name: str,
    payload: ConfirmFieldRequest,
    service: ReviewServiceDependency,
) -> FieldReviewRevision | Response:
    try:
        return await service.confirm_field(
            token,
            field_name,
            expected_version=payload.expected_version,
        )
    except (ReviewTokenError, ReviewError) as error:
        return _error_response(error)


@router.post("/{token}/fields/{field_name}/correct", response_model=FieldReviewRevision)
async def correct_supplier_field(
    token: str,
    field_name: str,
    payload: CorrectFieldRequest,
    service: ReviewServiceDependency,
) -> FieldReviewRevision | Response:
    try:
        return await service.correct_field(
            token,
            field_name,
            value=payload.value,
            normalized_value=payload.normalized_value,
            expected_version=payload.expected_version,
        )
    except (ReviewTokenError, ReviewError) as error:
        return _error_response(error)


@router.post("/{token}/fields/{field_name}/not-applicable", response_model=FieldReviewRevision)
async def mark_supplier_field_not_applicable(
    token: str,
    field_name: str,
    payload: ConfirmFieldRequest,
    service: ReviewServiceDependency,
) -> FieldReviewRevision | Response:
    try:
        return await service.mark_not_applicable(
            token,
            field_name,
            expected_version=payload.expected_version,
        )
    except (ReviewTokenError, ReviewError) as error:
        return _error_response(error)


@router.post("/{token}/submit", response_model=SupplierReviewSubmissionResult)
async def submit_supplier_review(
    token: str,
    service: ReviewServiceDependency,
) -> SupplierReviewSubmissionResult | Response:
    try:
        return await service.submit(token)
    except (ReviewTokenError, ReviewError) as error:
        return _error_response(error)


def _error_response(error: ReviewTokenError | ReviewError) -> JSONResponse:
    details: dict[str, Any] = {}
    if isinstance(error, ReviewIncompleteError):
        details["missing_fields"] = list(error.missing_fields)
    status_code = {
        "LINK_EXPIRED": status.HTTP_410_GONE,
        "LINK_INVALID": status.HTTP_403_FORBIDDEN,
        "NOT_FOUND": status.HTTP_404_NOT_FOUND,
        "VALIDATION_ERROR": status.HTTP_422_UNPROCESSABLE_ENTITY,
        "CONFLICT": status.HTTP_409_CONFLICT,
        "OPTIMISTIC_LOCK_CONFLICT": status.HTTP_409_CONFLICT,
        "INVALID_STATE_TRANSITION": status.HTTP_409_CONFLICT,
        "REVIEW_INCOMPLETE": status.HTTP_409_CONFLICT,
    }.get(error.code, status.HTTP_400_BAD_REQUEST)
    return JSONResponse(
        status_code=status_code,
        content={
            "error": {
                "code": error.code,
                "message": str(error),
                "details": details,
            }
        },
    )
