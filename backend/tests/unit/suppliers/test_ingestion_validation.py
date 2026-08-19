from __future__ import annotations

import hashlib
import io
import zipfile

import pytest

from app.modules.suppliers.application.ingestion import (
    MaterialEmptyError,
    MaterialTooLargeError,
    MaterialValidationError,
    ValidatedMaterial,
    validate_material,
)


def xlsx_bytes() -> bytes:
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as workbook:
        workbook.writestr("[Content_Types].xml", "<Types />")
        workbook.writestr("xl/workbook.xml", "<workbook />")
    return stream.getvalue()


@pytest.mark.parametrize(
    ("filename", "media_type", "content"),
    [
        ("catalog.pdf", "application/pdf", b"%PDF-1.7\nfixture"),
        ("menu.png", "image/png", b"\x89PNG\r\n\x1a\nfixture"),
        ("menu.jpg", "image/jpeg", b"\xff\xd8\xff\xe0fixture"),
        ("menu.jpeg", "image/jpeg", b"\xff\xd8\xff\xe1fixture"),
        (
            "prices.xlsx",
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            xlsx_bytes(),
        ),
        ("whatsapp.txt", "text/plain; charset=utf-8", "Olá! mínimo 30".encode()),
    ],
)
def test_p0_material_formats_are_validated(
    filename: str,
    media_type: str,
    content: bytes,
) -> None:
    material = validate_material(
        original_filename=filename,
        declared_media_type=media_type,
        content=content,
        max_size_bytes=2_000_000,
    )

    assert isinstance(material, ValidatedMaterial)
    assert material.media_type == media_type.partition(";")[0]
    assert material.size_bytes == len(content)
    assert material.sha256 == hashlib.sha256(content).hexdigest()


def test_empty_file_is_rejected() -> None:
    with pytest.raises(MaterialEmptyError):
        validate_material(
            original_filename="empty.pdf",
            declared_media_type="application/pdf",
            content=b"",
            max_size_bytes=100,
        )


def test_whitespace_only_text_is_rejected() -> None:
    with pytest.raises(MaterialEmptyError):
        validate_material(
            original_filename="message.txt",
            declared_media_type="text/plain",
            content=b"  \n\t ",
            max_size_bytes=100,
        )


def test_material_larger_than_configured_limit_is_rejected() -> None:
    with pytest.raises(MaterialTooLargeError):
        validate_material(
            original_filename="large.pdf",
            declared_media_type="application/pdf",
            content=b"%PDF-" + b"x" * 20,
            max_size_bytes=10,
        )


@pytest.mark.parametrize(
    ("filename", "media_type", "content"),
    [
        ("menu.png", "application/pdf", b"\x89PNG\r\n\x1a\nfixture"),
        ("menu.pdf", "application/pdf", b"not a pdf"),
        ("prices.xlsx", "application/zip", xlsx_bytes()),
        (
            "prices.xlsx",
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            b"PK",
        ),
        ("catalog.exe", "application/pdf", b"%PDF-1.7\nfixture"),
    ],
)
def test_mime_extension_and_content_must_agree(
    filename: str,
    media_type: str,
    content: bytes,
) -> None:
    with pytest.raises(MaterialValidationError):
        validate_material(
            original_filename=filename,
            declared_media_type=media_type,
            content=content,
            max_size_bytes=2_000_000,
        )


def test_text_must_be_valid_utf8() -> None:
    with pytest.raises(MaterialValidationError):
        validate_material(
            original_filename="message.txt",
            declared_media_type="text/plain",
            content=b"\xff\xfe",
            max_size_bytes=100,
        )


def test_filename_is_sanitized_without_losing_original_metadata() -> None:
    material = validate_material(
        original_filename="../../Cardápio agosto (final).PDF",
        declared_media_type="application/pdf",
        content=b"%PDF-1.7\nfixture",
        max_size_bytes=100,
    )

    assert material.original_filename == "../../Cardápio agosto (final).PDF"
    assert material.sanitized_filename == "Cardápio_agosto_final.pdf"
    assert "/" not in material.sanitized_filename
    assert "\\" not in material.sanitized_filename


def test_filename_with_only_unsafe_characters_gets_safe_stem() -> None:
    material = validate_material(
        original_filename="../???.pdf",
        declared_media_type="application/pdf",
        content=b"%PDF-1.7\nfixture",
        max_size_bytes=100,
    )

    assert material.sanitized_filename == "material.pdf"
