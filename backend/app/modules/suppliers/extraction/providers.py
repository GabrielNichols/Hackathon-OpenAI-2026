from __future__ import annotations

import hashlib
from collections.abc import Mapping
from datetime import datetime
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict

from .models import (
    CRITICAL_EXTRACTION_FIELDS,
    ExtractedFieldDTO,
    ExtractionFieldStatus,
    ProviderExtractedFieldDTO,
    SourceDocumentDTO,
    SupplierExtractionResultDTO,
    SupplierExtractionSchema,
)
from .provenance import finalize_extraction_result


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


class StructuredSupplierExtractionDTO(BaseModel):
    """Only schema-validated, model-owned decisions may cross the client boundary."""

    model_config = ConfigDict(extra="forbid")

    fields: list[ProviderExtractedFieldDTO]


class StructuredExtractionClient(Protocol):
    async def extract_structured(
        self,
        *,
        document: SourceDocumentDTO,
        schema: SupplierExtractionSchema,
        response_model: type[StructuredSupplierExtractionDTO],
    ) -> Any: ...


class ExtractionClock(Protocol):
    def now(self) -> datetime: ...


class LLMSupplierExtractionProvider:
    """Vendor-neutral adapter; the injected client must return the strict response model."""

    def __init__(self, *, client: StructuredExtractionClient, clock: ExtractionClock) -> None:
        self._client = client
        self._clock = clock

    async def extract(
        self,
        document: SourceDocumentDTO,
        schema: SupplierExtractionSchema,
    ) -> SupplierExtractionResultDTO:
        raw_response = await self._client.extract_structured(
            document=document,
            schema=schema,
            response_model=StructuredSupplierExtractionDTO,
        )
        response = StructuredSupplierExtractionDTO.model_validate(raw_response)
        allowed_fields = {field.name for field in schema.fields}
        run_suffix = hashlib.sha256(
            f"{document.document_id}:{document.sha256}".encode()
        ).hexdigest()[:16]
        run_id = f"ext_llm_{run_suffix}"
        fields: list[ExtractedFieldDTO] = []
        for candidate in response.fields:
            if candidate.field_name not in allowed_fields:
                raise ValueError(
                    f"structured provider returned unknown field: {candidate.field_name}"
                )
            provider_field = self._require_evidence_or_not_found(candidate)
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
        result = SupplierExtractionResultDTO(
            extraction_run_id=run_id,
            document_id=document.document_id,
            extracted_at=self._clock.now(),
            fields=fields,
        )
        return finalize_extraction_result(result)

    @staticmethod
    def _require_evidence_or_not_found(
        field: ProviderExtractedFieldDTO,
    ) -> ProviderExtractedFieldDTO:
        has_evidence = any(
            value is not None
            for value in (
                field.source_page,
                field.source_sheet,
                field.source_cell_range,
                field.source_excerpt,
            )
        )
        if field.field_name in CRITICAL_EXTRACTION_FIELDS and not has_evidence:
            return ProviderExtractedFieldDTO(
                field_name=field.field_name,
                value=None,
                normalized_value=None,
                status=ExtractionFieldStatus.NOT_FOUND,
                confidence=None,
            )
        return field
