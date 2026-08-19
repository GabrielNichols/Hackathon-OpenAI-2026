# Dev 4 — notas técnicas

> **O modo oficial da apresentação agora é real.** Consulte
> [`DEV4_LIVE_RUNBOOK.md`](DEV4_LIVE_RUNBOOK.md). As seções de fake abaixo
> documentam apenas a fixture histórica de regressão `FAKE_DEMO`; ela não deve
> ser publicada, apresentada nem conectada ao Dev 3.

O runtime live está em `app.live.entrypoint` e usa PostgreSQL, estado cifrado,
gateway de link manual auditado, autenticação humana e portais reais. O teste
`test_live_real_e2e.py` cobre o fluxo completo e restart sem adapters fake.

## P0 live oficial

- SQLAlchemy/Unit of Work com estado, idempotência e auditoria duráveis;
- capabilities individuais e gateway de envio manual com ações humanas
  registradas sem transformar envio em entrega;
- portais server-rendered de fornecedor e aprovador, com HTTPS, autenticação,
  CSRF action-bound e transições apenas em POST explícito;
- propostas versionadas, clarificação e regras eliminatórias executadas no
  servidor;
- comparação determinística observável em
  `GET /live/operator/comparisons/{comparison_id}`, autenticada, tenant-scoped
  e somente leitura;
- matriz de comparação com elegibilidade, valores, requisitos, riscos,
  evidências, score agregado e componentes do score;
- tela de award que exibe o snapshot congelado completo por allowlist tipada:
  proposta/versão, fornecedor, total/moeda, itens, substituições, cancelamento,
  data, janela e pessoas;
- aceite explícito vinculado ao hash visível desse snapshot e reserva em uma
  ação separada;
- evidência consolidada em
  `GET /live/operator/runs/{procurement_request_id}`, autenticada, tenant-scoped
  e somente leitura;
- event log append-only com envelope auditável. Eventos novos registram
  `origin`; transições registram `previous_state` e `new_state`; o contexto
  preserva `agent_run_id` e `idempotency_key` quando aplicáveis. Campos que não
  se aplicam permanecem nulos para manter compatibilidade com eventos antigos.

## Fixture histórica de regressão

Este worktree entrega um corte vertical executável do Plano 04:

```text
RFQ imutável
→ entrega confirmada
→ duas cotações validadas
→ comparação determinística
→ aprovação humana
→ award entregue e aceito
→ capacidade reservada
→ READY_FOR_CONTRACTING
```

Essa fixture histórica prova regras de domínio de forma rápida. Somente nela a
persistência e as ações externas são adapters de demonstração; isso não
descreve o runtime live oficial acima.

## O que a fixture histórica cobre

- DTOs Pydantic versionados no boundary Dev 3 ↔ Dev 4;
- snapshot imutável de requisitos e policy, com hashes;
- idempotência com detecção de reutilização conflitante;
- optimistic-version checks nos comandos que alteram workflow;
- um recipient e um link HMAC isolado por fornecedor;
- gateway de entrega fake com ack separado de aceitação;
- recálculo de `subtotal + taxas = total` no servidor;
- validação de expiração, disponibilidade, orçamento, NF e dietas;
- preço por pessoa calculado com inteiros;
- score determinístico em basis points, com componentes auditáveis;
- aprovação restrita a ator humano e à versão exata da quote;
- award vinculado à aprovação e à quote aprovada;
- aceite por token, seguido de reserva idempotente;
- timeline de eventos até `PROCUREMENT_READY_FOR_CONTRACTING`;
- API mínima com health check e execução canônica;
- testes de domínio, integração e API.

## Limite explícito da fixture fake

O runner histórico **não envia e-mail ou WhatsApp, não grava em banco e não
reserva capacidade em um sistema externo**. Essas limitações são do
`FAKE_DEMO`, não do runtime `app.live.entrypoint`.

- `InMemoryExecutionStore` perde o estado ao terminar o processo.
- `FakeDeliveryGateway` simula o boundary do provedor e registra mensagens na
  memória.
- A demo canônica usa `auto_ack=True`; portanto, o próprio fake confirma as
  entregas. Os testes usam também `auto_ack=False` para provar que o estado não
  avança antes do ack.
- O relógio da demo é fixo em `2026-08-19T15:00:00Z`; `executed_at` representa o
  relógio do cenário, não o horário real da execução.
- A resposta da API declara `mode: FAKE_DEMO` e
  `simulated_external_actions: true`.
- Tokens de fornecedor não aparecem na resposta HTTP nem na timeline.

Isso é intencional: a fixture preserva um teste de regressão rápido. A demo
oficial usa os adapters duráveis descritos no runbook.

## Instalação e testes

Execute na raiz deste worktree, com Python 3.12+:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
.\.venv\Scripts\python.exe -m pytest -q
```

O conjunto inclui testes unitários, adversariais, persistência, rotas e um E2E
real com restart. O comando deve terminar integralmente verde; não congele a
documentação em uma contagem específica enquanto os dois branches convergem.

## Runner CLI da fixture (nunca usar como demo live)

Resumo verificado do fluxo:

```powershell
.\.venv\Scripts\python.exe scripts\demo\run_dev4_prototype.py
```

Com timeline completa:

```powershell
.\.venv\Scripts\python.exe scripts\demo\run_dev4_prototype.py --timeline
```

Payload completo em JSON:

```powershell
.\.venv\Scripts\python.exe scripts\demo\run_dev4_prototype.py --json
```

O script termina com código diferente de zero se o modo fake deixar de estar
explícito, se um componente de score não fechar, se faltar um evento material
ou se o processo não chegar a `READY_FOR_CONTRACTING`.

## API da fixture (nunca usar como demo live)

Inicie o servidor:

```powershell
.\.venv\Scripts\python.exe -m uvicorn app.dev4_demo:app `
  --host 127.0.0.1 --port 8000
```

Em outro terminal:

```powershell
Invoke-RestMethod http://127.0.0.1:8000/health

Invoke-RestMethod `
  -Method Post `
  -Uri http://127.0.0.1:8000/api/v1/demo/run |
  ConvertTo-Json -Depth 20
```

Documentação interativa: `http://127.0.0.1:8000/docs`.

Superfície HTTP atual:

| Método | Rota | Uso |
|---|---|---|
| `GET` | `/health` | readiness mínima do protótipo |
| `POST` | `/api/v1/demo/run` | executa uma instância nova do cenário fake |

Essa superfície HTTP pertence somente ao `FAKE_DEMO`. Os portais e as rotas
individuais reais são servidos por `app.live.entrypoint`; consulte o runbook.

## Contrato Dev 3 ↔ Dev 4

Fonte: `app.modules.rfq.contracts`, versão
`dev3-dev4.v0` em cada `CommandContextDTO`.

Os dois protótipos nasceram com DTOs agent-facing e execution-facing
diferentes. A conexão atual é propositalmente feita por
`app.modules.rfq.dev3_adapter.Dev3RFQExecutionAdapter`, que:

- preserva `tenant_id` e rejeita ator anônimo;
- converte os snapshots abertos do Dev 3 nos snapshots tipados do Dev 4;
- resolve timezone e cidade com defaults explícitos;
- normaliza os pesos `total_price`, `mandatory_requirements` e
  `response_time` para os critérios do scorer;
- devolve somente os campos aceitos pelo `RFQRoundDTO` estrito do Dev 3;
- considera retries com novo run/correlation como a mesma operação, mas ainda
  rejeita mudança de recipients, requirements ou policy.

Na primeira integração, **depois do merge**, o orchestrator do Dev 3 deve
receber esse adapter no lugar de `InMemoryRFQExecutionAdapter`. Não deve
receber diretamente `ProcurementExecutionService`, porque os DTOs dos dois
lados têm papéis distintos. Até esse gate ser concluído, o E2E live valida o
port durável do Dev 4 sem alegar que o orchestrator do Dev 3 já está conectado.

Dev 3 depende de `RFQExecutionPort`:

```python
await execution.create_round(CreateRFQRoundCommand(...))
await execution.send_round(SendRFQRoundCommand(...))
await execution.get_delivery_status(rfq_round_id)
await execution.get_quote_status(rfq_round_id)
```

E de `QuoteDecisionPort`:

```python
await decisions.compare(CompareQuotesCommand(...))
await decisions.request_approval(RequestApprovalCommand(...))
await decisions.get_approval_status(approval_id)
await decisions.send_award(SendAwardCommand(...))
await decisions.get_award_status(award_id)
```

Regras do handoff:

1. Dev 3 envia `requirements` e `execution_policy` completos; Dev 4 congela os
   snapshots e não consulta objetos mutáveis do Dev 3.
2. Todo comando traz `idempotency_key`, `correlation_id`, actor e versões
   esperadas.
3. Aceitar `send_round` não significa entrega. Dev 3 só apresenta `RFQ_ACTIVE`
   quando `DeliveryBatchDTO.activation_criteria_met` for verdadeiro após ack.
4. O modelo não escolhe a proposta nem muda estado. Validação, score,
   elegibilidade e transições são determinísticos.
5. Dev 3 não acessa tokens de fornecedor. Links pertencem ao gateway/portal do
   Dev 4.
6. Aprovação, aceite e reserva são eventos materiais; não devem ser inferidos de
   texto do agente.

No merge, o adapter deve ser injetado no orchestrator. O Dev 3 não deve criar
um `InMemoryExecutionStore` nem importar a implementação concreta.

## Próximos itens após o P0 live

O P0 live já possui SQLAlchemy/UoW, lock concorrente por tenant, estado cifrado,
idempotência, auditoria, links manuais reais, portais, autenticação,
clarificação versionada, comparação/evidência observáveis e E2E de restart.
Depois de integrar o Dev 3, a ordem recomendada é:

1. substituir o bootstrap `create_all` por migrations Alembic;
2. adicionar rate limit distribuído e sessão web em vez de HTTP Basic;
3. adicionar integração oficial de e-mail/WhatsApp mantendo ack verificável;
4. adaptar lifecycle/eventos aos contratos finais do Dev 1;
5. ampliar negotiation topics e anexos sem sair da policy;
6. normalizar as tabelas do snapshot caso o produto avance além do hackathon.

Pagamento, contrato, ERP e negociação aberta continuam fora do MVP.
