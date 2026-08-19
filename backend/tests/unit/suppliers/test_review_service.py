import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from app.modules.suppliers.application.core_compat import (
    ActivateSupplierCommand,
    FakeAuditPort,
    FakeSupplierActivationCommandPort,
    FixedClock,
    SignedReviewTokenService,
    SupplierActivationResult,
    SupplierLifecycleStatus,
)
from app.modules.suppliers.application.review import (
    FieldReviewDecision,
    InMemorySupplierReviewRepository,
    ReviewConflictError,
    ReviewIncompleteError,
    SupplierReviewService,
    SupplierReviewSession,
)
from app.modules.suppliers.extraction.models import ExtractedFieldDTO, ExtractionFieldStatus

NOW = datetime(2026, 8, 19, 15, 0, tzinfo=UTC)


def extracted_field(
    field_name: str,
    value: object,
    *,
    normalized_value: object | None = None,
    version: int = 1,
) -> ExtractedFieldDTO:
    return ExtractedFieldDTO(
        field_name=field_name,
        value=value,
        normalized_value=value if normalized_value is None else normalized_value,
        status=ExtractionFieldStatus.EXTRACTED,
        confidence=0.91,
        source_document_id="doc_menu",
        source_page=2,
        source_excerpt=f"source for {field_name}",
        extraction_run_id="run_1",
        version=version,
    )


def build_service(
    *,
    required_fields: tuple[str, ...] = ("trade_name", "invoice_available"),
    activation_port: object | None = None,
) -> tuple[
    SupplierReviewService,
    InMemorySupplierReviewRepository,
    FakeSupplierActivationCommandPort,
    FakeAuditPort,
    str,
]:
    repository = InMemorySupplierReviewRepository()
    repository.add(
        SupplierReviewSession.from_extracted_fields(
            review_id="review_alpha",
            tenant_id="org_demo",
            supplier_id="sup_alpha",
            recipient_id="contact_alpha",
            required_fields=required_fields,
            fields=[
                extracted_field("trade_name", "Alpha Foods"),
                extracted_field("invoice_available", False),
                extracted_field("minimum_people", 20),
            ],
        )
    )
    clock = FixedClock(NOW)
    tokens = SignedReviewTokenService(secret=b"review-test-secret", clock=clock)
    token = tokens.issue(
        tenant_id="org_demo",
        supplier_id="sup_alpha",
        recipient_id="contact_alpha",
        expires_at=NOW + timedelta(hours=1),
        nonce="nonce_alpha",
    )
    fake_activation = FakeSupplierActivationCommandPort(clock=clock)
    audit = FakeAuditPort()
    service = SupplierReviewService(
        repository=repository,
        token_service=tokens,
        activation_port=activation_port or fake_activation,  # type: ignore[arg-type]
        audit_port=audit,
        clock=clock,
    )
    return service, repository, fake_activation, audit, token


@pytest.mark.asyncio
async def test_correction_creates_new_version_and_preserves_original() -> None:
    service, repository, _, _, token = build_service(required_fields=("minimum_people",))

    corrected = await service.correct_field(
        token,
        "minimum_people",
        value="30 pessoas",
        normalized_value=30,
        expected_version=1,
    )

    history = repository.get("review_alpha").field_history("minimum_people")
    assert [revision.version for revision in history] == [1, 2]
    assert history[0].value == 20
    assert history[0].source_document_id == "doc_menu"
    assert corrected.decision is FieldReviewDecision.CORRECTED
    assert corrected.value == "30 pessoas"
    assert corrected.normalized_value == 30
    assert corrected.source_document_id == "doc_menu"
    assert corrected.decided_by == "contact_alpha"
    assert corrected.decided_at == NOW


@pytest.mark.asyncio
async def test_confirmation_appends_version_without_mutating_extraction() -> None:
    service, repository, _, audit, token = build_service(required_fields=("trade_name",))

    confirmed = await service.confirm_field(token, "trade_name", expected_version=1)

    history = repository.get("review_alpha").field_history("trade_name")
    assert history[0].decision is None
    assert history[0].value == "Alpha Foods"
    assert confirmed.version == 2
    assert confirmed.decision is FieldReviewDecision.CONFIRMED
    assert confirmed.value == "Alpha Foods"
    assert [event.event_type for event in audit.events] == ["SUPPLIER_FIELD_CONFIRMED"]


@pytest.mark.asyncio
async def test_not_applicable_is_separate_decision_and_does_not_confirm_required_field() -> None:
    service, repository, _, _, token = build_service(required_fields=("invoice_available",))

    revision = await service.mark_not_applicable(
        token,
        "invoice_available",
        expected_version=1,
    )

    assert revision.decision is FieldReviewDecision.NOT_APPLICABLE
    assert revision.value is None
    assert repository.get("review_alpha").missing_required_fields() == ("invoice_available",)


@pytest.mark.asyncio
async def test_stale_field_version_is_rejected_without_appending_revision() -> None:
    service, repository, _, _, token = build_service(required_fields=("trade_name",))
    await service.confirm_field(token, "trade_name", expected_version=1)

    with pytest.raises(ReviewConflictError) as error:
        await service.correct_field(
            token,
            "trade_name",
            value="New name",
            normalized_value="New name",
            expected_version=1,
        )

    assert error.value.code == "OPTIMISTIC_LOCK_CONFLICT"
    assert len(repository.get("review_alpha").field_history("trade_name")) == 2


@pytest.mark.asyncio
async def test_incomplete_submit_is_blocked_without_consuming_nonce() -> None:
    service, _, activation, audit, token = build_service()
    await service.confirm_field(token, "trade_name", expected_version=1)

    with pytest.raises(ReviewIncompleteError) as error:
        await service.submit(token)

    assert error.value.missing_fields == ("invoice_available",)
    assert activation.commands == []
    assert audit.events[-1].event_type == "SUPPLIER_ACTIVATION_BLOCKED"

    await service.confirm_field(token, "invoice_available", expected_version=1)
    result = await service.submit(token)
    assert result.status is SupplierLifecycleStatus.ACTIVE
    assert len(activation.commands) == 1


class BlockingActivationPort:
    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.commands: list[ActivateSupplierCommand] = []

    async def activate(self, command: ActivateSupplierCommand) -> SupplierActivationResult:
        self.commands.append(command)
        self.started.set()
        await self.release.wait()
        return SupplierActivationResult(
            supplier_id=command.supplier_id,
            status=SupplierLifecycleStatus.ACTIVE,
            version=7,
            activated_at=NOW,
        )


@pytest.mark.asyncio
async def test_active_projection_changes_only_after_activation_command_returns() -> None:
    activation = BlockingActivationPort()
    service, repository, _, audit, token = build_service(
        required_fields=("trade_name",),
        activation_port=activation,
    )
    await service.confirm_field(token, "trade_name", expected_version=1)

    submission = asyncio.create_task(service.submit(token))
    await activation.started.wait()

    stored_while_core_is_pending = repository.get("review_alpha")
    assert stored_while_core_is_pending.projected_status is SupplierLifecycleStatus.CONFIRMED
    assert all(event.event_type != "SUPPLIER_ACTIVATED" for event in audit.events)

    activation.release.set()
    result = await submission

    assert result.status is SupplierLifecycleStatus.ACTIVE
    assert repository.get("review_alpha").projected_status is SupplierLifecycleStatus.ACTIVE
    assert activation.commands[0].review_submission_id == result.review_submission_id
    assert audit.events[-1].event_type == "SUPPLIER_ACTIVATED"
