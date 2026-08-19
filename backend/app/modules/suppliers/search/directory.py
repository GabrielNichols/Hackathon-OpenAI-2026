from __future__ import annotations

from typing import Protocol

from ..application.core_compat import SupplierLifecycleStatus
from .models import SupplierCandidateDTO, SupplierDirectoryRecord, SupplierSearchCriteria


class SupplierDirectoryPort(Protocol):
    async def search(self, criteria: SupplierSearchCriteria) -> list[SupplierCandidateDTO]: ...

    async def get(self, supplier_id: str) -> SupplierCandidateDTO | None: ...


class InMemorySupplierDirectory:
    """Tenant-scoped directory adapter suitable for contract tests and local integration."""

    def __init__(self, *, tenant_id: str, records: list[SupplierDirectoryRecord]) -> None:
        self._tenant_id = tenant_id
        self._records = tuple(records)

    async def search(self, criteria: SupplierSearchCriteria) -> list[SupplierCandidateDTO]:
        if criteria.tenant_id != self._tenant_id:
            return []
        matching = [
            record
            for record in self._records
            if self._is_visible(record) and self._matches_recall_filters(record, criteria)
        ]
        return [
            self._candidate(record)
            for record in sorted(matching, key=lambda item: item.supplier_id)
        ]

    async def get(self, supplier_id: str) -> SupplierCandidateDTO | None:
        record = next(
            (
                item
                for item in self._records
                if item.supplier_id == supplier_id and self._is_visible(item)
            ),
            None,
        )
        return None if record is None else self._candidate(record)

    def _is_visible(self, record: SupplierDirectoryRecord) -> bool:
        return (
            record.tenant_id == self._tenant_id
            and record.status is SupplierLifecycleStatus.ACTIVE
            and record.profile_confirmed
            and record.last_confirmed_at is not None
        )

    @staticmethod
    def _matches_recall_filters(
        record: SupplierDirectoryRecord,
        criteria: SupplierSearchCriteria,
    ) -> bool:
        normalized_categories = {value.casefold() for value in record.categories}
        normalized_cities = {value.casefold() for value in record.service_cities}
        normalized_districts = {value.casefold() for value in record.service_districts}
        if criteria.category.casefold() not in normalized_categories:
            return False
        if criteria.city.casefold() not in normalized_cities:
            return False
        if (
            criteria.district is not None
            and normalized_districts
            and criteria.district.casefold() not in normalized_districts
        ):
            return False
        if record.minimum_people is not None and criteria.people_count < record.minimum_people:
            return False
        if record.maximum_people is not None and criteria.people_count > record.maximum_people:
            return False
        if criteria.invoice_required and record.invoice_available is not True:
            return False
        if (
            criteria.maximum_lead_time_hours is not None
            and record.lead_time_hours is not None
            and record.lead_time_hours > criteria.maximum_lead_time_hours
        ):
            return False
        mandatory_tags = {tag.casefold() for tag in criteria.mandatory_tags}
        available_tags = {tag.casefold() for tag in record.sustainability_tags}
        if mandatory_tags - available_tags:
            return False
        return True

    @classmethod
    def _candidate(cls, record: SupplierDirectoryRecord) -> SupplierCandidateDTO:
        missing_fields = cls._missing_fields(record)
        service_areas = list(record.service_cities)
        service_areas.extend(
            f"{city}/{district}"
            for city in record.service_cities
            for district in record.service_districts
        )
        return SupplierCandidateDTO(
            supplier_id=record.supplier_id,
            display_name=record.display_name,
            status=record.status,
            categories=list(record.categories),
            service_areas=cls._unique(service_areas),
            minimum_people=record.minimum_people,
            maximum_people=record.maximum_people,
            lead_time_hours=record.lead_time_hours,
            invoice_available=record.invoice_available,
            dietary_capabilities=dict(record.dietary_capabilities),
            sustainability_tags=list(record.sustainability_tags),
            last_confirmed_at=record.last_confirmed_at,
            evidence_refs=cls._unique(record.evidence_refs),
            missing_fields=missing_fields,
        )

    @staticmethod
    def _missing_fields(record: SupplierDirectoryRecord) -> list[str]:
        checks = {
            "categories": not record.categories,
            "service_cities": not record.service_cities,
            "minimum_people": record.minimum_people is None,
            "maximum_people": record.maximum_people is None,
            "lead_time_hours": record.lead_time_hours is None,
            "invoice_available": record.invoice_available is None,
            "dietary_capabilities": not record.dietary_capabilities,
            "evidence_refs": not record.evidence_refs,
        }
        return [field_name for field_name, missing in checks.items() if missing]

    @staticmethod
    def _unique(values: list[str]) -> list[str]:
        return list(dict.fromkeys(values))
