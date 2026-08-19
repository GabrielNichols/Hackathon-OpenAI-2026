from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from app.config import (
    OpenAIInterpreterSettings,
    load_interpreter_provider,
    load_openai_api_key,
)
from app.modules.buyer_timeline.audit import InMemoryAuditLog
from app.modules.procurement_agent.adapters import (
    FixedClock,
    InMemoryAgentRunRepository,
    InMemoryRFQExecutionAdapter,
    PrototypePolicy,
    UUIDIdGenerator,
)
from app.modules.procurement_agent.orchestrator import ProcurementAgentOrchestrator
from app.modules.procurement_agent.sourcing_tools import InMemorySupplierDirectory
from app.modules.procurement_agent.workflow import InMemoryProcurementProcessRepository
from app.modules.procurement_requests.interpreter import LocalPortugueseProcurementInterpreter
from app.modules.procurement_requests.openai_interpreter import (
    FallbackProcurementInterpreter,
    OpenAIProcurementInterpreter,
)
from app.modules.procurement_requests.ports import ProcurementInterpretationPort
from app.modules.procurement_requests.service import ProcurementRequestService
from app.modules.sourcing.eligibility import SupplierEligibilityEngine
from app.modules.sourcing.models import SupplierCandidateDTO


@dataclass(frozen=True, slots=True)
class DemoContainer:
    orchestrator: ProcurementAgentOrchestrator
    interpreter: ProcurementInterpretationPort
    directory: InMemorySupplierDirectory
    rfq: InMemoryRFQExecutionAdapter
    audit: InMemoryAuditLog
    mode: str = "demo_fake"


def create_demo_container(
    *,
    max_steps: int = 16,
    suppliers: list[SupplierCandidateDTO] | None = None,
    denied_actions: set[str] | None = None,
    openai_settings: OpenAIInterpreterSettings | None = None,
    openai_fallback: bool = False,
) -> DemoContainer:
    """Build a fully offline, deterministic composition root for the prototype."""

    clock = FixedClock(datetime(2026, 8, 19, 15, 0, tzinfo=UTC))
    ids = UUIDIdGenerator()
    audit = InMemoryAuditLog()
    policy = PrototypePolicy(denied_actions=denied_actions)
    request_service = ProcurementRequestService(clock=clock)
    local_interpreter = LocalPortugueseProcurementInterpreter(
        clock,
        service=request_service,
    )
    interpreter: ProcurementInterpretationPort = local_interpreter
    mode = "demo_fake"
    if openai_settings is not None:
        openai_interpreter = OpenAIProcurementInterpreter(
            clock,
            openai_settings,
            service=request_service,
        )
        interpreter = (
            FallbackProcurementInterpreter(openai_interpreter, local_interpreter)
            if openai_fallback
            else openai_interpreter
        )
        mode = "demo_openai_with_local_fallback" if openai_fallback else "demo_openai_interpreter"
    directory = InMemorySupplierDirectory(
        _demo_suppliers(clock.now()) if suppliers is None else suppliers
    )
    rfq = InMemoryRFQExecutionAdapter(clock=clock, ids=ids)
    orchestrator = ProcurementAgentOrchestrator(
        requests=InMemoryProcurementProcessRepository(),
        request_service=request_service,
        interpreter=interpreter,
        directory=directory,
        eligibility=SupplierEligibilityEngine(max_profile_age=timedelta(days=90)),
        policy=policy,
        rfq=rfq,
        audit=audit,
        runs=InMemoryAgentRunRepository(),
        clock=clock,
        ids=ids,
        max_steps=max_steps,
        mode=mode,
    )
    return DemoContainer(
        orchestrator=orchestrator,
        interpreter=interpreter,
        directory=directory,
        rfq=rfq,
        audit=audit,
        mode=mode,
    )


def create_runtime_container() -> DemoContainer:
    """Compose the API from environment without making offline tests spend credits."""

    provider = load_interpreter_provider()
    if provider == "local":
        return create_demo_container()
    if provider == "auto" and load_openai_api_key() is None:
        return create_demo_container()
    settings = OpenAIInterpreterSettings.from_environment()
    return create_demo_container(
        openai_settings=settings,
        openai_fallback=provider == "auto",
    )


def _demo_suppliers(now: datetime) -> list[SupplierCandidateDTO]:
    common = {
        "status": "ACTIVE",
        "categories": ["corporate_catering"],
        "minimum_people": 20,
        "maximum_people": 200,
        "lead_time_hours": 24,
        "invoice_available": True,
        "dietary_capabilities": {
            "vegetarian": "confirmed",
            "vegan": "confirmed",
            "gluten_free": "confirmed",
        },
        "sustainability_tags": ["no_single_use_plastic"],
        "last_confirmed_at": now - timedelta(days=10),
        "missing_fields": [],
    }
    return [
        SupplierCandidateDTO(
            supplier_id="sup_alpha",
            display_name="Cozinha Alpha",
            service_areas=["São Paulo"],
            evidence_refs=["ev_alpha_profile_v3"],
            **common,
        ),
        SupplierCandidateDTO(
            supplier_id="sup_beta",
            display_name="Mesa Beta Eventos",
            service_areas=["Vila Olímpia", "Pinheiros"],
            evidence_refs=["ev_beta_profile_v2"],
            **common,
        ),
        SupplierCandidateDTO(
            supplier_id="sup_sem_nf",
            display_name="Sabor de Bairro",
            service_areas=["São Paulo"],
            evidence_refs=["ev_sem_nf_profile_v1"],
            **{**common, "invoice_available": False},
        ),
        SupplierCandidateDTO(
            supplier_id="sup_fora_area",
            display_name="Campinas Coffee Co.",
            service_areas=["Campinas"],
            evidence_refs=["ev_fora_area_profile_v4"],
            **common,
        ),
        SupplierCandidateDTO(
            supplier_id="sup_atualizar",
            display_name="Verde & Grão",
            service_areas=["São Paulo"],
            evidence_refs=["ev_atualizar_profile_v1"],
            **{
                **common,
                "dietary_capabilities": {
                    "vegetarian": "confirmed",
                    "gluten_free": "confirmed",
                },
                "missing_fields": ["vegan_supported"],
            },
        ),
    ]


__all__ = ["DemoContainer", "create_demo_container", "create_runtime_container"]
