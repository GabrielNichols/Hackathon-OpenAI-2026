# Implementation Plan 04 — RFQ, Cotações, Negociação, Aprovação, Award e E2E

**Projeto:** Canal Agente  
**Responsável:** Dev 4 — Procurement Execution/Integration  
**Branch:** `feat/rfq-quotes-approval-award-e2e`  
**PR base:** `main`, desenvolver contra fakes e rebasedar nos contratos do Dev 1 e adapters dos Devs 2–3 antes do merge  
**Missão:** executar a metade material do procurement: entregar RFQs, receber propostas reais, normalizar, negociar dentro de regras, aprovar, enviar award e registrar aceite/reserva.  
**Resultado esperado:** a demo termina em `READY_FOR_CONTRACTING` por eventos reais, não em uma recomendação simulada.

---

## 1. Contexto essencial do produto

Esta branch é responsável pelo resultado que os avaliadores conseguem verificar:

```text
RFQ criada
→ mensagem/link realmente entregue
→ fornecedor responde
→ cotação validada e versionada
→ propostas comparadas
→ negociação/esclarecimento auditado
→ pessoa aprova
→ award realmente entregue
→ fornecedor aceita
→ capacidade/data reservada
→ pronto para contratação
```

Pagamento, contrato, ERP e emissão fiscal ficam fora do MVP.

---

## 2. Limites da branch

### Esta branch deve implementar

- criação e versionamento de rodada de RFQ;
- recipients individuais;
- template estruturado e igual para todos;
- gateway de entrega real P0 e fake para testes;
- outbox consumer e delivery acknowledgment;
- links individuais de resposta;
- portal mobile de cotação;
- anexos opcionais;
- submissão e validação de quote;
- cálculo determinístico de subtotal, total e preço por pessoa;
- clarificação de respostas ambíguas;
- rodadas de negociação versionadas e limitadas;
- comparação e score determinístico;
- recomendação baseada no score, com fatos e riscos separados;
- aprovação humana;
- envio de award vinculado à versão exata da quote;
- rejeição/encerramento dos demais fornecedores;
- aceite do fornecedor;
- reserva de data/capacidade;
- telas de comparação, aprovação, award e supplier response;
- integração final de routers e frontend routes;
- testes E2E da prova central;
- seed/demo script e fallback de replay identificado.

### Esta branch não deve implementar

- parsing do briefing do comprador;
- onboarding ou extração de fornecedores;
- criação de suppliers;
- alteração das máquinas de estado centrais;
- pagamento;
- assinatura de contrato;
- homologação corporativa completa;
- negociação irrestrita;
- alegação automática de economia.

---

## 3. Estrutura e propriedade de arquivos

```text
backend/app/modules/rfq/
backend/app/modules/messaging/
backend/app/modules/quotes/
backend/app/modules/negotiation/
backend/app/modules/comparison/
backend/app/modules/approvals/
backend/app/modules/awards/
backend/app/modules/reservations/
frontend/src/features/supplier-rfq-response/
frontend/src/features/quote-comparison/
frontend/src/features/approval/
frontend/src/features/award-status/
backend/tests/unit/rfq/
backend/tests/unit/quotes/
backend/tests/contract/rfq/
backend/tests/integration/rfq/
tests/e2e/
scripts/demo/
```

Dev 4 também realiza o wiring final, mas não deve mover ou reescrever módulos dos outros devs. Integre por routers, route exports e ports.

---

## 4. Rodada de RFQ

### 4.1 Snapshot imutável

Uma rodada deve congelar:

```text
procurement_request_id
request_version
requirements_snapshot
policy_snapshot
recipient_supplier_ids
response_deadline
created_by_agent_run_id
created_at
version
```

Mudança de requisito cria nova rodada/version, não altera RFQ já entregue.

### 4.2 Recipient

Cada `RFQRecipient` contém:

```text
recipient_id
rfq_round_id
supplier_id
supplier_contact_id
channel
signed_response_token_id
delivery_status
delivery_external_id
delivered_at
opened_at
response_status
follow_up_count
```

### 4.3 Estados de entrega

```text
PENDING
→ QUEUED
→ SENT_TO_GATEWAY
→ DELIVERED | FAILED
```

`RFQ_ACTIVE` no processo só ocorre depois de ao menos uma entrega confirmada conforme policy. Aceitação do gateway sem ack não é suficiente.

---

## 5. Gateway de mensagens

### Ports

```python
class DeliveryGateway(Protocol):
    async def send(self, message: OutboundMessage) -> GatewaySendResult: ...
    async def get_status(self, external_id: str) -> GatewayDeliveryStatus: ...
```

Implementações:

- `FakeDeliveryGateway` para testes;
- um adapter real de e-mail ou canal configurado por ambiente;
- `ManualLinkDeliveryAdapter` somente quando a interface registra explicitamente quem copiou/enviou o link e não o apresenta como envio automático.

### Regras

- idempotency key por recipient e versão;
- retry com backoff controlado;
- sem segredos em logs;
- opt-out e horário comercial;
- não duplicar mensagem;
- falha parcial não marca todos como entregues;
- delivery event deve guardar external ID e timestamp.

---

## 6. Portal de resposta de RFQ

### APIs

```text
GET  /api/v1/rfq-response/{token}
POST /api/v1/rfq-response/{token}/open
POST /api/v1/rfq-response/{token}/draft
POST /api/v1/rfq-response/{token}/submit
POST /api/v1/rfq-response/{token}/attachments
GET  /api/v1/rfq-response/{token}/status
```

### Campos P0 da cotação

```text
availability_confirmed
subtotal_cents
delivery_fee_cents
other_fee_cents
total_cents
included_items[]
substitutions[]
invoice_available
vegetarian_status
vegan_status
gluten_free_status
cross_contamination_warning
valid_until
cancellation_terms
respondent_name
respondent_contact
supplier_confirmation
```

### UX

- mobile-first;
- briefing visível e não editável;
- resposta salva como rascunho;
- total calculado na tela e servidor;
- alertas de campos faltantes;
- aviso explícito de validade e confirmação;
- suporte a “não consigo atender” com motivo;
- fornecedor não vê concorrentes.

---

## 7. Validação e normalização

### Funções determinísticas

```text
calculate_quote_total
calculate_price_per_person
validate_required_quote_fields
validate_budget_limit
validate_requirement_coverage
validate_quote_expiration
compare_profile_and_quote_claims
```

### Regras

- dinheiro em centavos;
- servidor recalcula totais;
- divergência entre subtotal + taxas e total retorna erro;
- cotação expirada nunca é elegível;
- invoice `unknown` não atende invoice obrigatória;
- dieta “parcial” não atende requisito obrigatório sem aprovação explícita;
- contradição com perfil cadastrado gera `NEEDS_CLARIFICATION` e não sobrescreve o perfil;
- cotação válida referencia o respondente real e timestamp.

---

## 8. Negociação limitada

### Política consumida do Dev 1/Dev 3

```yaml
negotiation:
  enabled: true
  target_total_price_cents: 410000
  maximum_total_price_cents: 450000
  maximum_rounds: 2
  allowed_topics:
    - total_price
    - delivery_fee
    - included_items
    - payment_term
  forbidden_actions:
    - invent_competing_offer
    - disclose_other_supplier_identity
    - commit_without_approval
    - change_mandatory_requirements
```

### Modelo

Cada rodada guarda:

```text
negotiation_round_id
quote_id
quote_version_before
initiated_by
message_sent
allowed_topic
requested_change
supplier_response
quote_version_after
created_at
```

A IA pode redigir a mensagem; o domain service valida tópico, limite, estado e quantidade de rodadas antes do envio.

### MVP demonstrável

Uma rodada de esclarecimento ou negociação é suficiente. Não crie leilão autônomo complexo.

---

## 9. Comparação e score

### 9.1 Matriz

Colunas mínimas:

- fornecedor;
- elegibilidade;
- total;
- preço por pessoa;
- entrega;
- itens incluídos;
- restrições;
- NF;
- sustentabilidade;
- validade;
- resposta;
- pendências;
- evidências.

### 9.2 Score determinístico

Pesos iniciais:

```text
preço total                 35
restrições                  20
adequação                   15
logística                   10
prazo de resposta            5
sustentabilidade             5
documentação                 5
histórico                    5
```

O resultado deve guardar componentes, pesos e fórmula. Empates usam regra estável, por exemplo:

1. mais requisitos obrigatórios atendidos;
2. menor total;
3. resposta mais recente/rápida;
4. `supplier_id` somente como desempate técnico final.

A IA explica o score, mas não o altera.

### 9.3 Separação de conteúdo

A recomendação deve conter seções distintas:

```text
Fatos confirmados
Cálculo determinístico
Interpretação do agente
Riscos e pendências
```

---

## 10. Aprovação

### APIs

```text
POST /api/v1/procurement-requests/{request_id}/approval-requests
GET  /api/v1/approvals/{approval_id}
POST /api/v1/approvals/{approval_id}/approve
POST /api/v1/approvals/{approval_id}/reject
POST /api/v1/approvals/{approval_id}/request-changes
```

### Regras

- aprovador deve ser usuário humano autorizado;
- agente não pode aprovar;
- decisão registra quote ID + versão;
- alteração posterior da quote invalida aprovação ou exige nova versão;
- rejeição deve manter motivo;
- aprovação é idempotente;
- auditoria guarda actor e timestamp.

---

## 11. Award e aceite

### 11.1 Award

```text
award_id
procurement_request_id
supplier_id
approved_quote_id
approved_quote_version
approved_total_cents
terms_snapshot
approval_id
status
sent_at
```

### Regras

- só após aprovação válida;
- vinculado à versão exata;
- enviado por outbox/gateway;
- `AWARD_SENT` apenas após delivery ack;
- demais fornecedores recebem fechamento apenas se policy permitir.

### 11.2 Aceite

Portal por token de uso controlado:

```text
GET  /api/v1/award-response/{token}
POST /api/v1/award-response/{token}/accept
POST /api/v1/award-response/{token}/decline
```

Aceite contém:

- identidade do respondente;
- termos exibidos;
- hash do snapshot;
- timestamp;
- confirmação explícita;
- capacidade/data reservada.

### 11.3 Reserva

```text
reservation_id
supplier_id
procurement_request_id
event_date
delivery_window
people_count
capacity_status
confirmed_by
confirmed_at
expires_at | null
```

`READY_FOR_CONTRACTING` exige award aceito e reserva confirmada.

---

## 12. Eventos desta branch

```text
RFQ_ROUND_CREATED
RFQ_RECIPIENT_CREATED
RFQ_DELIVERY_QUEUED
RFQ_DELIVERY_CONFIRMED
RFQ_DELIVERY_FAILED
RFQ_OPENED
QUOTE_DRAFT_SAVED
QUOTE_SUBMITTED
QUOTE_VALIDATION_FAILED
QUOTE_NEEDS_CLARIFICATION
QUOTE_VALIDATED
NEGOTIATION_ROUND_CREATED
NEGOTIATION_MESSAGE_DELIVERED
NEGOTIATION_RESPONSE_RECEIVED
QUOTE_FINALIZED
QUOTE_COMPARISON_CREATED
APPROVAL_REQUESTED
APPROVAL_GRANTED
APPROVAL_REJECTED
AWARD_CREATED
AWARD_DELIVERY_CONFIRMED
AWARD_DELIVERY_FAILED
SUPPLIER_ACCEPTED_AWARD
SUPPLIER_DECLINED_AWARD
CAPACITY_RESERVED
PROCUREMENT_READY_FOR_CONTRACTING
```

---

## 13. Contratos entregues ao Dev 3

Implementar:

- `RFQExecutionPort`
- `QuoteDecisionPort`

### Operações adicionais internas

```python
class QuoteDecisionPort(Protocol):
    async def compare(self, procurement_request_id: str) -> QuoteComparisonDTO: ...
    async def run_negotiation(self, command: NegotiationCommand) -> NegotiationResultDTO: ...
    async def request_approval(self, command: RequestApprovalCommand) -> ApprovalDTO: ...
    async def send_award(self, command: SendAwardCommand) -> AwardDTO: ...
    async def get_award_status(self, award_id: str) -> AwardDTO: ...
```

Todos os comandos externos exigem idempotency key.

---

## 14. Estratégia TDD acelerada

### Regra

Comece com gateways fake e clocks/IDs fixos. Nenhum teste de negócio deve depender do provedor real de mensagem.

### Camadas

#### Unitários

- cálculo de total;
- validação de campos;
- expiração;
- score;
- limites de negociação;
- approval binding;
- award binding;
- reserva.

#### Contract

- `DeliveryGateway`;
- `RFQExecutionPort`;
- `QuoteDecisionPort`;
- tokens;
- DTOs de comparação.

#### Integração

- outbox → gateway → ack → event/state;
- response token → quote submission;
- approval → award;
- acceptance → reservation.

#### E2E

- fornecedor ativo e requisição pronta via fixtures/adapters;
- RFQ entregue;
- duas respostas;
- uma clarificação/negociação;
- comparação;
- aprovação;
- award;
- aceite e reserva;
- timeline auditável.

### Testes obrigatórios

```text
test_rfq_round_keeps_immutable_request_snapshot
test_same_idempotency_key_does_not_create_second_round
test_rfq_is_not_marked_delivered_without_gateway_ack
test_partial_delivery_does_not_mark_all_recipients_delivered
test_retry_does_not_send_duplicate_message
test_response_token_is_bound_to_single_supplier_and_rfq
test_expired_response_token_is_rejected
test_supplier_cannot_view_competitor_data
test_quote_total_is_recalculated_server_side
test_quote_rejects_total_mismatch
test_quote_requires_availability_price_validity_and_respondent
test_expired_quote_is_not_eligible
test_profile_contradiction_requests_clarification
test_negotiation_rejects_forbidden_topic
test_negotiation_stops_after_maximum_rounds
test_negotiation_never_discloses_competitor_identity
test_score_is_deterministic_for_same_inputs
test_score_components_sum_to_final_score
test_ineligible_quote_cannot_win_automatically
test_agent_cannot_approve_its_own_recommendation
test_approval_is_bound_to_quote_version
test_quote_change_invalidates_previous_approval
test_award_cannot_be_created_without_approval
test_award_is_not_sent_without_delivery_ack
test_award_acceptance_requires_real_token_submission
test_capacity_reservation_is_required_for_ready_for_contracting
test_repeated_acceptance_is_idempotent
test_full_procurement_happy_path_reaches_ready_for_contracting
```

---

## 15. Teste E2E canônico

Criar um cenário automatizado com IDs fixos:

```text
org_demo
supplier_alpha — elegível, R$ 4.200
supplier_beta — elegível, R$ 4.350
supplier_gamma — excluído por NF
pr_demo_coffee_break — 80 pessoas, Vila Olímpia, teto R$ 4.500
```

Fluxo:

1. Dev 2 adapter retorna fornecedores ativos.
2. Dev 3 cria requisição e seleciona Alpha/Beta.
3. Dev 4 cria e entrega RFQ via fake gateway.
4. Alpha e Beta submetem quotes por tokens distintos.
5. Alpha precisa esclarecer taxa de entrega ou restrição.
6. Quotes ficam `FINAL`.
7. Comparação gera score reproduzível.
8. Humano aprova a versão vencedora.
9. Award é entregue.
10. Fornecedor aceita e reserva 80 pessoas na data.
11. Processo chega a `READY_FOR_CONTRACTING`.
12. Audit log permite reconstruir todos os passos.

Esse cenário deve rodar sem LLM real. Um segundo smoke test pode usar adapters reais configurados.

---

## 16. UI e wiring final

### Telas P0 desta branch

- resposta de RFQ no celular;
- status da rodada;
- comparação de propostas;
- detalhe com evidência;
- aprovação/rejeição;
- status do award;
- aceite/reserva do fornecedor;
- timeline final.

### Integração final

Dev 4 deve:

- registrar routers exportados pelos quatro módulos;
- montar navegação buyer/supplier;
- conectar real providers por environment;
- preservar fakes somente em test/dev explícito;
- criar health check de dependências;
- não ocultar falhas externas com estado otimista falso.

---

## 17. Script de demo

Criar comandos reproduzíveis:

```text
scripts/demo/reset_demo
scripts/demo/seed_suppliers
scripts/demo/create_request
scripts/demo/run_fake_e2e
scripts/demo/export_audit_timeline
```

E uma configuração `DEMO_MODE` que:

- usa fornecedores e documentos autorizados;
- pode reduzir latência visual;
- não inventa delivery, response, approval ou acceptance;
- identifica replay com timestamp quando utilizado;
- nunca troca provider real por fake sem indicador visível.

O fallback de palco deve carregar replay de execução real previamente persistida, não gerar respostas artificiais no momento.

---

## 18. Uso de multiagents pelo Dev 4

### Subagente A — RFQ/Messaging

- cria tests, outbox consumer, gateway fake/real e delivery states;
- não toca comparação.

### Subagente B — Quote/Negotiation

- implementa portal, quote validation, normalização e versões;
- não toca approval/award.

### Subagente C — Scoring/Approval/Award

- implementa score, approval binding, award e reservation;
- trabalha contra fakes de RFQ.

### Subagente D — Frontend/E2E

- implementa telas, route wiring e Playwright;
- usa mock server até backend estabilizar.

### Subagente E — Failure/Adversarial Reviewer

- testa duplicação, token leakage, stale approval, partial delivery, expired quote e retry;
- adiciona regressões sem redesenhar o produto.

### Coordenação

- A, B e C trabalham em bounded contexts separados;
- D cria page objects e contract mocks desde o início;
- E revisa cada boundary antes do E2E;
- líder faz wiring final após rebase dos Devs 1–3.

---

## 19. Plano de execução por waves

### Wave A — Contracts e fakes

- implementar ports do Dev 1;
- fake gateway;
- fake supplier/request adapters;
- testes de contract.

### Wave B — RFQ e delivery

- round/recipient;
- outbox;
- adapter real;
- status e idempotência.

### Wave C — Quote portal

- token;
- draft/submit;
- cálculos;
- validation/clarification;
- anexos P1.

### Wave D — Negotiation e comparison

- rounds versionadas;
- policy checks;
- scorecard;
- recommendation view.

### Wave E — Approval, award e reservation

- human approval;
- version binding;
- delivery;
- acceptance;
- ready for contracting.

### Wave F — Integration e E2E

- rebase;
- adapters reais;
- routers/routes;
- happy path;
- failure paths;
- demo scripts.

---

## 20. Critérios de aceite da branch

- [ ] RFQ preserva snapshot imutável.
- [ ] Entrega só é confirmada por ack real do gateway.
- [ ] Retry não duplica RFQ, mensagem ou award.
- [ ] Cada fornecedor recebe token isolado.
- [ ] Portal mobile coleta todos os campos P0.
- [ ] Totais são recalculados no servidor.
- [ ] Quote inválida ou expirada não entra como elegível.
- [ ] Negociação respeita tópicos e rodadas.
- [ ] Score é determinístico e explicável.
- [ ] Aprovação é humana e ligada à versão da quote.
- [ ] Award referencia exatamente os termos aprovados.
- [ ] Aceite e reserva vêm de submissão real.
- [ ] Processo só chega a `READY_FOR_CONTRACTING` com todos os eventos.
- [ ] E2E canônico roda em comando único.
- [ ] Wiring final não quebra testes dos outros módulos.
- [ ] Demo mode não apresenta fake como real.
- [ ] Testes, lint, tipos e build frontend passam.

---

## 21. Commits sugeridos

```text
test(rfq): freeze execution and delivery contracts
feat(rfq): add immutable rounds, recipients and outbox delivery
feat(messaging): add idempotent delivery gateway and acknowledgements
feat(quotes): add secure mobile response flow
feat(quotes): add deterministic validation and normalization
test(quotes): cover expiration, contradictions and isolation
feat(negotiation): add policy-bound versioned rounds
feat(comparison): add deterministic scorecard and evidence matrix
feat(approval): bind human decision to quote version
feat(award): add idempotent delivery, acceptance and reservation
test(e2e): cover full path to ready for contracting
feat(ui): add quote comparison, approval and supplier response flows
chore(integration): wire module routers and demo configuration
docs(demo): add reproducible runbook and replay rules
```

---

## 22. Merge e integração

Ordem recomendada de merge:

1. Dev 1 — contracts/core;
2. Dev 2 — supplier onboarding/directory;
3. Dev 3 — request/agent/sourcing;
4. Dev 4 — RFQ/quotes/integration/E2E.

O desenvolvimento do Dev 4 não precisa esperar os merges: use fakes que implementem os mesmos ports. Antes do PR final:

- rebase em `main` atualizado;
- substituir fakes de integração por adapters reais;
- manter fakes apenas em testes;
- rodar contract tests de todos os ports;
- rodar suíte completa e E2E;
- resolver conflitos preservando ownership, sem copiar lógica entre módulos.

---

## 23. Crítica da frente

### Ponto positivo

Esta frente entrega a prova mais forte do hackathon: ação externa, proposta real, decisão humana e aceite do fornecedor com reserva verificável.

### Ponto negativo

É também a frente com maior risco de integração e dependência de serviços externos. Se ela tentar implementar WhatsApp oficial, pagamentos e automação completa ao mesmo tempo, o projeto provavelmente não fecha ponta a ponta.

### Decisão de escopo

Use um canal real simples para entrega, um único fluxo de negociação e uma única reserva. A nota vem da autenticidade e auditabilidade do resultado, não da quantidade de integrações.
