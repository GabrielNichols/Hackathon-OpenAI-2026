from __future__ import annotations

from datetime import datetime
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field

from .core_compat import (
    AuditPort,
    Clock,
    IdGenerator,
    SupplierAuditEvent,
    SupplierLifecycleStatus,
)


class CreateSupplierProfileCommand(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    organization_id: str = Field(min_length=1)
    legal_name: str = Field(min_length=1)
    trade_name: str = Field(min_length=1)
    cnpj: str | None = None
    contact_name: str = Field(min_length=1)
    contact_email: str = Field(min_length=1)
    contact_phone: str = Field(min_length=1)


class SupplierProfileDTO(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    supplier_id: str
    organization_id: str
    legal_name: str
    trade_name: str
    cnpj: str | None
    contact_id: str
    contact_name: str
    contact_email: str
    contact_phone: str
    status: SupplierLifecycleStatus
    last_confirmed_at: datetime | None
    created_at: datetime
    version: int = Field(ge=1)


class SupplierProfileError(Exception):
    def __init__(self, message: str, *, code: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class SupplierProfileRepository(Protocol):
    async def add(self, profile: SupplierProfileDTO) -> None: ...

    async def get(self, *, tenant_id: str, supplier_id: str) -> SupplierProfileDTO | None: ...


class InMemorySupplierProfileRepository:
    def __init__(self) -> None:
        self._profiles: dict[tuple[str, str], SupplierProfileDTO] = {}

    async def add(self, profile: SupplierProfileDTO) -> None:
        key = (profile.organization_id, profile.supplier_id)
        if key in self._profiles:
            raise SupplierProfileError("supplier already exists", code="CONFLICT")
        self._profiles[key] = profile

    async def get(self, *, tenant_id: str, supplier_id: str) -> SupplierProfileDTO | None:
        return self._profiles.get((tenant_id, supplier_id))


class SupplierProfileService:
    def __init__(
        self,
        *,
        repository: SupplierProfileRepository,
        audit_port: AuditPort,
        clock: Clock,
        id_generator: IdGenerator,
    ) -> None:
        self._repository = repository
        self._audit = audit_port
        self._clock = clock
        self._ids = id_generator

    async def create(self, command: CreateSupplierProfileCommand) -> SupplierProfileDTO:
        profile = SupplierProfileDTO(
            supplier_id=self._ids.new("sup"),
            organization_id=command.organization_id,
            legal_name=command.legal_name,
            trade_name=command.trade_name,
            cnpj=command.cnpj,
            contact_id=self._ids.new("contact"),
            contact_name=command.contact_name,
            contact_email=command.contact_email,
            contact_phone=command.contact_phone,
            status=SupplierLifecycleStatus.DRAFT,
            last_confirmed_at=None,
            created_at=self._clock.now(),
            version=1,
        )
        await self._repository.add(profile)
        await self._audit.append(
            [
                SupplierAuditEvent(
                    event_id=self._ids.new("evt"),
                    event_type="SUPPLIER_CREATED",
                    aggregate_id=profile.supplier_id,
                    actor_type="system",
                    actor_id=None,
                    occurred_at=self._clock.now(),
                    previous_state=None,
                    new_state=SupplierLifecycleStatus.DRAFT,
                    correlation_id=self._ids.new("cor"),
                    payload={"contact_id": profile.contact_id},
                )
            ]
        )
        return profile

    async def get(self, *, tenant_id: str, supplier_id: str) -> SupplierProfileDTO:
        profile = await self._repository.get(tenant_id=tenant_id, supplier_id=supplier_id)
        if profile is None:
            raise SupplierProfileError("supplier profile not found", code="NOT_FOUND")
        return profile
