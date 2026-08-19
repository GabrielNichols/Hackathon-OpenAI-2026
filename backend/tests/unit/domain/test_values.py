from datetime import datetime

import pytest

from app.contracts import ErrorCode
from app.domain import DomainError
from app.domain.common import Money, require_utc


@pytest.mark.parametrize("invalid", [-1, 1.5, True, False])
def test_money_rejects_negative_float_and_bool(invalid: object) -> None:
    with pytest.raises(DomainError) as exc_info:
        Money(invalid)  # type: ignore[arg-type]

    assert exc_info.value.code is ErrorCode.VALIDATION_ERROR
    assert exc_info.value.details["field"] == "cents"


def test_money_accepts_zero_integer_cents() -> None:
    assert Money(0).cents == 0


def test_internal_datetime_requires_utc() -> None:
    with pytest.raises(DomainError) as exc_info:
        require_utc(datetime(2026, 8, 19, 12, 0), field="occurred_at")

    assert exc_info.value.code is ErrorCode.VALIDATION_ERROR
