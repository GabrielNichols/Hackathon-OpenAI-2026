from fastapi import APIRouter

from app.contracts import CONTRACT_VERSION

router = APIRouter(prefix="/core", tags=["core"])


@router.get("/health")
async def core_health() -> dict[str, str]:
    return {"status": "ok", "contract_version": CONTRACT_VERSION}
