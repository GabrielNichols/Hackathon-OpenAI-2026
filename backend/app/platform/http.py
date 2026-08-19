from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.contracts import ErrorCode, ErrorDetailDTO, ErrorEnvelopeDTO


class CoreServiceError(RuntimeError):
    def __init__(
        self,
        code: ErrorCode,
        message: str,
        *,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.details = details or {}


def install_core_error_handler(app: FastAPI) -> None:
    @app.exception_handler(CoreServiceError)
    async def handle_core_error(request: Request, error: CoreServiceError) -> JSONResponse:
        correlation_id = request.headers.get("x-correlation-id", "cor_unknown")
        envelope = ErrorEnvelopeDTO(
            error=ErrorDetailDTO(
                code=error.code,
                message=str(error),
                details=error.details,
                correlation_id=correlation_id,
            )
        )
        status = {
            ErrorCode.NOT_FOUND: 404,
            ErrorCode.CONFLICT: 409,
            ErrorCode.IDEMPOTENCY_CONFLICT: 409,
            ErrorCode.OPTIMISTIC_LOCK_CONFLICT: 409,
            ErrorCode.POLICY_DENIED: 403,
            ErrorCode.LINK_EXPIRED: 410,
            ErrorCode.LINK_INVALID: 400,
        }.get(error.code, 400)
        return JSONResponse(status_code=status, content=envelope.model_dump(mode="json"))
