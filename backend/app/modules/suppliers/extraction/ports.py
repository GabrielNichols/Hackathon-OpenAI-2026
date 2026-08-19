from typing import Protocol

from .models import SourceDocumentDTO, SupplierExtractionResultDTO, SupplierExtractionSchema


class SupplierExtractionPort(Protocol):
    async def extract(
        self,
        document: SourceDocumentDTO,
        schema: SupplierExtractionSchema,
    ) -> SupplierExtractionResultDTO: ...

