from decimal import Decimal

import pytest

from app.modules.suppliers.extraction.normalization import (
    AmbiguousValueError,
    map_dietary_capabilities,
    normalize_people_quantity,
    normalize_price_to_cents,
)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("R$ 42,50", 4_250),
        ("1.250,00", 125_000),
        (Decimal("19.90"), 1_990),
        (25, 2_500),
    ],
)
def test_price_is_normalized_to_integer_cents(raw: object, expected: int) -> None:
    assert normalize_price_to_cents(raw) == expected


def test_ambiguous_price_requires_review_instead_of_guessing() -> None:
    with pytest.raises(AmbiguousValueError):
        normalize_price_to_cents("pacotes de R$ 50 ou R$ 80")


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("a partir de 30 pessoas", 30),
        ("mínimo: 120 convidados", 120),
        (45, 45),
    ],
)
def test_people_quantity_is_normalized_deterministically(raw: object, expected: int) -> None:
    assert normalize_people_quantity(raw) == expected


def test_gluten_free_does_not_imply_no_cross_contamination() -> None:
    mapped = map_dietary_capabilities("Temos opções sem glúten")

    assert mapped["gluten_free_supported"] is True
    assert mapped["cross_contamination_warning"] is None


def test_explicit_cross_contamination_warning_is_preserved() -> None:
    mapped = map_dietary_capabilities(
        "Temos opções sem glúten, preparadas em cozinha com risco de contaminação cruzada"
    )

    assert mapped["gluten_free_supported"] is True
    assert mapped["cross_contamination_warning"] is True
