from __future__ import annotations

import asyncio
import json

from app.modules.procurement_agent.demo import create_demo_container

MESSAGE = (
    "Preciso de um coffee break para 80 pessoas em 21/08/2026, entregue às 8h30 "
    "na Vila Olímpia, São Paulo. Serão 12 vegetarianos, 4 veganos e 3 sem glúten. "
    "Orçamento máximo de R$ 4.500, com nota fiscal obrigatória e sem plásticos "
    "descartáveis. Quero 3 cotações, respostas até 20/08/2026 às 18h. "
    "Aprovador: approver_demo."
)


async def main() -> None:
    container = create_demo_container()
    ready = await container.orchestrator.receive_message(MESSAGE)
    sourced = await container.orchestrator.confirm_plan(ready.request_id)
    summary = {
        "mode": sourced.mode,
        "request_id": sourced.request_id,
        "status": sourced.status,
        "stop_reason": sourced.stop_reason,
        "selected_supplier_ids": sourced.selected_supplier_ids,
        "rfq_round_id": sourced.rfq_round_id,
        "rfq_delivery_confirmed": False,
        "eligibility": {
            result.supplier_id: result.decision for result in sourced.eligibility_results
        },
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    asyncio.run(main())
