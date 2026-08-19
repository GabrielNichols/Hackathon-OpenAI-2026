from __future__ import annotations

import re
import unicodedata
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any


class AmbiguousValueError(ValueError):
    """Raised when deterministic normalization would require guessing."""


_NUMBER_PATTERN = re.compile(
    r"(?:R\$\s*)?(?<![\d.])(?:\d{1,3}(?:\.\d{3})+(?:,\d{1,2})?|\d+(?:[.,]\d{1,2})?)(?!\d)"
)


def _decimal_from_brazilian_number(raw: str) -> Decimal:
    cleaned = raw.casefold().replace("r$", "").replace(" ", "")
    if "," in cleaned:
        cleaned = cleaned.replace(".", "").replace(",", ".")
    try:
        return Decimal(cleaned)
    except InvalidOperation as error:
        raise ValueError(f"invalid monetary value: {raw!r}") from error


def normalize_price_to_cents(raw: Any) -> int:
    if isinstance(raw, bool):
        raise ValueError("boolean is not a monetary value")
    if isinstance(raw, Decimal):
        amount = raw
    elif isinstance(raw, (int, float)):
        amount = Decimal(str(raw))
    elif isinstance(raw, str):
        matches = _NUMBER_PATTERN.findall(raw)
        if not matches:
            raise ValueError("no monetary value found")
        if len(matches) > 1:
            raise AmbiguousValueError("more than one monetary value found")
        amount = _decimal_from_brazilian_number(matches[0])
    else:
        raise TypeError(f"unsupported monetary value type: {type(raw).__name__}")
    if amount < 0:
        raise ValueError("money cannot be negative")
    return int((amount * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def normalize_people_quantity(raw: Any) -> int:
    if isinstance(raw, bool):
        raise ValueError("boolean is not a people quantity")
    if isinstance(raw, int):
        quantity = raw
    elif isinstance(raw, str):
        matches = re.findall(r"(?<!\d)\d+(?!\d)", raw)
        if not matches:
            raise ValueError("no people quantity found")
        if len(matches) > 1:
            raise AmbiguousValueError("more than one people quantity found")
        quantity = int(matches[0])
    else:
        raise TypeError(f"unsupported people quantity type: {type(raw).__name__}")
    if quantity <= 0:
        raise ValueError("people quantity must be positive")
    return quantity


def _fold_text(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value.casefold())
    return "".join(character for character in decomposed if not unicodedata.combining(character))


def map_dietary_capabilities(raw: str) -> dict[str, bool | None]:
    text = _fold_text(raw)
    gluten_free = "sem gluten" in text or "gluten-free" in text
    cross_contamination = (
        "contaminacao cruzada" in text
        or "risco de contaminacao" in text
        or "cozinha compartilhada" in text
    )
    return {
        "vegetarian_supported": True if "vegetarian" in text else None,
        "vegan_supported": True if "vegan" in text else None,
        "gluten_free_supported": True if gluten_free else None,
        "cross_contamination_warning": True if cross_contamination else None,
    }
