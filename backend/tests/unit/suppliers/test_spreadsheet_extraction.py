import hashlib
from datetime import UTC, datetime
from io import BytesIO

from openpyxl import Workbook

from app.modules.suppliers.extraction.models import (
    ExtractionFieldStatus,
    SourceDocumentDTO,
    SupplierExtractionSchema,
)
from app.modules.suppliers.extraction.spreadsheet import parse_supplier_spreadsheet

NOW = datetime(2026, 8, 19, 15, tzinfo=UTC)
XLSX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def workbook_bytes(rows: list[tuple[object, object]]) -> bytes:
    workbook = Workbook()
    worksheet = workbook.active
    worksheet.title = "Perfil"
    worksheet.append(("Campo", "Valor"))
    for row in rows:
        worksheet.append(row)
    buffer = BytesIO()
    workbook.save(buffer)
    workbook.close()
    return buffer.getvalue()


def document_for(content: bytes) -> SourceDocumentDTO:
    return SourceDocumentDTO(
        document_id="doc_xlsx",
        supplier_id="sup_1",
        tenant_id="org_1",
        original_filename="perfil.xlsx",
        sanitized_filename="perfil.xlsx",
        media_type=XLSX_MEDIA_TYPE,
        size_bytes=len(content),
        sha256=hashlib.sha256(content).hexdigest(),
        blob_id="blob_xlsx",
        created_at=NOW,
    )


def test_spreadsheet_parser_preserves_sheet_and_cell_provenance() -> None:
    content = workbook_bytes(
        [
            ("Nome comercial", "Synthetic Catering"),
            ("Quantidade mínima", "30 pessoas"),
            ("Cidades atendidas", "São Paulo; Osasco"),
            ("Emite nota fiscal", "Não informado"),
            ("Sem glúten", "Sim"),
        ]
    )

    result = parse_supplier_spreadsheet(
        document=document_for(content),
        content=content,
        schema=SupplierExtractionSchema.canonical(),
        extracted_at=NOW,
    )

    fields = {field.field_name: field for field in result.fields}
    assert fields["trade_name"].normalized_value == "Synthetic Catering"
    assert fields["minimum_people"].normalized_value == 30
    assert fields["minimum_people"].source_sheet == "Perfil"
    assert fields["minimum_people"].source_cell_range == "B3"
    assert fields["service_cities"].normalized_value == ["São Paulo", "Osasco"]
    assert fields["invoice_available"].status is ExtractionFieldStatus.NEEDS_REVIEW
    assert fields["invoice_available"].normalized_value is None
    assert all(field.confirmed_by is None for field in result.fields)


def test_spreadsheet_parser_marks_contradictory_cells_for_review() -> None:
    content = workbook_bytes(
        [
            ("Quantidade mínima", "30 pessoas"),
            ("Quantidade mínima", "50 pessoas"),
        ]
    )

    result = parse_supplier_spreadsheet(
        document=document_for(content),
        content=content,
        schema=SupplierExtractionSchema.canonical(),
        extracted_at=NOW,
    )

    [field] = result.fields
    assert field.status is ExtractionFieldStatus.NEEDS_REVIEW
    assert field.value == ["30 pessoas", "50 pessoas"]
    assert field.normalized_value is None
    assert field.source_cell_range == "B2"
    assert field.source_excerpt == "Spreadsheet source: Perfil!B2, Perfil!B3"
