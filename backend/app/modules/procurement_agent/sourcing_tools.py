from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.modules.procurement_agent.models import CreateRFQRoundCommand, RFQRoundDTO
from app.modules.procurement_agent.ports import RFQExecutionPort
from app.modules.procurement_agent.tool_registry import ToolRegistry, ToolSpec
from app.modules.sourcing.eligibility import SupplierEligibilityEngine
from app.modules.sourcing.models import (
    SupplierCandidateDTO,
    SupplierEligibilityResult,
    SupplierSearchCriteria,
)
from app.modules.sourcing.ports import SupplierDirectoryPort
from app.modules.sourcing.selection import select_rfq_recipients


class SearchSuppliersInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    criteria: SupplierSearchCriteria


class SearchSuppliersOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    candidates: list[SupplierCandidateDTO]


class EvaluateSupplierInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    candidate: SupplierCandidateDTO
    criteria: SupplierSearchCriteria
    as_of: datetime


class EvaluateSupplierOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    result: SupplierEligibilityResult


class SelectRecipientsInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    results: list[SupplierEligibilityResult]
    candidate_supplier_ids: list[str]
    limit: int = Field(ge=1, le=20)

    @model_validator(mode="after")
    def results_must_come_from_directory_candidates(self) -> SelectRecipientsInput:
        candidates = set(self.candidate_supplier_ids)
        unknown = {result.supplier_id for result in self.results} - candidates
        if unknown:
            raise ValueError("eligibility result references an unknown directory candidate")
        return self


class SelectRecipientsOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    supplier_ids: list[str]


class InMemorySupplierDirectory:
    """Dev 2-compatible demo adapter. Search returns recall; eligibility remains Dev 3's job."""

    def __init__(self, candidates: list[SupplierCandidateDTO]) -> None:
        self._candidates = {
            candidate.supplier_id: candidate.model_copy(deep=True) for candidate in candidates
        }
        self.query_count = 0

    async def search(self, criteria: SupplierSearchCriteria) -> list[SupplierCandidateDTO]:
        del criteria
        self.query_count += 1
        return [
            self._candidates[supplier_id].model_copy(deep=True)
            for supplier_id in sorted(self._candidates)
        ]

    async def get(self, supplier_id: str) -> SupplierCandidateDTO | None:
        candidate = self._candidates.get(supplier_id)
        return candidate.model_copy(deep=True) if candidate else None


def register_sourcing_tools(
    registry: ToolRegistry,
    *,
    directory: SupplierDirectoryPort,
    eligibility: SupplierEligibilityEngine,
    rfq: RFQExecutionPort,
) -> None:
    async def search(arguments: BaseModel) -> BaseModel:
        typed = SearchSuppliersInput.model_validate(arguments)
        return SearchSuppliersOutput(candidates=await directory.search(typed.criteria))

    async def evaluate(arguments: BaseModel) -> BaseModel:
        typed = EvaluateSupplierInput.model_validate(arguments)
        return EvaluateSupplierOutput(
            result=eligibility.evaluate(
                typed.candidate,
                typed.criteria,
                as_of=typed.as_of,
            )
        )

    async def select(arguments: BaseModel) -> BaseModel:
        typed = SelectRecipientsInput.model_validate(arguments)
        return SelectRecipientsOutput(
            supplier_ids=select_rfq_recipients(typed.results, typed.limit)
        )

    async def create_rfq(arguments: BaseModel) -> BaseModel:
        typed = CreateRFQRoundCommand.model_validate(arguments)
        return await rfq.create_round(typed)

    registry.register(
        ToolSpec(
            name="search_suppliers",
            input_model=SearchSuppliersInput,
            output_model=SearchSuppliersOutput,
            allowed_states=frozenset({"SOURCING"}),
            policy_action="search_suppliers",
            audit_event_type="SUPPLIER_DIRECTORY_QUERIED",
            timeout_seconds=3,
            idempotent=True,
            handler=search,
        )
    )
    registry.register(
        ToolSpec(
            name="evaluate_supplier_eligibility",
            input_model=EvaluateSupplierInput,
            output_model=EvaluateSupplierOutput,
            allowed_states=frozenset({"SOURCING"}),
            policy_action="evaluate_supplier_eligibility",
            audit_event_type="SUPPLIER_ELIGIBILITY_EVALUATED",
            timeout_seconds=1,
            idempotent=True,
            handler=evaluate,
        )
    )
    registry.register(
        ToolSpec(
            name="select_rfq_recipients",
            input_model=SelectRecipientsInput,
            output_model=SelectRecipientsOutput,
            allowed_states=frozenset({"SOURCING"}),
            policy_action="select_rfq_recipients",
            audit_event_type="RFQ_RECIPIENTS_SELECTED",
            timeout_seconds=1,
            idempotent=True,
            handler=select,
        )
    )
    registry.register(
        ToolSpec(
            name="create_rfq_round",
            input_model=CreateRFQRoundCommand,
            output_model=RFQRoundDTO,
            allowed_states=frozenset({"SOURCING"}),
            policy_action="create_rfq_round",
            audit_event_type="RFQ_ROUND_DRAFT_CREATED",
            timeout_seconds=3,
            idempotent=True,
            handler=create_rfq,
        )
    )


__all__ = [
    "EvaluateSupplierInput",
    "EvaluateSupplierOutput",
    "InMemorySupplierDirectory",
    "SearchSuppliersInput",
    "SearchSuppliersOutput",
    "SelectRecipientsInput",
    "SelectRecipientsOutput",
    "register_sourcing_tools",
]
