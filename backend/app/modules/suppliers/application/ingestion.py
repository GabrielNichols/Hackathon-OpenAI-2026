from __future__ import annotations

import hashlib
import io
import re
import unicodedata
import zipfile

from pydantic import BaseModel, ConfigDict

from app.modules.suppliers.application.ingestion_ports import (
    Clock,
    DocumentStoragePort,
    ExtractionFailureDTO,
    ExtractionJobDTO,
    ExtractionQueuePort,
    IdGenerator,
    RepositoryConflictError,
    SourceDocumentRepositoryPort,
)
from app.modules.suppliers.extraction.models import (
    DocumentProcessingStatus,
    SourceDocumentDTO,
)


class IngestionError(ValueError):
    code = "INVALID_MATERIAL"


class MaterialValidationError(IngestionError):
    code = "INVALID_MATERIAL"


class MaterialEmptyError(MaterialValidationError):
    code = "EMPTY_MATERIAL"


class MaterialTooLargeError(MaterialValidationError):
    code = "MATERIAL_TOO_LARGE"


class MaterialNotFoundError(IngestionError):
    code = "MATERIAL_NOT_FOUND"


class InvalidDocumentTransitionError(IngestionError):
    code = "INVALID_DOCUMENT_TRANSITION"


class ValidatedMaterial(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    original_filename: str
    sanitized_filename: str
    media_type: str
    content: bytes
    size_bytes: int
    sha256: str


class MaterialIngestionResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    document: SourceDocumentDTO
    blob_reused: bool
    extraction_job: ExtractionJobDTO


_XLSX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
_MEDIA_TYPES_BY_EXTENSION: dict[str, frozenset[str]] = {
    ".pdf": frozenset({"application/pdf"}),
    ".png": frozenset({"image/png"}),
    ".jpg": frozenset({"image/jpeg"}),
    ".jpeg": frozenset({"image/jpeg"}),
    ".xlsx": frozenset({_XLSX_MEDIA_TYPE}),
    ".txt": frozenset({"text/plain"}),
}

_ALLOWED_TRANSITIONS: dict[DocumentProcessingStatus, frozenset[DocumentProcessingStatus]] = {
    DocumentProcessingStatus.STORED: frozenset({DocumentProcessingStatus.EXTRACTION_QUEUED}),
    DocumentProcessingStatus.EXTRACTION_QUEUED: frozenset({DocumentProcessingStatus.EXTRACTING}),
    DocumentProcessingStatus.EXTRACTING: frozenset(
        {
            DocumentProcessingStatus.EXTRACTED,
            DocumentProcessingStatus.EXTRACTION_FAILED,
        }
    ),
    DocumentProcessingStatus.EXTRACTED: frozenset(
        {DocumentProcessingStatus.AWAITING_SUPPLIER_REVIEW}
    ),
}


def _sanitize_filename(original_filename: str) -> tuple[str, str]:
    if not original_filename:
        raise MaterialValidationError("filename is required")
    basename = original_filename.replace("\\", "/").rsplit("/", maxsplit=1)[-1]
    basename = unicodedata.normalize("NFKC", basename)
    basename = "".join(
        character
        for character in basename
        if character != "\x00" and not unicodedata.category(character).startswith("C")
    ).strip()
    if "." not in basename:
        raise MaterialValidationError("filename has no supported extension")
    stem, raw_extension = basename.rsplit(".", maxsplit=1)
    extension = f".{raw_extension.casefold()}"
    if extension not in _MEDIA_TYPES_BY_EXTENSION:
        raise MaterialValidationError("unsupported material extension")

    safe_stem = re.sub(r"[^\w.-]+", "_", stem, flags=re.UNICODE)
    safe_stem = re.sub(r"_+", "_", safe_stem).strip(" ._-")
    if not safe_stem:
        safe_stem = "material"
    maximum_stem_length = 255 - len(extension)
    safe_stem = safe_stem[:maximum_stem_length].rstrip(" ._-") or "material"
    return f"{safe_stem}{extension}", extension


def _validate_content_signature(
    *,
    extension: str,
    media_type: str,
    content: bytes,
    max_size_bytes: int,
) -> None:
    if extension == ".pdf" and not content.startswith(b"%PDF-"):
        raise MaterialValidationError("content is not a PDF")
    if extension == ".png" and not content.startswith(b"\x89PNG\r\n\x1a\n"):
        raise MaterialValidationError("content is not a PNG image")
    if extension in {".jpg", ".jpeg"} and not content.startswith(b"\xff\xd8\xff"):
        raise MaterialValidationError("content is not a JPEG image")
    if extension == ".txt":
        try:
            decoded = content.decode("utf-8")
        except UnicodeDecodeError as error:
            raise MaterialValidationError("text material must be UTF-8") from error
        if not decoded.strip():
            raise MaterialEmptyError("text material is empty")
    if extension == ".xlsx":
        try:
            with zipfile.ZipFile(io.BytesIO(content)) as workbook:
                names = set(workbook.namelist())
                if not {"[Content_Types].xml", "xl/workbook.xml"}.issubset(names):
                    raise MaterialValidationError("XLSX package is missing workbook entries")
                uncompressed_size = sum(item.file_size for item in workbook.infolist())
                maximum_uncompressed_size = max(max_size_bytes * 20, 20_000_000)
                if uncompressed_size > maximum_uncompressed_size:
                    raise MaterialTooLargeError("XLSX expanded content exceeds limit")
        except zipfile.BadZipFile as error:
            raise MaterialValidationError("content is not a valid XLSX package") from error


def validate_material(
    *,
    original_filename: str,
    declared_media_type: str,
    content: bytes,
    max_size_bytes: int,
) -> ValidatedMaterial:
    if max_size_bytes <= 0:
        raise ValueError("max_size_bytes must be positive")
    if not isinstance(content, bytes):
        raise TypeError("material content must be bytes")
    if not content:
        raise MaterialEmptyError("material is empty")
    if len(content) > max_size_bytes:
        raise MaterialTooLargeError("material exceeds configured size limit")

    sanitized_filename, extension = _sanitize_filename(original_filename)
    media_type = declared_media_type.partition(";")[0].strip().casefold()
    if media_type not in _MEDIA_TYPES_BY_EXTENSION[extension]:
        raise MaterialValidationError("declared MIME type does not match extension")
    _validate_content_signature(
        extension=extension,
        media_type=media_type,
        content=content,
        max_size_bytes=max_size_bytes,
    )
    return ValidatedMaterial(
        original_filename=original_filename,
        sanitized_filename=sanitized_filename,
        media_type=media_type,
        content=content,
        size_bytes=len(content),
        sha256=hashlib.sha256(content).hexdigest(),
    )


class SupplierMaterialIngestionService:
    def __init__(
        self,
        *,
        storage: DocumentStoragePort,
        documents: SourceDocumentRepositoryPort,
        extraction_queue: ExtractionQueuePort,
        clock: Clock,
        ids: IdGenerator,
        max_size_bytes: int,
    ) -> None:
        if max_size_bytes <= 0:
            raise ValueError("max_size_bytes must be positive")
        self._storage = storage
        self._documents = documents
        self._extraction_queue = extraction_queue
        self._clock = clock
        self._ids = ids
        self._max_size_bytes = max_size_bytes

    @property
    def max_size_bytes(self) -> int:
        return self._max_size_bytes

    async def ingest_file(
        self,
        *,
        tenant_id: str,
        supplier_id: str,
        original_filename: str,
        declared_media_type: str,
        content: bytes,
    ) -> MaterialIngestionResult:
        if not tenant_id or not supplier_id:
            raise MaterialValidationError("tenant and supplier are required")
        material = validate_material(
            original_filename=original_filename,
            declared_media_type=declared_media_type,
            content=content,
            max_size_bytes=self._max_size_bytes,
        )
        blob = await self._storage.store(
            tenant_id=tenant_id,
            sha256=material.sha256,
            media_type=material.media_type,
            content=material.content,
        )
        now = self._clock.now()
        document = SourceDocumentDTO(
            document_id=self._ids.new("doc"),
            supplier_id=supplier_id,
            tenant_id=tenant_id,
            original_filename=material.original_filename,
            sanitized_filename=material.sanitized_filename,
            media_type=material.media_type,
            size_bytes=material.size_bytes,
            sha256=material.sha256,
            blob_id=blob.blob_id,
            created_at=now,
            status=DocumentProcessingStatus.STORED,
        )
        await self._documents.add(
            document,
            initial_statuses=(
                DocumentProcessingStatus.RECEIVED,
                DocumentProcessingStatus.VALIDATED,
                DocumentProcessingStatus.STORED,
            ),
            occurred_at=now,
        )
        job = ExtractionJobDTO(
            job_id=self._ids.new("job"),
            tenant_id=tenant_id,
            supplier_id=supplier_id,
            document_id=document.document_id,
            enqueued_at=now,
        )
        await self._extraction_queue.enqueue(job)
        queued_document = await self._transition(
            document.document_id,
            DocumentProcessingStatus.EXTRACTION_QUEUED,
        )
        return MaterialIngestionResult(
            document=queued_document,
            blob_reused=blob.reused,
            extraction_job=job,
        )

    async def ingest_text(
        self,
        *,
        tenant_id: str,
        supplier_id: str,
        text: str,
        original_filename: str = "whatsapp.txt",
    ) -> MaterialIngestionResult:
        return await self.ingest_file(
            tenant_id=tenant_id,
            supplier_id=supplier_id,
            original_filename=original_filename,
            declared_media_type="text/plain",
            content=text.encode("utf-8"),
        )

    async def mark_extraction_started(self, document_id: str) -> SourceDocumentDTO:
        return await self._transition(document_id, DocumentProcessingStatus.EXTRACTING)

    async def mark_extraction_completed(self, document_id: str) -> SourceDocumentDTO:
        return await self._transition(document_id, DocumentProcessingStatus.EXTRACTED)

    async def mark_awaiting_supplier_review(self, document_id: str) -> SourceDocumentDTO:
        return await self._transition(
            document_id,
            DocumentProcessingStatus.AWAITING_SUPPLIER_REVIEW,
        )

    async def mark_extraction_failed(
        self,
        document_id: str,
        *,
        reason_code: str,
    ) -> SourceDocumentDTO:
        if re.fullmatch(r"[A-Z][A-Z0-9_]{2,63}", reason_code) is None:
            raise ValueError("reason_code must be a stable uppercase code")
        failed = await self._transition(
            document_id,
            DocumentProcessingStatus.EXTRACTION_FAILED,
        )
        await self._documents.record_failure(
            ExtractionFailureDTO(
                document_id=document_id,
                reason_code=reason_code,
                failed_at=self._clock.now(),
            )
        )
        return failed

    async def get_material(
        self,
        *,
        tenant_id: str,
        supplier_id: str,
        document_id: str,
    ) -> tuple[SourceDocumentDTO, bytes]:
        document = await self._documents.get(document_id)
        if (
            document is None
            or document.tenant_id != tenant_id
            or document.supplier_id != supplier_id
        ):
            raise MaterialNotFoundError("material was not found")
        content = await self._storage.read(tenant_id=tenant_id, blob_id=document.blob_id)
        return document, content

    async def _transition(
        self,
        document_id: str,
        new_status: DocumentProcessingStatus,
    ) -> SourceDocumentDTO:
        document = await self._documents.get(document_id)
        if document is None:
            raise MaterialNotFoundError("material was not found")
        if new_status not in _ALLOWED_TRANSITIONS.get(document.status, frozenset()):
            raise InvalidDocumentTransitionError(
                f"document cannot move from {document.status} to {new_status}"
            )
        try:
            return await self._documents.transition(
                document_id,
                expected_status=document.status,
                new_status=new_status,
                occurred_at=self._clock.now(),
            )
        except RepositoryConflictError as error:
            raise InvalidDocumentTransitionError(str(error)) from error
