from __future__ import annotations

from .models import (
    CRITICAL_EXTRACTION_FIELDS,
    ExtractedFieldDTO,
    ExtractionFieldStatus,
    SupplierExtractionResultDTO,
)


def finalize_extraction_result(
    result: SupplierExtractionResultDTO,
    *,
    confidence_threshold: float = 0.8,
) -> SupplierExtractionResultDTO:
    """Apply deterministic safety rules after provider schema validation."""

    fields_by_name: dict[str, ExtractedFieldDTO] = {}
    for field in result.fields:
        if (
            field.status is ExtractionFieldStatus.EXTRACTED
            and field.confidence is not None
            and field.confidence < confidence_threshold
        ):
            field = field.model_copy(update={"status": ExtractionFieldStatus.NEEDS_REVIEW})
        fields_by_name[field.field_name] = field

    for field_name in CRITICAL_EXTRACTION_FIELDS:
        if field_name not in fields_by_name:
            fields_by_name[field_name] = ExtractedFieldDTO(
                field_name=field_name,
                value=None,
                normalized_value=None,
                status=ExtractionFieldStatus.NOT_FOUND,
                confidence=None,
                source_document_id=result.document_id,
                source_page=None,
                source_sheet=None,
                source_cell_range=None,
                source_excerpt=None,
                extraction_run_id=result.extraction_run_id,
                confirmed_by=None,
                confirmed_at=None,
                version=1,
            )

    ordered_names = [*CRITICAL_EXTRACTION_FIELDS]
    ordered_names.extend(name for name in fields_by_name if name not in CRITICAL_EXTRACTION_FIELDS)
    return result.model_copy(update={"fields": [fields_by_name[name] for name in ordered_names]})

