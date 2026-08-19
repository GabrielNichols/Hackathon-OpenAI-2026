from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ExtractionFieldStatus(StrEnum):
    EXTRACTED = "extracted"
    NOT_FOUND = "not_found"
    NEEDS_REVIEW = "needs_review"
    CONFIRMED = "confirmed"
    CORRECTED = "corrected"


class DocumentProcessingStatus(StrEnum):
    RECEIVED = "RECEIVED"
    VALIDATED = "VALIDATED"
    STORED = "STORED"
    EXTRACTION_QUEUED = "EXTRACTION_QUEUED"
    EXTRACTING = "EXTRACTING"
    EXTRACTED = "EXTRACTED"
    EXTRACTION_FAILED = "EXTRACTION_FAILED"
    AWAITING_SUPPLIER_REVIEW = "AWAITING_SUPPLIER_REVIEW"


CRITICAL_EXTRACTION_FIELDS: tuple[str, ...] = (
    "trade_name",
    "categories",
    "service_cities",
    "minimum_people",
    "maximum_people",
    "lead_time_hours",
    "invoice_available",
    "pricing_model",
    "vegetarian_supported",
    "vegan_supported",
    "gluten_free_supported",
    "cross_contamination_warning",
)


class ExtractionFieldDefinition(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str
    critical: bool = False
    description: str


class SupplierExtractionSchema(BaseModel):
    model_config = ConfigDict(frozen=True)

    version: str = "supplier-extraction-v1"
    fields: tuple[ExtractionFieldDefinition, ...]

    @classmethod
    def canonical(cls) -> Self:
        descriptions = {
            "trade_name": "Supplier commercial display name",
            "categories": "Corporate food service categories",
            "service_cities": "Cities served for delivery",
            "minimum_people": "Minimum number of people served",
            "maximum_people": "Approximate maximum number of people served",
            "lead_time_hours": "Minimum advance notice in hours",
            "invoice_available": "Whether an invoice can be issued",
            "pricing_model": "Per person, fixed package, or custom quote",
            "vegetarian_supported": "Whether vegetarian options are supported",
            "vegan_supported": "Whether vegan options are supported",
            "gluten_free_supported": "Whether gluten-free options are supported",
            "cross_contamination_warning": (
                "Explicit warning about gluten cross-contamination; never infer false"
            ),
        }
        return cls(
            fields=tuple(
                ExtractionFieldDefinition(
                    name=name,
                    critical=True,
                    description=descriptions[name],
                )
                for name in CRITICAL_EXTRACTION_FIELDS
            )
        )


class ExtractedFieldDTO(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    field_name: str
    value: Any | None
    normalized_value: Any | None
    status: ExtractionFieldStatus
    confidence: float | None = Field(default=None, ge=0, le=1)
    source_document_id: str
    source_page: int | None = Field(default=None, ge=1)
    source_sheet: str | None = None
    source_cell_range: str | None = None
    source_excerpt: str | None = None
    extraction_run_id: str
    confirmed_by: str | None = None
    confirmed_at: datetime | None = None
    version: int = Field(ge=1)

    @model_validator(mode="after")
    def enforce_provenance_and_confirmation(self) -> Self:
        if self.status is ExtractionFieldStatus.NOT_FOUND:
            if self.value is not None or self.normalized_value is not None:
                raise ValueError("not_found fields cannot contain a value")
            if self.confidence is not None:
                raise ValueError("not_found fields cannot contain confidence")

        supplier_decision = self.status in {
            ExtractionFieldStatus.CONFIRMED,
            ExtractionFieldStatus.CORRECTED,
        }
        if supplier_decision and (self.confirmed_by is None or self.confirmed_at is None):
            raise ValueError("confirmed and corrected fields require actor and timestamp")
        if not supplier_decision and (
            self.confirmed_by is not None or self.confirmed_at is not None
        ):
            raise ValueError("model extraction cannot contain supplier confirmation metadata")
        return self


class ProviderExtractedFieldDTO(BaseModel):
    """Strict model-owned output before application-level provenance finalization."""

    model_config = ConfigDict(extra="forbid")

    field_name: str
    value: Any | None
    normalized_value: Any | None
    status: ExtractionFieldStatus = ExtractionFieldStatus.EXTRACTED
    confidence: float | None = Field(default=None, ge=0, le=1)
    source_page: int | None = Field(default=None, ge=1)
    source_sheet: str | None = None
    source_cell_range: str | None = None
    source_excerpt: str | None = None

    @model_validator(mode="after")
    def prevent_model_owned_confirmation(self) -> Self:
        if self.status not in {
            ExtractionFieldStatus.EXTRACTED,
            ExtractionFieldStatus.NOT_FOUND,
            ExtractionFieldStatus.NEEDS_REVIEW,
        }:
            raise ValueError("an extraction provider cannot confirm or correct a field")
        if self.status is ExtractionFieldStatus.NOT_FOUND:
            if self.value is not None or self.normalized_value is not None:
                raise ValueError("not_found fields cannot contain a value")
            if self.confidence is not None:
                raise ValueError("not_found fields cannot contain confidence")
        return self


class SupplierExtractionResultDTO(BaseModel):
    model_config = ConfigDict(extra="forbid")

    extraction_run_id: str
    document_id: str
    extracted_at: datetime
    fields: list[ExtractedFieldDTO]

    @model_validator(mode="after")
    def field_names_are_unique(self) -> Self:
        field_names = [field.field_name for field in self.fields]
        if len(field_names) != len(set(field_names)):
            raise ValueError("extraction result contains duplicate field names")
        return self


class SourceDocumentDTO(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    document_id: str
    supplier_id: str
    tenant_id: str
    original_filename: str
    sanitized_filename: str
    media_type: str
    size_bytes: int = Field(gt=0)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    blob_id: str
    created_at: datetime
    status: DocumentProcessingStatus = DocumentProcessingStatus.STORED

