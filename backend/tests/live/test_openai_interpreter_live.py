from __future__ import annotations

import os
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from app.config import OpenAIInterpreterSettings
from app.modules.procurement_requests import OpenAIProcurementInterpreter

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_OPENAI_LIVE_TESTS") != "1",
    reason="Set RUN_OPENAI_LIVE_TESTS=1 to spend OpenAI API credits",
)


class LiveClock:
    def now(self) -> datetime:
        return datetime(2026, 8, 19, 12, 0, tzinfo=ZoneInfo("America/Sao_Paulo"))


async def test_real_openai_structured_procurement_extraction() -> None:
    interpreter = OpenAIProcurementInterpreter(
        LiveClock(),
        OpenAIInterpreterSettings.from_environment(),
    )

    result = await interpreter.interpret(
        "Preciso de coffee break para 25 pessoas no dia 30/08/2026 às 09:30, "
        "na Vila Olímpia, São Paulo. O orçamento máximo total é R$ 2.000. "
        "Nota fiscal é obrigatória e não pode ter plástico descartável. "
        "Propostas até 25/08/2026 às 18:00. Aprovador: buyer_manager."
    )

    patch = result.extracted_fields
    assert patch.category == "corporate_catering"
    assert patch.event_date.isoformat() == "2026-08-30"
    assert patch.people_count == 25
    assert patch.maximum_total_cents == 200_000
    assert patch.invoice_required is True
    assert patch.no_single_use_plastic is True
    assert result.provider_metadata is not None
    assert result.provider_metadata.provider == "openai"
    assert (result.provider_metadata.input_tokens or 0) > 0
    assert (result.provider_metadata.output_tokens or 0) > 0
