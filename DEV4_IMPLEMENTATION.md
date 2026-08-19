# Dev 4 — protótipo operacional

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

O objetivo atual é provar as regras e o contrato de integração de ponta a
ponta. A persistência e as ações externas ainda são adapters de demonstração.

## O que está entregue no P0

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

## Limite explícito do fake

Este protótipo **não envia e-mail ou WhatsApp, não grava em banco e não reserva
capacidade em um sistema externo**.

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

Isso é intencional: os services recebem store, clock, token service e gateway
por injeção. Um adapter real pode substituí-los sem alterar cálculo, scoring ou
contratos.

## Instalação e testes

Execute na raiz deste worktree, com Python 3.12+:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
.\.venv\Scripts\python.exe -m pytest -q
```

Resultado esperado no estado atual:

```text
22 passed
```

## Demo por CLI

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

## Demo por API

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

Os endpoints individuais de RFQ, portal do fornecedor, aprovação e award são
P1. O P0 atual expõe o fluxo completo em uma única rota para reduzir risco de
integração durante o hackathon; cada etapa já existe separadamente no service.

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

Na primeira integração, o orchestrator do Dev 3 deve receber esse adapter no
lugar de `InMemoryRFQExecutionAdapter`. Não deve receber diretamente
`ProcurementExecutionService`, porque os DTOs dos dois lados têm papéis
distintos.

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

## Próximos itens P1

Ordem recomendada:

1. Adaptar o lifecycle e os eventos aos contratos finais do Dev 1.
2. Trocar o store por repositories SQLAlchemy e Unit of Work atômico.
3. Persistir audit log, outbox, idempotência, tokens e delivery acknowledgments.
4. Criar gateway real simples, com webhook/poll de ack e retry controlado.
5. Expor routers individuais para RFQ, quote response, comparison, approval,
   award response e timeline.
6. Adicionar autenticação real de comprador/aprovador e autorização por org.
7. Implementar draft, clarificação/uma rodada de negociação e anexos.
8. Implementar as telas mobile do fornecedor e comparação/aprovação do buyer.
9. Adicionar concorrência, partial delivery, stale approval e failure-path E2E.

Pagamento, contrato, ERP e negociação aberta continuam fora do MVP.
