"""Stable test helpers shared by the feature branches."""

from .contract_kit import assert_core_port
from .fixtures import (
    FIXED_NOW,
    MAXIMUM_TOTAL_CENTS,
    PEOPLE_COUNT,
    TARGET_TOTAL_CENTS,
    FixtureIds,
    make_quote_comparison,
    make_supplier_candidates,
    make_supplier_search,
)

__all__ = [
    "FIXED_NOW",
    "MAXIMUM_TOTAL_CENTS",
    "PEOPLE_COUNT",
    "TARGET_TOTAL_CENTS",
    "FixtureIds",
    "assert_core_port",
    "make_quote_comparison",
    "make_supplier_candidates",
    "make_supplier_search",
]
