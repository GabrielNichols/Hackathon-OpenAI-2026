from datetime import UTC, datetime, timedelta

import pytest

from app.modules.suppliers.application.core_compat import (
    FixedClock,
    ReviewTokenError,
    SignedReviewTokenService,
)


NOW = datetime(2026, 8, 19, 15, 0, tzinfo=UTC)


def token_service() -> SignedReviewTokenService:
    return SignedReviewTokenService(secret=b"test-only-secret", clock=FixedClock(NOW))


def issue_token(service: SignedReviewTokenService, **overrides: object) -> str:
    values: dict[str, object] = {
        "tenant_id": "org_demo",
        "supplier_id": "sup_alpha",
        "recipient_id": "contact_alpha",
        "expires_at": NOW + timedelta(hours=1),
        "nonce": "nonce_review_alpha",
    }
    values.update(overrides)
    return service.issue(**values)  # type: ignore[arg-type]


def test_review_token_is_bound_to_tenant_purpose_supplier_and_recipient() -> None:
    service = token_service()
    token = issue_token(service)

    claims = service.validate(
        token,
        expected_tenant_id="org_demo",
        expected_supplier_id="sup_alpha",
        expected_recipient_id="contact_alpha",
    )

    assert claims.purpose == "supplier_profile_review"
    assert claims.tenant_id == "org_demo"
    assert claims.supplier_id == "sup_alpha"
    assert claims.recipient_id == "contact_alpha"


@pytest.mark.parametrize(
    ("argument", "value"),
    [
        ("expected_tenant_id", "org_other"),
        ("expected_supplier_id", "sup_other"),
        ("expected_recipient_id", "contact_other"),
        ("expected_purpose", "rfq_response"),
    ],
)
def test_review_token_rejects_wrong_binding(argument: str, value: str) -> None:
    service = token_service()
    token = issue_token(service)

    with pytest.raises(ReviewTokenError, match="invalid") as error:
        service.validate(token, **{argument: value})

    assert error.value.code == "LINK_INVALID"


def test_review_token_rejects_expired_token() -> None:
    service = token_service()
    token = issue_token(service, expires_at=NOW)

    with pytest.raises(ReviewTokenError, match="expired") as error:
        service.validate(token)

    assert error.value.code == "LINK_EXPIRED"


def test_review_token_rejects_tampering() -> None:
    service = token_service()
    token = issue_token(service)
    payload, signature = token.split(".")
    tampered = f"{payload[:-1]}A.{signature}"

    with pytest.raises(ReviewTokenError) as error:
        service.validate(tampered)

    assert error.value.code == "LINK_INVALID"


def test_nonce_is_not_consumed_by_reads_or_field_decisions() -> None:
    service = token_service()
    token = issue_token(service)

    service.validate(token)
    service.validate(token)

    service.consume_for_submit(token)
    with pytest.raises(ReviewTokenError, match="used") as error:
        service.consume_for_submit(token)

    assert error.value.code == "LINK_INVALID"
