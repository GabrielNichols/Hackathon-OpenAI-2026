from __future__ import annotations

from typing import Protocol

from app.modules.sourcing.models import SupplierCandidateDTO, SupplierSearchCriteria


class SupplierDirectoryPort(Protocol):
    async def search(self, criteria: SupplierSearchCriteria) -> list[SupplierCandidateDTO]: ...

    async def get(self, supplier_id: str) -> SupplierCandidateDTO | None: ...


__all__ = ["SupplierDirectoryPort"]
