"""Run and verify the canonical Dev 4 prototype from the command line."""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from backend.app.main import DemoRunResponse, run_canonical_demo  # noqa: E402
from backend.app.shared.errors import DomainError  # noqa: E402

REQUIRED_MATERIAL_EVENTS = (
    "RFQ_DELIVERY_CONFIRMED",
    "QUOTE_SUBMITTED",
    "QUOTE_COMPARISON_CREATED",
    "APPROVAL_GRANTED",
    "AWARD_DELIVERY_CONFIRMED",
    "SUPPLIER_ACCEPTED_AWARD",
    "CAPACITY_RESERVED",
    "PROCUREMENT_READY_FOR_CONTRACTING",
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run the deterministic Dev 4 vertical slice. External delivery and "
            "reservation are explicitly simulated."
        )
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="print the complete Pydantic response as JSON",
    )
    parser.add_argument(
        "--timeline",
        action="store_true",
        help="include every audit event in the human-readable output",
    )
    return parser.parse_args()


async def run(args: argparse.Namespace) -> int:
    result = await run_canonical_demo()
    _verify(result)

    if args.json:
        print(result.model_dump_json(indent=2))
        return 0

    _print_summary(result)
    if args.timeline:
        _print_timeline(result)
    return 0


def _verify(result: DemoRunResponse) -> None:
    if result.mode != "FAKE_DEMO" or result.simulated_external_actions is not True:
        raise RuntimeError("demo must identify all external actions as simulated")
    if result.status != "READY_FOR_CONTRACTING" or not result.ready_for_contracting:
        raise RuntimeError(f"unexpected final procurement status: {result.status}")
    if not result.delivery.activation_criteria_met:
        raise RuntimeError("RFQ activation criteria were not met")
    if result.approval.status != "APPROVED":
        raise RuntimeError(f"approval is not final: {result.approval.status}")
    if result.award.status != "ACCEPTED":
        raise RuntimeError(f"award is not accepted: {result.award.status}")
    if result.award.reservation_status != "CONFIRMED":
        raise RuntimeError(
            f"capacity reservation is not confirmed: {result.award.reservation_status}"
        )

    for candidate in result.comparison.candidates:
        component_total = sum(
            component.points_basis_points for component in candidate.score_components
        )
        if candidate.score_basis_points != component_total:
            raise RuntimeError(f"score components do not sum for quote {candidate.quote_id}")

    event_types = {event.event_type for event in result.timeline}
    missing_events = [
        event_type for event_type in REQUIRED_MATERIAL_EVENTS if event_type not in event_types
    ]
    if missing_events:
        raise RuntimeError(
            "audit timeline is missing material events: " + ", ".join(missing_events)
        )


def _print_summary(result: DemoRunResponse) -> None:
    selected_ref = result.comparison.recommended_quote
    if selected_ref is None:
        raise RuntimeError("comparison has no recommended quote")
    selected = next(
        candidate
        for candidate in result.comparison.candidates
        if candidate.quote_id == selected_ref.quote_id
        and candidate.quote_version == selected_ref.quote_version
    )

    print("Canal Agente - Dev 4 prototype")
    print("MODE: FAKE_DEMO (delivery and capacity reservation are simulated)")
    print(f"Scenario clock: {result.executed_at.isoformat()}")
    print(f"Procurement request: {result.procurement_request_id}")
    print(
        "RFQ deliveries: "
        f"{result.delivery.confirmed_count}/{len(result.delivery.deliveries)} confirmed"
    )
    print("Quotes:")
    candidates = {item.quote_id: item for item in result.comparison.candidates}
    for quote in result.quotes:
        candidate = candidates[quote.quote_id]
        print(
            f"  - {quote.supplier_id}: {_format_brl(quote.total_cents)}, "
            f"score={candidate.score_basis_points} bp, eligible={candidate.eligible}"
        )
    print(
        f"Recommended: {selected.supplier_id} "
        f"({_format_brl(selected.total_cents)}, {selected.score_basis_points} bp)"
    )
    print(f"Human approval: {result.approval.status}")
    print(f"Award: {result.award.status}; reservation={result.award.reservation_status}")
    print(f"Final status: {result.status}")
    print(f"Audit events: {len(result.timeline)}")
    print("Verification: PASS")


def _print_timeline(result: DemoRunResponse) -> None:
    print("Timeline:")
    for index, event in enumerate(result.timeline, start=1):
        print(
            f"  {index:02d}. {event.event_type} "
            f"[{event.aggregate_type}:{event.aggregate_id}] "
            f"actor={event.actor_type}:{event.actor_id}"
        )


def _format_brl(cents: int) -> str:
    reais, centavos = divmod(cents, 100)
    grouped = f"{reais:,}".replace(",", ".")
    return f"R$ {grouped},{centavos:02d}"


def main() -> int:
    args = parse_args()
    try:
        return asyncio.run(run(args))
    except DomainError as error:
        print(f"Domain failure: {error}", file=sys.stderr)
        return 2
    except RuntimeError as error:
        print(f"Verification failure: {error}", file=sys.stderr)
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
