import pytest

from app.contracts import ErrorCode, SupplierState
from app.domain import DomainError
from app.domain.suppliers import REQUIRED_SUPPLIER_FIELDS, SupplierAggregate


def _confirmed_supplier(*, confirmed_fields: frozenset[str]) -> SupplierAggregate:
    supplier = SupplierAggregate.create(supplier_id="sup_alpha", tenant_id="org_demo")
    supplier.record_materials_uploaded(document_id="doc_menu")
    supplier.record_extraction(extraction_id="ext_menu")
    supplier.request_review(review_id="review_alpha")
    supplier.confirm_review(
        submission_id="submission_alpha",
        confirmed_fields=confirmed_fields,
    )
    return supplier


def test_supplier_cannot_skip_from_draft_to_active() -> None:
    supplier = SupplierAggregate.create(supplier_id="sup_alpha", tenant_id="org_demo")
    version_before = supplier.version
    events_before = supplier.pending_events

    with pytest.raises(DomainError) as exc_info:
        supplier.activate()

    assert exc_info.value.code is ErrorCode.INVALID_STATE_TRANSITION
    assert supplier.state is SupplierState.DRAFT
    assert supplier.version == version_before
    assert supplier.pending_events == events_before


def test_supplier_can_activate_only_with_confirmed_required_fields() -> None:
    supplier = _confirmed_supplier(
        confirmed_fields=REQUIRED_SUPPLIER_FIELDS - {"contact"},
    )
    version_before = supplier.version

    with pytest.raises(DomainError) as exc_info:
        supplier.activate()

    assert exc_info.value.code is ErrorCode.VALIDATION_ERROR
    assert exc_info.value.details["missing_fields"] == ["contact"]
    assert supplier.state is SupplierState.CONFIRMED
    assert supplier.version == version_before

    complete = _confirmed_supplier(confirmed_fields=REQUIRED_SUPPLIER_FIELDS)
    complete.activate()
    assert complete.state is SupplierState.ACTIVE


def test_supplier_review_confirmation_requires_real_submission() -> None:
    supplier = SupplierAggregate.create(supplier_id="sup_alpha", tenant_id="org_demo")
    supplier.record_materials_uploaded(document_id="doc_menu")
    supplier.record_extraction(extraction_id="ext_menu")
    supplier.request_review(review_id="review_alpha")

    with pytest.raises(DomainError) as exc_info:
        supplier.confirm_review(
            submission_id="",
            confirmed_fields=REQUIRED_SUPPLIER_FIELDS,
        )

    assert exc_info.value.code is ErrorCode.VALIDATION_ERROR
    assert supplier.state is SupplierState.AWAITING_SUPPLIER_REVIEW


def test_supplier_transition_increments_version_and_emits_one_event() -> None:
    supplier = SupplierAggregate.create(supplier_id="sup_alpha", tenant_id="org_demo")
    supplier.pull_events()
    version_before = supplier.version

    supplier.record_materials_uploaded(document_id="doc_menu")

    assert supplier.version == version_before + 1
    assert len(supplier.pending_events) == 1
    event = supplier.pending_events[0]
    assert event.previous_state == SupplierState.DRAFT.value
    assert event.new_state == SupplierState.MATERIALS_UPLOADED.value
    assert event.aggregate_version == supplier.version
