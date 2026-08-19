import asyncio
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
from app.modules.procurement_requests import (
    LocalPortugueseProcurementInterpreter,
    ProcurementRequestDraft,
    ProcurementRequestService,
)


class FixedClock:
    def __init__(self, value: datetime) -> None:
        self.value = value

    def now(self) -> datetime:
        return self.value


@pytest.fixture
def clock() -> FixedClock:
    return FixedClock(datetime(2026, 8, 19, 12, 0, tzinfo=ZoneInfo("America/Sao_Paulo")))


def test_complete_portuguese_request_is_structured_with_injected_clock(
    clock: FixedClock,
) -> None:
    interpreter = LocalPortugueseProcurementInterpreter(clock)

    result = asyncio.run(
        interpreter.interpret(
            "Preciso de um coffee break para 80 pessoas na próxima sexta-feira, "
            "entregue às 8h30 na Vila Olímpia. Teremos 12 vegetarianos, 4 veganos e "
            "3 pessoas com restrição a glúten. O orçamento máximo é R$ 4.500. "
            "É obrigatório emitir nota fiscal e evitar descartáveis plásticos. "
            "Propostas até amanhã às 18h. Aprovador: approver_demo."
        )
    )

    patch = result.extracted_fields
    assert patch.event_date.isoformat() == "2026-08-21"
    assert patch.delivery_time.isoformat() == "08:30:00"
    assert patch.people_count == 80
    assert patch.maximum_total_cents == 450_000
    assert patch.vegetarian_count == 12
    assert patch.vegan_count == 4
    assert patch.gluten_free_count == 3
    assert patch.invoice_required is True
    assert patch.no_single_use_plastic is True
    assert patch.location_district == "Vila Olímpia"
    assert patch.response_deadline.isoformat() == "2026-08-20T18:00:00-03:00"
    assert patch.approver_user_id == "approver_demo"
    assert result.missing_required_fields == []


def test_conflicting_people_count_is_preserved_for_confirmation(
    clock: FixedClock,
) -> None:
    current = ProcurementRequestDraft(
        request_id="pr_123",
        description="Coffee break",
        people_count=80,
    )
    interpreter = LocalPortugueseProcurementInterpreter(clock)

    result = asyncio.run(
        interpreter.interpret(
            "Na verdade serão 90 pessoas.",
            current,
        )
    )

    assert "people_count" not in result.extracted_fields.model_fields_set
    assert result.conflicts[0].field == "people_count"
    assert result.conflicts[0].current_value == 80
    assert result.conflicts[0].candidate_value == 90
    merged = ProcurementRequestService(clock=clock).apply_interpretation(current, result)
    assert merged.people_count == 80


def test_deadline_without_time_remains_ambiguous(clock: FixedClock) -> None:
    result = asyncio.run(
        LocalPortugueseProcurementInterpreter(clock).interpret(
            "Coffee break para 20 pessoas. Propostas até amanhã."
        )
    )

    assert result.extracted_fields.response_deadline is None
    assert "RESPONSE_DEADLINE_TIME_REQUIRED" in result.ambiguities


def test_negative_invoice_and_plastic_requirements_are_not_truthy_defaults(
    clock: FixedClock,
) -> None:
    result = asyncio.run(
        LocalPortugueseProcurementInterpreter(clock).interpret(
            "Não precisa emitir nota fiscal e não é necessário evitar plástico."
        )
    )

    assert result.extracted_fields.invoice_required is False
    assert result.extracted_fields.no_single_use_plastic is False


def test_event_and_deadline_are_not_swapped_when_quote_count_comes_first(
    clock: FixedClock,
) -> None:
    result = asyncio.run(
        LocalPortugueseProcurementInterpreter(clock).interpret(
            "Quero 3 cotações para o evento em 25/08/2026 às 12h; respostas até 20/08/2026 às 18h."
        )
    )

    assert result.extracted_fields.event_date.isoformat() == "2026-08-25"
    assert result.extracted_fields.delivery_time.isoformat() == "12:00:00"
    assert result.extracted_fields.response_deadline.isoformat() == "2026-08-20T18:00:00-03:00"


@pytest.mark.parametrize(
    "message",
    [
        "O preço estimado é 50 reais por pessoa.",
        "A taxa de entrega custa 100 reais.",
        "O fornecedor informou taxa de R$ 100.",
    ],
)
def test_unit_price_or_delivery_fee_is_not_invented_as_total_budget(
    clock: FixedClock,
    message: str,
) -> None:
    result = asyncio.run(LocalPortugueseProcurementInterpreter(clock).interpret(message))

    assert result.extracted_fields.maximum_total_cents is None


def test_negation_scope_does_not_turn_supplier_capability_into_buyer_requirement(
    clock: FixedClock,
) -> None:
    not_required = asyncio.run(
        LocalPortugueseProcurementInterpreter(clock).interpret(
            "Nota fiscal não é obrigatória. Sem restrição a plástico descartável."
        )
    )
    unavailable = asyncio.run(
        LocalPortugueseProcurementInterpreter(clock).interpret(
            "O fornecedor não pode emitir nota fiscal."
        )
    )

    assert not_required.extracted_fields.invoice_required is False
    assert not_required.extracted_fields.no_single_use_plastic is False
    assert unavailable.extracted_fields.invoice_required is None
    assert "AMBIGUOUS_INVOICE_REQUIREMENT" in unavailable.ambiguities


def test_no_plastic_can_be_stated_as_cannot_have(clock: FixedClock) -> None:
    result = asyncio.run(
        LocalPortugueseProcurementInterpreter(clock).interpret(
            "O coffee break não pode ter plástico descartável."
        )
    )

    assert result.extracted_fields.no_single_use_plastic is True
