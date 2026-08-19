from datetime import UTC, datetime

import pytest

from app.modules.suppliers.extraction.models import (
    SourceDocumentDTO,
    SupplierExtractionSchema,
)
from app.modules.suppliers.extraction.ports import SupplierExtractionPort
from app.modules.suppliers.extraction.providers import FakeSupplierExtractionProvider


def source_document(document_id: str = "doc_1") -> SourceDocumentDTO:
    return SourceDocumentDTO(
        document_id=document_id,
        supplier_id="sup_1",
        tenant_id="org_1",
        original_filename="cardapio.pdf",
        sanitized_filename="cardapio.pdf",
        media_type="application/pdf",
        size_bytes=42,
        sha256="a" * 64,
        blob_id="blob_1",
        created_at=datetime(2026, 8, 19, 15, tzinfo=UTC),
    )


@pytest.mark.asyncio
async def test_fake_provider_implements_extraction_port() -> None:
    provider: SupplierExtractionPort = FakeSupplierExtractionProvider(
        fixtures={
            "doc_1": {
                "minimum_people": {
                    "value": "30 pessoas",
                    "normalized_value": 30,
                    "confidence": 0.99,
                    "source_page": 1,
                    "source_excerpt": "Mínimo de 30 pessoas",
                }
            }
        },
        fixed_now=datetime(2026, 8, 19, 15, tzinfo=UTC),
    )

    result = await provider.extract(source_document(), SupplierExtractionSchema.canonical())

    assert result.document_id == "doc_1"
    assert result.extraction_run_id.startswith("ext_")
    assert result.fields[0].status == "extracted"


@pytest.mark.asyncio
async def test_fake_provider_is_deterministic_for_same_document() -> None:
    provider = FakeSupplierExtractionProvider(
        fixtures={"doc_1": {}},
        fixed_now=datetime(2026, 8, 19, 15, tzinfo=UTC),
    )

    first = await provider.extract(source_document(), SupplierExtractionSchema.canonical())
    second = await provider.extract(source_document(), SupplierExtractionSchema.canonical())

    assert first == second


@pytest.mark.asyncio
async def test_fake_provider_does_not_invent_unconfigured_fields() -> None:
    provider = FakeSupplierExtractionProvider(
        fixtures={"doc_1": {}},
        fixed_now=datetime(2026, 8, 19, 15, tzinfo=UTC),
    )

    result = await provider.extract(source_document(), SupplierExtractionSchema.canonical())

    assert result.fields == []


@pytest.mark.asyncio
async def test_provider_failure_is_explicit() -> None:
    provider = FakeSupplierExtractionProvider(
        fixtures={},
        fixed_now=datetime(2026, 8, 19, 15, tzinfo=UTC),
    )

    with pytest.raises(KeyError, match="doc_missing"):
        await provider.extract(
            source_document(document_id="doc_missing"),
            SupplierExtractionSchema.canonical(),
        )
