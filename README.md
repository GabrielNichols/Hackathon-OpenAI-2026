# Canal Agente — OpenAI Hackathon Brasil 2026

Fluxo agentic de procurement para transformar uma necessidade em linguagem
natural em sourcing verificável, RFQs reais, propostas comparáveis, aprovação
humana, award e reserva de capacidade.

## Arquitetura integrada

- **Dev 3 — Agent/Buyer Workflow:** interpreta o briefing com Structured
  Outputs da OpenAI (ou interpretador local), esclarece campos bloqueantes,
  aplica elegibilidade, seleciona fornecedores e orquestra o plano.
- **Dev 4 — Execution Workflow:** congela requisitos e policy, entrega RFQs,
  valida propostas, calcula score deterministicamente, registra aprovação,
  award, aceite, reserva e auditoria.
- **Runtime live:** PostgreSQL, estado cifrado, links individuais enviados por
  um operador real e portais server-rendered para fornecedor e aprovador.

No runtime live, `POST /api/v1/procurement-requests/messages` recebe o briefing
com autenticação Bearer. A confirmação do plano executa o sourcing do Dev 3,
cria a rodada no Dev 4 e prepara os links manuais de RFQ de forma idempotente.

## Instalação e validação

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m ruff check backend scripts
```

## Desenvolvimento local do fluxo comprador

```powershell
.\.venv\Scripts\python.exe -m uvicorn app.main:app --app-dir backend --reload
```

Abra [http://127.0.0.1:8000](http://127.0.0.1:8000). Por padrão, a
interpretação é local e não consome créditos. Para habilitar a Responses API:

```powershell
$env:PROCUREMENT_INTERPRETER='openai'
$env:OPENAI_PROCUREMENT_MODEL='gpt-5.6-luna'
.\.venv\Scripts\python.exe -m uvicorn app.main:app --app-dir backend --reload
```

A chave é lida de `OPENAI_API_KEY` ou, somente em desenvolvimento, de
`.secrets/openai_api_key.txt`. Nunca coloque a chave no frontend, em commits ou
em logs. O servidor local não possui autenticação suficiente para exposição
pública; mantenha-o em `127.0.0.1`.

## Demo live do fluxo de execução

O caminho oficial não usa auto-ack, auto-approval, respostas pré-fabricadas ou
persistência em memória. Consulte:

- [Runbook da demo real](DEV4_LIVE_RUNBOOK.md)
- [Notas do Dev 3](docs/DEV3_PROTOTYPE.md)
- [Contrato e notas do Dev 4](DEV4_IMPLEMENTATION.md)
- [PRD](PRD_Canal_Agente_Procurement.md)

O antigo `app.dev4_demo` e `scripts/demo/run_dev4_prototype.py` são fixtures de
regressão marcadas como `FAKE_DEMO`; não devem ser apresentados nem publicados
como execução real.

O teste pago da OpenAI é separado e opt-in:

```powershell
$env:RUN_OPENAI_LIVE_TESTS='1'
.\.venv\Scripts\python.exe -m pytest backend/tests/live -q
Remove-Item Env:RUN_OPENAI_LIVE_TESTS
```
