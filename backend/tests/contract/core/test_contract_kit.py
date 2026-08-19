from app.contracts import RFQExecutionPort, SupplierDirectoryPort
from app.platform.clock import FixedClock
from app.platform.fakes import FakeRFQExecutionPort, FakeSupplierDirectory
from app.platform.ids import SequenceIdGenerator
from app.testing import (
    FIXED_NOW,
    MAXIMUM_TOTAL_CENTS,
    PEOPLE_COUNT,
    TARGET_TOTAL_CENTS,
    FixtureIds,
    assert_core_port,
    make_quote_comparison,
    make_supplier_candidates,
    make_supplier_search,
)


def test_shared_fixture_values_are_frozen_for_feature_branches() -> None:
    ids = FixtureIds()
    assert FIXED_NOW.isoformat() == "2026-08-19T15:00:00+00:00"
    assert PEOPLE_COUNT == 80
    assert MAXIMUM_TOTAL_CENTS == 450_000
    assert TARGET_TOTAL_CENTS == 410_000
    assert ids.supplier_alpha == "sup_alpha"
    assert make_supplier_search().tenant_id == ids.org_demo
    assert make_quote_comparison().recommended_quote_id == ids.quote_alpha_v1


def test_contract_kit_accepts_published_supplier_and_rfq_fakes() -> None:
    assert_core_port(FakeSupplierDirectory(make_supplier_candidates()), SupplierDirectoryPort)
    assert_core_port(
        FakeRFQExecutionPort(FixedClock(FIXED_NOW), SequenceIdGenerator()),
        RFQExecutionPort,
    )
