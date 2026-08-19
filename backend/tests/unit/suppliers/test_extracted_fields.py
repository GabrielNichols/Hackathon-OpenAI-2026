from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from app.modules.suppliers.extraction.models import (
    CRITICAL_EXTRACTION_FIELDS,
    ExtractedFieldDTO,
    ExtractionFieldStatus,
    SupplierExtractionResultDTO,
)
from app.modules.suppliers.extraction.provenance import finalize_extraction_result


def extracted_field(**overrides: object) -> ExtractedFieldDTO:
    values: dict[str, object] = {
        "field_name": "minimum_people",
        "value": "30 pessoas",
        "normalized_value": 30,
        "status": "extracted",
        "confidence": 0.95,
        "source_document_id": "doc_pdf",
        "source_page": 2,
        "source_sheet": None,
        "source_cell_range": None,
        "source_excerpt": "Atendemos eventos a partir de 30 pessoas",
        "extraction_run_id": "ext_run_1",
        "confirmed_by": None,
        "confirmed_at": None,
        "version": 1,
    }
    values.update(overrides)
    return ExtractedFieldDTO.model_validate(values)


def test_extracted_field_keeps_raw_and_normalized_values() -> None:
    field = extracted_field()

    assert field.value == "30 pessoas"
    assert field.normalized_value == 30


def test_model_output_cannot_claim_supplier_confirmation() -> None:
    with pytest.raises(ValidationError):
        extracted_field(
            status="confirmed",
            confirmed_by=None,
            confirmed_at=None,
        )


def test_low_confidence_field_requires_review() -> None:
    result = SupplierExtractionResultDTO(
        extraction_run_id="ext_run_1",
        document_id="doc_pdf",
        extracted_at=datetime(2026, 8, 19, 15, tzinfo=UTC),
        fields=[extracted_field(confidence=0.49)],
    )

    finalized = finalize_extraction_result(result, confidence_threshold=0.8)

    field = next(item for item in finalized.fields if item.field_name == "minimum_people")
    assert field.status is ExtractionFieldStatus.NEEDS_REVIEW


def test_missing_critical_field_is_persisted_as_not_found() -> None:
    result = SupplierExtractionResultDTO(
        extraction_run_id="ext_run_1",
        document_id="doc_pdf",
        extracted_at=datetime(2026, 8, 19, 15, tzinfo=UTC),
        fields=[extracted_field()],
    )

    finalized = finalize_extraction_result(result)

    by_name = {field.field_name: field for field in finalized.fields}
    assert set(CRITICAL_EXTRACTION_FIELDS).issubset(by_name)
    assert by_name["invoice_available"].status is ExtractionFieldStatus.NOT_FOUND
    assert by_name["invoice_available"].value is None
    assert by_name["invoice_available"].confidence is None
    assert by_name["invoice_available"].source_document_id == "doc_pdf"


def test_not_found_field_cannot_contain_a_value_or_confidence() -> None:
    with pytest.raises(ValidationError):
        extracted_field(status="not_found", value="yes", normalized_value=True, confidence=0.8)


def test_confirmation_requires_actor_and_timestamp() -> None:
    confirmed = extracted_field(
        status="confirmed",
        confirmed_by="contact_123",
        confirmed_at=datetime(2026, 8, 19, 15, tzinfo=UTC),
    )

    assert confirmed.confirmed_by == "contact_123"


def test_version_must_start_at_one() -> None:
    with pytest.raises(ValidationError):
        extracted_field(version=0)
