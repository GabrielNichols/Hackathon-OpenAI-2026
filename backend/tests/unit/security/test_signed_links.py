from datetime import UTC, datetime, timedelta

import pytest

from app.contracts import ErrorCode
from app.infrastructure.security import (
    InMemoryNonceRegistry,
    SignedLinkError,
    SignedLinkPayload,
    SignedLinkService,
)

NOW = datetime(2026, 8, 19, 15, tzinfo=UTC)
SERVICE = SignedLinkService(b"test-secret-with-at-least-32-bytes!!")


def payload(**overrides: object) -> SignedLinkPayload:
    values: dict[str, object] = {
        "purpose": "supplier_profile_review",
        "subject_id": "sup_alpha",
        "recipient_id": "contact_alpha",
        "expires_at": NOW + timedelta(hours=1),
        "nonce": "nonce-1",
        "tenant_id": "org_demo",
    }
    values.update(overrides)
    return SignedLinkPayload.model_validate(values)


def verify(token: str, **overrides: object) -> SignedLinkPayload:
    values: dict[str, object] = {
        "expected_purpose": "supplier_profile_review",
        "expected_tenant_id": "org_demo",
        "now": NOW,
    }
    values.update(overrides)
    return SERVICE.verify(token, **values)


def test_signed_link_round_trip_binds_purpose_tenant_and_subject() -> None:
    token = SERVICE.issue(payload())
    decoded = verify(token, expected_subject_id="sup_alpha")
    assert decoded.expires_at == NOW + timedelta(hours=1)


def test_signed_link_rejects_expired_token() -> None:
    token = SERVICE.issue(payload(expires_at=NOW - timedelta(seconds=1)))
    with pytest.raises(SignedLinkError) as raised:
        verify(token)
    assert raised.value.code == ErrorCode.LINK_EXPIRED


def test_signed_link_rejects_wrong_purpose() -> None:
    token = SERVICE.issue(payload())
    with pytest.raises(SignedLinkError) as raised:
        verify(token, expected_purpose="rfq_response")
    assert raised.value.code == ErrorCode.LINK_INVALID


def test_signed_link_rejects_wrong_tenant_and_tampering() -> None:
    token = SERVICE.issue(payload())
    with pytest.raises(SignedLinkError):
        verify(token, expected_tenant_id="org_other")
    with pytest.raises(SignedLinkError):
        verify(f"x{token[1:]}")


def test_nonce_can_be_consumed_only_once() -> None:
    registry = InMemoryNonceRegistry()
    item = payload()
    registry.consume(item)
    with pytest.raises(SignedLinkError) as raised:
        registry.consume(item)
    assert raised.value.code == ErrorCode.LINK_INVALID
