from __future__ import annotations

import inspect

from app.contracts import (
    AuditPort,
    Clock,
    IdGenerator,
    PolicyPort,
    QuoteDecisionPort,
    RFQExecutionPort,
    SupplierDirectoryPort,
)


def test_ports_are_runtime_checkable_protocols() -> None:
    class FakeClock:
        def now(self):  # type: ignore[no-untyped-def]
            raise NotImplementedError

    class FakeIdGenerator:
        def new(self, prefix: str) -> str:
            return f"{prefix}_fixed"

    class FakeAudit:
        async def append(self, events):  # type: ignore[no-untyped-def]
            return None

    class FakePolicy:
        async def authorize(self, request):  # type: ignore[no-untyped-def]
            raise NotImplementedError

    assert isinstance(FakeClock(), Clock)
    assert isinstance(FakeIdGenerator(), IdGenerator)
    assert isinstance(FakeAudit(), AuditPort)
    assert isinstance(FakePolicy(), PolicyPort)


def test_async_port_methods_are_declared_async() -> None:
    async_methods = {
        SupplierDirectoryPort: {"search", "get"},
        RFQExecutionPort: {"create_round", "send_round", "get_status"},
        QuoteDecisionPort: {
            "compare",
            "run_negotiation",
            "request_approval",
            "send_award",
            "get_award_status",
        },
    }

    for port, method_names in async_methods.items():
        for method_name in method_names:
            assert inspect.iscoroutinefunction(getattr(port, method_name)), (
                f"{port.__name__}.{method_name} must be async"
            )


def test_quote_decision_port_is_the_frozen_superset() -> None:
    assert {
        "compare",
        "run_negotiation",
        "request_approval",
        "send_award",
        "get_award_status",
    }.issubset(set(dir(QuoteDecisionPort)))
