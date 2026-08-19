from datetime import UTC, datetime
from typing import Any

import pytest

from app.modules.suppliers.extraction.models import (
    ExtractionFieldStatus,
    SourceDocumentDTO,
    SupplierExtractionSchema,
)
from app.modules.suppliers.extraction.providers import (
    LLMSupplierExtractionProvider,
    StructuredSupplierExtractionDTO,
)

NOW = datetime(2026, 8, 19, 15, tzinfo=UTC)


class FixedClock:
    def now(self) -> datetime:
        return NOW


class StructuredClientStub:
    def __init__(self, response: dict[str, Any]) -> None:
        self.response = response
        self.response_model: type[StructuredSupplierExtractionDTO] | None = None

    async def extract_structured(
        self,
        *,
        document: SourceDocumentDTO,
        schema: SupplierExtractionSchema,
        response_model: type[StructuredSupplierExtractionDTO],
    ) -> Any:
        del document, schema
        self.response_model = response_model
        return self.response


def source_document() -> SourceDocumentDTO:
    return SourceDocumentDTO(
        document_id="doc_llm",
        supplier_id="sup_1",
        tenant_id="org_1",
        original_filename="menu.pdf",
        sanitized_filename="menu.pdf",
        media_type="application/pdf",
        size_bytes=100,
        sha256="a" * 64,
        blob_id="blob_1",
        created_at=NOW,
    )


@pytest.mark.asyncio
async def test_llm_provider_uses_strict_output_and_never_confirms() -> None:
    client = StructuredClientStub(
        {
            "fields": [
                {
                    "field_name": "trade_name",
                    "value": "Alpha",
                    "normalized_value": "Alpha",
                    "status": "extracted",
                    "confidence": 0.95,
                    "source_page": 1,
                    "source_excerpt": "Alpha",
                },
                {
                    "field_name": "minimum_people",
                    "value": "30 pessoas",
                    "normalized_value": 30,
                    "status": "extracted",
                    "confidence": 0.4,
                    "source_page": 1,
                    "source_excerpt": "mínimo de 30 pessoas",
                },
                {
                    "field_name": "invoice_available",
                    "value": True,
                    "normalized_value": True,
                    "status": "extracted",
                    "confidence": 0.99,
                },
            ]
        }
    )
    provider = LLMSupplierExtractionProvider(client=client, clock=FixedClock())

    result = await provider.extract(source_document(), SupplierExtractionSchema.canonical())

    fields = {field.field_name: field for field in result.fields}
    assert client.response_model is StructuredSupplierExtractionDTO
    assert fields["trade_name"].status is ExtractionFieldStatus.EXTRACTED
    assert fields["minimum_people"].status is ExtractionFieldStatus.NEEDS_REVIEW
    assert fields["invoice_available"].status is ExtractionFieldStatus.NOT_FOUND
    assert fields["invoice_available"].value is None
    assert all(
        field.status not in {ExtractionFieldStatus.CONFIRMED, ExtractionFieldStatus.CORRECTED}
        for field in result.fields
    )


@pytest.mark.asyncio
async def test_llm_provider_rejects_model_claim_of_confirmation() -> None:
    client = StructuredClientStub(
        {
            "fields": [
                {
                    "field_name": "invoice_available",
                    "value": True,
                    "normalized_value": True,
                    "status": "confirmed",
                    "confidence": 0.99,
                    "source_page": 1,
                }
            ]
        }
    )
    provider = LLMSupplierExtractionProvider(client=client, clock=FixedClock())

    with pytest.raises(ValueError, match="cannot confirm or correct"):
        await provider.extract(source_document(), SupplierExtractionSchema.canonical())
