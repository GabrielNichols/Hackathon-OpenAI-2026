"""Small reusable assertions for adapters implementing the core ports."""

from __future__ import annotations

from inspect import iscoroutinefunction
from typing import Any

from app.contracts import (
    AuditPort,
    PolicyPort,
    QuoteDecisionPort,
    RFQExecutionPort,
    SupplierDirectoryPort,
)

_ASYNC_METHODS: dict[type[Any], tuple[str, ...]] = {
    AuditPort: ("append",),
    PolicyPort: ("authorize",),
    SupplierDirectoryPort: ("search", "get"),
    RFQExecutionPort: ("create_round", "send_round", "get_status"),
    QuoteDecisionPort: (
        "compare",
        "run_negotiation",
        "request_approval",
        "send_award",
        "get_award_status",
    ),
}


def assert_core_port(implementation: object, port: type[Any]) -> None:
    """Assert structural compatibility and async boundaries for a core port."""

    methods = _ASYNC_METHODS.get(port)
    if methods is None:
        raise AssertionError(f"unsupported core port: {port!r}")
    if not isinstance(implementation, port):
        raise AssertionError(f"{implementation!r} does not implement {port.__name__}")
    for method_name in methods:
        method = getattr(implementation, method_name, None)
        if method is None or not iscoroutinefunction(method):
            raise AssertionError(f"{port.__name__}.{method_name} must be async")
