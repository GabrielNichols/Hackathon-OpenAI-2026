from __future__ import annotations

import hashlib
from collections.abc import Mapping
from datetime import datetime
from typing import Any

from .models import (
    ExtractedFieldDTO,
    ProviderExtractedFieldDTO,
    SourceDocumentDTO,
    SupplierExtractionResultDTO,
    SupplierExtractionSchema,
)


class FakeSupplierExtractionProvider:
    """Deterministic provider for contract, integration, and demo-replay tests."""

    def __init__(
        self,
        *,
        fixtures: Mapping[str, Mapping[str, Mapping[str, Any]]],
        fixed_now: datetime,
    ) -> None:
        self._fixtures = fixtures
        self._fixed_now = fixed_now

    async def extract(
        self,
        document: SourceDocumentDTO,
        schema: SupplierExtractionSchema,
    ) -> SupplierExtractionResultDTO:
        del schema
        try:
            fixture = self._fixtures[document.document_id]
        except KeyError as error:
            raise KeyError(f"no fake extraction fixture for {document.document_id}") from error

        run_suffix = hashlib.sha256(document.document_id.encode()).hexdigest()[:16]
        run_id = f"ext_{run_suffix}"
        fields: list[ExtractedFieldDTO] = []
        for field_name, raw_field in fixture.items():
            provider_field = ProviderExtractedFieldDTO.model_validate(
                {"field_name": field_name, **raw_field}
            )
            fields.append(
                ExtractedFieldDTO(
                    **provider_field.model_dump(),
                    source_document_id=document.document_id,
                    extraction_run_id=run_id,
                    confirmed_by=None,
                    confirmed_at=None,
                    version=1,
                )
            )
        return SupplierExtractionResultDTO(
            extraction_run_id=run_id,
            document_id=document.document_id,
            extracted_at=self._fixed_now,
            fields=fields,
        )

