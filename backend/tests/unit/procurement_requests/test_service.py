from datetime import date, datetime, time
from zoneinfo import ZoneInfo

import pytest
from app.modules.procurement_requests import (
    ProcurementPlanPatch,
    ProcurementPolicySnapshot,
    ProcurementRequestDraft,
    ProcurementRequestPatch,
    ProcurementRequestService,
    ProcurementRequestStatus,
)


class FixedClock:
    def __init__(self, value: datetime) -> None:
        self.value = value

    def now(self) -> datetime:
        return self.value


def _clock() -> FixedClock:
    return FixedClock(datetime(2026, 8, 19, 12, 0, tzinfo=ZoneInfo("America/Sao_Paulo")))


def _complete_draft(**changes: object) -> ProcurementRequestDraft:
    fields: dict[str, object] = {
        "request_id": "pr_demo",
        "description": "Coffee break corporativo",
        "category": "corporate_catering",
        "event_date": date(2026, 8, 28),
        "delivery_time": time(8, 30),
        "location_city": "São Paulo",
        "location_district": "Vila Olímpia",
        "people_count": 80,
        "maximum_total_cents": 450_000,
        "vegetarian_count": 12,
        "vegan_count": 4,
        "gluten_free_count": 3,
        "invoice_required": True,
        "no_single_use_plastic": True,
        "response_deadline": datetime(
            2026,
            8,
            20,
            18,
            tzinfo=ZoneInfo("America/Sao_Paulo"),
        ),
        "approver_user_id": "approver_demo",
    }
    fields.update(changes)
    return ProcurementRequestDraft.model_validate(fields)


def test_missing_location_requests_clarification_and_does_not_source() -> None:
    request = _complete_draft(location_district=None)
    service = ProcurementRequestService(clock=_clock())

    assessment = service.assess(request)

    assert assessment.status is ProcurementRequestStatus.NEEDS_CLARIFICATION
    assert assessment.can_start_sourcing is False
    assert assessment.missing_required_fields == ["location_district"]
    assert assessment.clarifications[0].reason_code == "MISSING_DELIVERY_LOCATION"


def test_complete_request_promotes_to_typed_ready_and_builds_default_plan() -> None:
    service = ProcurementRequestService(clock=_clock())

    assessment = service.assess(_complete_draft())

    assert assessment.status is ProcurementRequestStatus.READY
    assert assessment.can_start_sourcing is True
    assert assessment.ready_request is not None
    plan = service.default_plan(assessment.ready_request)
    assert plan.request_id == "pr_demo"
    assert plan.target_supplier_count == 3
    assert plan.negotiation_enabled is False
    assert plan.maximum_negotiation_rounds == 0
    assert plan.allowed_negotiation_topics == []
    assert plan.policy_snapshot.policy_id == "procurement_default_v1"
    assert "invoice_available" in plan.eliminatory_criteria


def test_dietary_counts_above_people_count_are_flagged_not_rejected_as_draft() -> None:
    request = _complete_draft(
        people_count=10,
        vegetarian_count=8,
        vegan_count=4,
    )

    assessment = ProcurementRequestService(clock=_clock()).assess(request)

    assert assessment.can_start_sourcing is False
    assert assessment.blocking_issues == ["DIETARY_COUNTS_EXCEED_PEOPLE_COUNT"]


def test_budget_can_be_optional_only_when_snapshotted_policy_says_so() -> None:
    policy = ProcurementPolicySnapshot(budget_is_eliminatory=False)
    assessment = ProcurementRequestService(clock=_clock()).assess(
        _complete_draft(maximum_total_cents=None),
        policy,
    )

    assert assessment.can_start_sourcing is True
    assert assessment.ready_request is not None
    assert assessment.ready_request.maximum_total_cents is None


def test_policy_optional_fields_remain_unknown_in_ready_request() -> None:
    policy = ProcurementPolicySnapshot(
        delivery_time_is_required=False,
        invoice_answer_is_required=False,
        plastic_answer_is_required=False,
    )
    assessment = ProcurementRequestService(clock=_clock()).assess(
        _complete_draft(
            delivery_time=None,
            invoice_required=None,
            no_single_use_plastic=None,
        ),
        policy,
    )

    assert assessment.can_start_sourcing is True
    assert assessment.ready_request is not None
    assert assessment.ready_request.delivery_time is None
    assert assessment.ready_request.invoice_required is None
    assert assessment.ready_request.no_single_use_plastic is None


def test_patch_preserves_existing_fact_without_explicit_confirmation() -> None:
    request = _complete_draft(people_count=80)
    patch = ProcurementRequestPatch(people_count=90)
    service = ProcurementRequestService(clock=_clock())

    preserved = service.apply_patch(request, patch)
    confirmed = service.apply_patch(
        request,
        patch,
        allow_overwrite_fields={"people_count"},
    )

    assert preserved.people_count == 80
    assert preserved.version == request.version
    assert confirmed.people_count == 90
    assert confirmed.version == request.version + 1


def test_patch_does_not_turn_omitted_defaults_into_confirmed_facts() -> None:
    initial = ProcurementRequestDraft(request_id="pr_partial")
    service = ProcurementRequestService(clock=_clock())

    after_people = service.apply_patch(initial, ProcurementRequestPatch(people_count=80))
    after_diet = service.apply_patch(after_people, ProcurementRequestPatch(vegan_count=4))

    assert "vegan_count" not in after_people.model_fields_set
    assert after_diet.vegan_count == 4
    assert after_diet.version == 3


def test_negotiation_defaults_are_copied_from_immutable_policy_snapshot() -> None:
    policy = ProcurementPolicySnapshot(
        negotiation_enabled=True,
        target_budget_percent=90,
    )
    service = ProcurementRequestService(clock=_clock(), default_policy=policy)
    ready = service.to_ready(_complete_draft(), policy)

    plan = service.default_plan(ready, policy)

    assert plan.negotiation_enabled is True
    assert plan.target_total_cents == 405_000
    assert plan.maximum_negotiation_rounds == 2
    assert plan.allowed_negotiation_topics == list(policy.allowed_negotiation_topics)


def test_plan_update_cannot_bypass_frozen_policy_or_remove_eliminatory_rules() -> None:
    service = ProcurementRequestService(clock=_clock())
    ready = service.to_ready(_complete_draft())
    plan = service.default_plan(ready)

    with pytest.raises(ValueError, match="negotiation cannot be enabled"):
        service.update_plan(plan, ProcurementPlanPatch(negotiation_enabled=True))

    with pytest.raises(ValueError, match="eliminatory criteria cannot be removed"):
        service.update_plan(plan, ProcurementPlanPatch(eliminatory_criteria=[]))


def test_plan_update_limits_rounds_topics_and_followups_to_policy_snapshot() -> None:
    policy = ProcurementPolicySnapshot(negotiation_enabled=True)
    service = ProcurementRequestService(clock=_clock(), default_policy=policy)
    plan = service.default_plan(service.to_ready(_complete_draft(), policy), policy)

    with pytest.raises(ValueError, match="maximum_negotiation_rounds"):
        service.update_plan(
            plan,
            ProcurementPlanPatch(maximum_negotiation_rounds=3),
        )
    with pytest.raises(ValueError, match="allowed_negotiation_topics"):
        service.update_plan(
            plan,
            ProcurementPlanPatch(allowed_negotiation_topics=["supplier_identity"]),
        )
    with pytest.raises(ValueError, match="maximum_follow_ups"):
        service.update_plan(plan, ProcurementPlanPatch(maximum_follow_ups=3))
