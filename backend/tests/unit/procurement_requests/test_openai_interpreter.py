from __future__ import annotations

import hashlib
import json
from datetime import date, datetime
from types import SimpleNamespace
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from openai import OpenAIError

from app.config import OpenAIInterpreterSettings
from app.modules.procurement_requests import (
    MAX_BUYER_MESSAGE_CHARS,
    FallbackProcurementInterpreter,
    InterpretationProviderError,
    LocalPortugueseProcurementInterpreter,
    OpenAIProcurementInterpreter,
    ProcurementRequestDraft,
)
from app.modules.procurement_requests.openai_interpreter import (
    OPENAI_EXTRACTION_SCHEMA_SHA256,
    OpenAIFieldEvidence,
    OpenAIProcurementExtraction,
)
from app.modules.procurement_requests.schemas import ProcurementRequestPatch


class FixedClock:
    def now(self) -> datetime:
        return datetime(2026, 8, 19, 12, 0, tzinfo=ZoneInfo("America/Sao_Paulo"))


class FakeResponses:
    def __init__(self, *, payload: OpenAIProcurementExtraction | None = None) -> None:
        self.payload = payload
        self.error: Exception | None = None
        self.arguments: dict[str, Any] = {}

    async def parse(self, **kwargs: Any) -> Any:
        self.arguments = kwargs
        if self.error is not None:
            raise self.error
        return SimpleNamespace(
            id="resp_test_123",
            model="gpt-5.6-luna-2026-08-01",
            status="completed",
            output_parsed=self.payload,
            usage=SimpleNamespace(input_tokens=123, output_tokens=45),
        )


class FakeClient:
    def __init__(self, responses: FakeResponses) -> None:
        self.responses = responses


def _settings() -> OpenAIInterpreterSettings:
    return OpenAIInterpreterSettings(api_key="secret-sentinel", model="gpt-5.6-luna")


async def test_openai_interpreter_uses_structured_responses_and_verified_evidence() -> None:
    message = (
        "Coffee break para 20 pessoas em 30/08/2026. Orçamento máximo total R$ 1.000. "
        "Nota fiscal não é obrigatória e teremos 0 veganos."
    )
    payload = OpenAIProcurementExtraction(
        extracted_fields=ProcurementRequestPatch(
            category="corporate_catering",
            event_date=date(2026, 8, 30),
            people_count=20,
            maximum_total_cents=100_000,
            currency="BRL",
            invoice_required=False,
            vegan_count=0,
        ),
        evidence=[
            OpenAIFieldEvidence(field="category", source_text="Coffee break", confidence=0.99),
            OpenAIFieldEvidence(field="event_date", source_text="30/08/2026", confidence=0.99),
            OpenAIFieldEvidence(field="people_count", source_text="20 pessoas", confidence=0.99),
            OpenAIFieldEvidence(
                field="maximum_total_cents",
                source_text="Orçamento máximo total R$ 1.000",
                confidence=0.99,
            ),
            OpenAIFieldEvidence(
                field="currency",
                source_text="Orçamento máximo total R$ 1.000",
                confidence=1.0,
            ),
            OpenAIFieldEvidence(
                field="invoice_required",
                source_text="Nota fiscal não é obrigatória",
                confidence=0.98,
            ),
            OpenAIFieldEvidence(field="vegan_count", source_text="0 veganos", confidence=0.99),
        ],
        ambiguities=[],
        assumptions=[],
    )
    responses = FakeResponses(payload=payload)
    interpreter = OpenAIProcurementInterpreter(
        FixedClock(),
        _settings(),
        client=FakeClient(responses),
    )

    result = await interpreter.interpret(message)

    assert result.extracted_fields.people_count == 20
    assert result.extracted_fields.maximum_total_cents == 100_000
    assert result.extracted_fields.invoice_required is False
    assert result.extracted_fields.vegan_count == 0
    assert result.extracted_fields.description == message
    assert result.provider_metadata is not None
    assert result.provider_metadata.provider == "openai"
    assert result.provider_metadata.input_tokens == 123
    canonical_input = json.dumps(
        {
            "buyer_message": message,
            "reference_datetime": "2026-08-19T12:00:00-03:00",
            "timezone": "America/Sao_Paulo",
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    canonical_output = json.dumps(
        payload.model_dump(mode="json"),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    assert (
        result.provider_metadata.input_sha256
        == hashlib.sha256(canonical_input.encode("utf-8")).hexdigest()
    )
    assert (
        result.provider_metadata.output_sha256
        == hashlib.sha256(canonical_output.encode("utf-8")).hexdigest()
    )
    canonical_schema = json.dumps(
        OpenAIProcurementExtraction.model_json_schema(),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    expected_schema_sha256 = hashlib.sha256(canonical_schema.encode("utf-8")).hexdigest()
    assert expected_schema_sha256 == OPENAI_EXTRACTION_SCHEMA_SHA256
    assert result.provider_metadata.schema_sha256 == expected_schema_sha256
    assert responses.arguments["store"] is False
    assert responses.arguments["input"] == canonical_input
    assert "current_request" not in json.loads(responses.arguments["input"])
    assert "self-contained" in responses.arguments["instructions"]
    assert "in isolation" in responses.arguments["instructions"]
    assert responses.arguments["text_format"] is OpenAIProcurementExtraction
    assert responses.arguments["reasoning"] == {"effort": "low"}
    assert "secret-sentinel" not in repr(responses.arguments)


async def test_unverified_model_fact_is_discarded() -> None:
    payload = OpenAIProcurementExtraction(
        extracted_fields=ProcurementRequestPatch(people_count=999),
        evidence=[
            OpenAIFieldEvidence(
                field="people_count",
                source_text="999 convidados",
                confidence=0.9,
            )
        ],
        ambiguities=[],
        assumptions=[],
    )
    interpreter = OpenAIProcurementInterpreter(
        FixedClock(),
        _settings(),
        client=FakeClient(FakeResponses(payload=payload)),
    )

    result = await interpreter.interpret("Coffee break para a equipe.")

    assert result.extracted_fields.people_count is None
    assert "UNVERIFIED_EVIDENCE_PEOPLE_COUNT" in result.ambiguities


async def test_literal_evidence_cannot_support_a_contradictory_model_value() -> None:
    message = "Coffee break para 90 pessoas. Nota fiscal não é obrigatória."
    payload = OpenAIProcurementExtraction(
        extracted_fields=ProcurementRequestPatch(
            people_count=900,
            invoice_required=True,
        ),
        evidence=[
            OpenAIFieldEvidence(field="people_count", source_text="90 pessoas", confidence=0.99),
            OpenAIFieldEvidence(
                field="invoice_required",
                source_text="Nota fiscal não é obrigatória",
                confidence=0.99,
            ),
        ],
        ambiguities=[],
        assumptions=[],
    )
    interpreter = OpenAIProcurementInterpreter(
        FixedClock(),
        _settings(),
        client=FakeClient(FakeResponses(payload=payload)),
    )

    result = await interpreter.interpret(message)

    assert result.extracted_fields.people_count is None
    assert result.extracted_fields.invoice_required is None
    assert "UNVERIFIED_VALUE_PEOPLE_COUNT" in result.ambiguities
    assert "UNVERIFIED_VALUE_INVOICE_REQUIRED" in result.ambiguities


async def test_irrelevant_excerpt_cannot_borrow_support_from_full_message() -> None:
    message = "Coffee break para 90 pessoas."
    payload = OpenAIProcurementExtraction(
        extracted_fields=ProcurementRequestPatch(people_count=90),
        evidence=[
            OpenAIFieldEvidence(
                field="people_count",
                source_text="Coffee break",
                confidence=0.99,
            )
        ],
        ambiguities=[],
        assumptions=[],
    )
    interpreter = OpenAIProcurementInterpreter(
        FixedClock(),
        _settings(),
        client=FakeClient(FakeResponses(payload=payload)),
    )

    result = await interpreter.interpret(message)

    assert result.extracted_fields.people_count is None
    assert "UNVERIFIED_VALUE_PEOPLE_COUNT" in result.ambiguities


async def test_one_character_text_does_not_pass_by_substring() -> None:
    message = "Aprovador: A."
    payload = OpenAIProcurementExtraction(
        extracted_fields=ProcurementRequestPatch(approver_user_id="A"),
        evidence=[
            OpenAIFieldEvidence(
                field="approver_user_id",
                source_text="A",
                confidence=0.99,
            )
        ],
        ambiguities=[],
        assumptions=[],
    )
    interpreter = OpenAIProcurementInterpreter(
        FixedClock(),
        _settings(),
        client=FakeClient(FakeResponses(payload=payload)),
    )

    result = await interpreter.interpret(message)

    assert result.extracted_fields.approver_user_id is None
    assert "UNVERIFIED_VALUE_APPROVER_USER_ID" in result.ambiguities


async def test_openai_candidate_conflict_cannot_overwrite_current_request() -> None:
    payload = OpenAIProcurementExtraction(
        extracted_fields=ProcurementRequestPatch(people_count=90),
        evidence=[
            OpenAIFieldEvidence(field="people_count", source_text="90 pessoas", confidence=0.99)
        ],
        ambiguities=[],
        assumptions=[],
    )
    interpreter = OpenAIProcurementInterpreter(
        FixedClock(),
        _settings(),
        client=FakeClient(FakeResponses(payload=payload)),
    )
    current = ProcurementRequestDraft(
        request_id="pr_test",
        description="Coffee break",
        people_count=80,
    )

    result = await interpreter.interpret("Agora serão 90 pessoas.", current)

    assert result.extracted_fields.people_count is None
    assert result.conflicts[0].current_value == 80
    assert result.conflicts[0].candidate_value == 90


async def test_current_request_is_not_sent_to_openai() -> None:
    responses = FakeResponses(
        payload=OpenAIProcurementExtraction(
            extracted_fields=ProcurementRequestPatch(),
            evidence=[],
            ambiguities=[],
            assumptions=[],
        )
    )
    interpreter = OpenAIProcurementInterpreter(
        FixedClock(),
        _settings(),
        client=FakeClient(responses),
    )
    current = ProcurementRequestDraft(
        request_id="pr_private",
        description="Mensagem anterior privada",
        people_count=80,
    )

    await interpreter.interpret("Apenas uma nova mensagem.", current)

    sent_payload = json.loads(responses.arguments["input"])
    assert set(sent_payload) == {"buyer_message", "reference_datetime", "timezone"}
    assert "pr_private" not in responses.arguments["input"]
    assert "Mensagem anterior privada" not in responses.arguments["input"]


async def test_buyer_message_and_evidence_share_the_same_size_limit() -> None:
    with pytest.raises(ValueError, match="at most 4000 characters"):
        OpenAIFieldEvidence(
            field="description",
            source_text="x" * (MAX_BUYER_MESSAGE_CHARS + 1),
            confidence=1.0,
        )

    responses = FakeResponses()
    interpreter = OpenAIProcurementInterpreter(
        FixedClock(),
        _settings(),
        client=FakeClient(responses),
    )
    with pytest.raises(ValueError, match="message cannot exceed 4000 characters"):
        await interpreter.interpret("x" * (MAX_BUYER_MESSAGE_CHARS + 1))
    assert responses.arguments == {}


async def test_provider_error_is_sanitized_and_can_fallback_locally() -> None:
    sentinel = "secret-sentinel-provider-message"
    responses = FakeResponses()
    responses.error = OpenAIError(sentinel)
    primary = OpenAIProcurementInterpreter(
        FixedClock(),
        _settings(),
        client=FakeClient(responses),
    )

    with pytest.raises(InterpretationProviderError) as caught:
        await primary.interpret("Coffee break para 10 pessoas.")
    assert str(caught.value) == "OPENAI_INTERPRETATION_FAILED"
    assert sentinel not in str(caught.value)

    resilient = FallbackProcurementInterpreter(
        primary,
        LocalPortugueseProcurementInterpreter(FixedClock()),
    )
    result = await resilient.interpret("Coffee break para 10 pessoas.")
    assert result.extracted_fields.people_count == 10
    assert result.provider_metadata is not None
    assert result.provider_metadata.provider == "local_fallback"
    assert result.provider_metadata.fallback_reason_code == "OPENAI_INTERPRETATION_FAILED"


async def test_programming_errors_are_not_hidden_by_provider_fallback() -> None:
    responses = FakeResponses()
    responses.error = RuntimeError("programming-bug")
    primary = OpenAIProcurementInterpreter(
        FixedClock(),
        _settings(),
        client=FakeClient(responses),
    )
    resilient = FallbackProcurementInterpreter(
        primary,
        LocalPortugueseProcurementInterpreter(FixedClock()),
    )

    with pytest.raises(RuntimeError, match="programming-bug"):
        await resilient.interpret("Coffee break para 10 pessoas.")
