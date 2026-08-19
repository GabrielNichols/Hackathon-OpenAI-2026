from __future__ import annotations

import asyncio
import hashlib
import os
import re
import tempfile
from pathlib import Path

from app.modules.suppliers.application.ingestion_ports import (
    BlobNotFoundError,
    StoredBlobDTO,
)

_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")


class FileSystemDocumentStorage:
    """Content-addressed local adapter suitable for isolated development and demos."""

    def __init__(self, root: Path) -> None:
        self._root = root.resolve()
        self._root.mkdir(parents=True, exist_ok=True)

    async def store(
        self,
        *,
        tenant_id: str,
        sha256: str,
        media_type: str,
        content: bytes,
    ) -> StoredBlobDTO:
        return await asyncio.to_thread(
            self._store_sync,
            tenant_id,
            sha256,
            media_type,
            content,
        )

    def _store_sync(
        self,
        tenant_id: str,
        sha256: str,
        media_type: str,
        content: bytes,
    ) -> StoredBlobDTO:
        actual_sha256 = hashlib.sha256(content).hexdigest()
        if actual_sha256 != sha256 or _SHA256_PATTERN.fullmatch(sha256) is None:
            raise ValueError("content does not match declared sha256")

        tenant_digest = self._tenant_digest(tenant_id)
        blob_id = f"blob_{tenant_digest}_{sha256}"
        destination = self._blob_path(tenant_digest, sha256)
        destination.parent.mkdir(parents=True, exist_ok=True)
        reused = destination.exists()

        if not reused:
            temporary_path: Path | None = None
            try:
                with tempfile.NamedTemporaryFile(
                    dir=destination.parent,
                    prefix=".upload-",
                    delete=False,
                ) as temporary:
                    temporary.write(content)
                    temporary.flush()
                    os.fsync(temporary.fileno())
                    temporary_path = Path(temporary.name)
                try:
                    os.link(temporary_path, destination)
                except FileExistsError:
                    reused = True
            finally:
                if temporary_path is not None:
                    temporary_path.unlink(missing_ok=True)

        persisted = destination.read_bytes()
        if hashlib.sha256(persisted).hexdigest() != sha256:
            raise OSError("stored blob failed sha256 verification")
        return StoredBlobDTO(
            blob_id=blob_id,
            tenant_id=tenant_id,
            sha256=sha256,
            media_type=media_type,
            size_bytes=len(persisted),
            reused=reused,
        )

    async def read(self, *, tenant_id: str, blob_id: str) -> bytes:
        return await asyncio.to_thread(self._read_sync, tenant_id, blob_id)

    def _read_sync(self, tenant_id: str, blob_id: str) -> bytes:
        tenant_digest = self._tenant_digest(tenant_id)
        prefix = f"blob_{tenant_digest}_"
        if not blob_id.startswith(prefix):
            raise BlobNotFoundError(blob_id)
        sha256 = blob_id.removeprefix(prefix)
        if _SHA256_PATTERN.fullmatch(sha256) is None:
            raise BlobNotFoundError(blob_id)
        path = self._blob_path(tenant_digest, sha256)
        try:
            content = path.read_bytes()
        except FileNotFoundError as error:
            raise BlobNotFoundError(blob_id) from error
        if hashlib.sha256(content).hexdigest() != sha256:
            raise OSError("stored blob failed sha256 verification")
        return content

    def _blob_path(self, tenant_digest: str, sha256: str) -> Path:
        return self._root / tenant_digest / sha256[:2] / sha256

    @staticmethod
    def _tenant_digest(tenant_id: str) -> str:
        return hashlib.sha256(tenant_id.encode()).hexdigest()[:24]
