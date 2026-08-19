from __future__ import annotations

import hashlib
from collections.abc import Callable
from pathlib import Path

import pytest

from app.modules.suppliers.application.ingestion_ports import (
    BlobNotFoundError,
    DocumentStoragePort,
)
from app.modules.suppliers.persistence.filesystem_storage import FileSystemDocumentStorage
from app.modules.suppliers.persistence.in_memory import InMemoryDocumentStorage


@pytest.fixture(params=["memory", "filesystem"])
def storage(
    request: pytest.FixtureRequest,
    tmp_path: Path,
) -> DocumentStoragePort:
    factories: dict[str, Callable[[], DocumentStoragePort]] = {
        "memory": InMemoryDocumentStorage,
        "filesystem": lambda: FileSystemDocumentStorage(tmp_path / "blobs"),
    }
    return factories[str(request.param)]()


@pytest.mark.asyncio
async def test_duplicate_content_reuses_tenant_blob(storage: DocumentStoragePort) -> None:
    content = b"same bytes"
    sha256 = hashlib.sha256(content).hexdigest()
    first = await storage.store(
        tenant_id="org_1",
        sha256=sha256,
        media_type="application/pdf",
        content=content,
    )
    second = await storage.store(
        tenant_id="org_1",
        sha256=sha256,
        media_type="application/pdf",
        content=content,
    )

    assert first.blob_id == second.blob_id
    assert first.reused is False
    assert second.reused is True
    assert await storage.read(tenant_id="org_1", blob_id=first.blob_id) == content


@pytest.mark.asyncio
async def test_storage_deduplication_is_tenant_scoped(storage: DocumentStoragePort) -> None:
    content = b"same bytes"
    sha256 = hashlib.sha256(content).hexdigest()
    first = await storage.store(
        tenant_id="org_1",
        sha256=sha256,
        media_type="application/pdf",
        content=content,
    )
    other_tenant = await storage.store(
        tenant_id="org_2",
        sha256=sha256,
        media_type="application/pdf",
        content=content,
    )

    assert first.blob_id != other_tenant.blob_id
    with pytest.raises(BlobNotFoundError):
        await storage.read(tenant_id="org_2", blob_id=first.blob_id)


@pytest.mark.asyncio
async def test_storage_rejects_content_that_does_not_match_hash(
    storage: DocumentStoragePort,
) -> None:
    with pytest.raises(ValueError, match="sha256"):
        await storage.store(
            tenant_id="org_1",
            sha256="0" * 64,
            media_type="application/pdf",
            content=b"different hash",
        )
