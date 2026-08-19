# Canal Agente — OpenAI Hackathon Brasil 2026

Implementação do fluxo de procurement agentic com execução verificável de
RFQ, propostas, comparação determinística, aprovação humana, award e reserva
de capacidade.

O Dev 4 já possui um modo `live` sem dependências fake: PostgreSQL, links
individuais enviados manualmente por um operador real, portais de fornecedor e
aprovação, comparação observável, auditoria, idempotência e criptografia do
estado persistido. O operador autenticado dispõe de uma página de evidência da
execução; o fornecedor vê todos os termos congelados antes de aceitar o award.

- [PRD](PRD_Canal_Agente_Procurement.md)
- [Plano de implementação do Dev 4](canal_agente_implementation_plans/04_RFQ_QUOTES_APPROVAL_AWARD_E2E_TDD.md)
- [Runbook da demo real](DEV4_LIVE_RUNBOOK.md)
- [Notas técnicas e contrato Dev 3 ↔ Dev 4](DEV4_IMPLEMENTATION.md)

Validação local:

```powershell
python -m pip install -e ".[dev]"
python -m pytest -q
python -m ruff check backend scripts
```

O antigo `app.dev4_demo` e `scripts/demo/run_dev4_prototype.py` são fixtures de
regressão explicitamente marcadas como `FAKE_DEMO`; não devem ser usados na
apresentação nem no deploy live.

O Dev 4 expõe o adapter durável esperado pelo Dev 3, mas a conexão com o
orchestrator é um **gate pós-merge**. Até os branches convergirem, valide o
runtime live do Dev 4 pelo E2E e não apresente a fixture fake como integração.
