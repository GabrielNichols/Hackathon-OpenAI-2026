# Implementation Plan 03 — Agente de Procurement, Requisição e Sourcing

**Projeto:** Canal Agente  
**Responsável:** Dev 3 — Agent/Buyer Workflow  
**Branch:** `feat/procurement-agent-sourcing`  
**PR base:** `main`, rebasedar no `contracts-v0` do Dev 1  
**Missão:** colocar o agente dentro da plataforma para interpretar a necessidade do comprador, pedir clarificações, construir um plano, buscar fornecedores e orquestrar ferramentas autorizadas.  
**Resultado esperado:** uma requisição em linguagem natural vira um processo estruturado e auditável; o agente age somente por tools tipadas, para em checkpoints humanos e nunca inventa execução.

---

## 1. Contexto essencial do produto

O agente não é um chatbot decorativo nem uma API para agentes externos. Ele é o operador nativo da plataforma.

No MVP, deve conduzir:

```text
mensagem do comprador
→ interpretação estruturada
→ clarificação bloqueante
→ plano de procurement
→ sourcing
→ elegibilidade explicável
→ criação/disparo de RFQ via port do Dev 4
→ acompanhamento
→ comparação/recomendação via port do Dev 4
→ checkpoint de aprovação humana
```

Esta branch não implementa o ciclo de cotação em si; implementa o cérebro orquestrador e a experiência do comprador até os handoffs tipados.

---

## 2. Limites da branch

### Esta branch deve implementar

- criação de requisição em texto livre;
- schema estruturado de procurement;
- interpretação por provider de IA;
- fake provider roteirizado para testes;
- detecção de campos faltantes;
- perguntas de clarificação objetivas;
- plano de procurement editável;
- policy snapshot por processo;
- sourcing contra `SupplierDirectoryPort`;
- filtros determinísticos de elegibilidade;
- explicação de inclusão/exclusão;
- orquestrador do agente;
- tool registry interno e allowlist por estado;
- execução com correlation/agent-run IDs;
- checkpoints humanos;
- buyer UI: chat, resumo estruturado, plano e sourcing;
- ports/adapters para chamar RFQ/comparison/approval do Dev 4;
- timeline de ações do agente baseada no audit log.

### Esta branch não deve implementar

- upload e extração de fornecedores;
- persistência de dados de supplier fora do contrato;
- envio de mensagens a fornecedores;
- portal de resposta de RFQ;
- cálculo final de quotes;
- scorecard final;
- persistência de aprovação/award;
- pagamento;
- MCP ou API pública para agentes externos.

---

## 3. Estrutura e propriedade de arquivos

```text
backend/app/modules/procurement_requests/
backend/app/modules/procurement_agent/
backend/app/modules/sourcing/
backend/app/modules/buyer_timeline/
frontend/src/features/buyer-request/
frontend/src/features/procurement-agent/
frontend/src/features/sourcing/
backend/tests/unit/procurement_agent/
backend/tests/unit/sourcing/
backend/tests/contract/procurement_agent/
backend/tests/integration/procurement_agent/
```

Não altere contratos compartilhados sem proposta ao Dev 1. Não altere módulos de supplier ou RFQ diretamente. Use ports.

Não edite router central. Exporte routers e route definitions para integração final pelo Dev 4.

---

## 4. Schema da requisição

```python
class ProcurementRequestInput(BaseModel):
    category: Literal["corporate_catering"]
    description: str
    event_date: date | None
    delivery_time: time | None
    location_city: str | None
    location_district: str | None
    full_address: str | None
    people_count: int | None
    maximum_total_cents: int | None
    currency: Literal["BRL"] = "BRL"
    vegetarian_count: int = 0
    vegan_count: int = 0
    gluten_free_count: int = 0
    invoice_required: bool | None
    no_single_use_plastic: bool | None
    response_deadline: datetime | None
    desired_quote_count: int | None
    approver_user_id: str | None
```

### Campos bloqueantes P0

- categoria;
- data;
- região/endereço suficiente para entrega;
- quantidade de pessoas;
- prazo de resposta;
- aprovador;
- orçamento, quando configurado como eliminatório;
- requisitos obrigatórios explicitamente conhecidos.

O agente não deve iniciar sourcing com dados bloqueantes ausentes.

---

## 5. Provider de interpretação

```python
class ProcurementInterpretationPort(Protocol):
    async def interpret(
        self,
        message: str,
        current_request: ProcurementRequestInput | None,
    ) -> ProcurementInterpretationResult: ...
```

Saída:

```python
class ProcurementInterpretationResult(BaseModel):
    extracted_fields: ProcurementRequestInput
    evidence: dict[str, str]
    ambiguities: list[str]
    assumptions: list[str]
    missing_required_fields: list[str]
    confidence_by_field: dict[str, float]
```

### Regras

- datas relativas devem ser resolvidas usando clock injetado;
- assumptions nunca viram requisitos confirmados silenciosamente;
- orçamento deve ser normalizado para centavos;
- contagens não podem exceder `people_count` sem alerta;
- local ausente gera clarificação;
- informação conflitante entre mensagens preserva histórico e solicita confirmação;
- provider não chama tools nem persiste estado.

---

## 6. Clarificação

O clarificador deve ser determinístico sobre quais campos bloqueiam. A IA pode redigir a pergunta, mas não decidir se o campo é obrigatório.

Exemplo:

```text
Campo ausente: location_district
Pergunta: “Qual é o bairro do evento? Preciso dele para validar cobertura e taxa de entrega.”
```

### Regras

- uma pergunta por grupo lógico, não interrogatório longo;
- não repetir pergunta já respondida;
- não iniciar RFQ antes de `READY`;
- registrar `PROCUREMENT_CLARIFICATION_REQUESTED`;
- ao receber resposta, atualizar somente campos sustentados pela mensagem.

---

## 7. Plano de procurement

```python
class ProcurementPlan(BaseModel):
    request_id: str
    target_supplier_count: int
    eliminatory_criteria: list[str]
    ranking_weights: dict[str, int]
    response_deadline: datetime
    negotiation_enabled: bool
    target_total_cents: int | None
    maximum_negotiation_rounds: int
    allowed_negotiation_topics: list[str]
    maximum_follow_ups: int
    approval_checkpoint: Literal["before_award"]
    version: int
```

O comprador deve poder revisar o plano antes de iniciar. O agente pode sugerir defaults; o policy engine do Dev 1 valida.

---

## 8. Sourcing e elegibilidade

### 8.1 Estratégia

1. converter requisição em `SupplierSearchCriteria`;
2. chamar `SupplierDirectoryPort` do Dev 2;
3. aplicar filtros eliminatórios determinísticos;
4. classificar cada candidato como `eligible`, `excluded` ou `needs_refresh`;
5. registrar razão por critério;
6. selecionar até o limite do plano;
7. criar draft de RFQ via port do Dev 4.

### 8.2 Critérios P0

- `status == ACTIVE`;
- categoria compatível;
- região compatível;
- capacidade mínima/máxima;
- antecedência;
- NF, se obrigatória;
- dieta/restrições;
- atualização suficiente;
- campos críticos não desconhecidos.

Não trate `None` como `True`. Desconhecido deve bloquear ou ir para `needs_refresh`.

### 8.3 Resultado explicável

```python
class SupplierEligibilityResult(BaseModel):
    supplier_id: str
    decision: Literal["eligible", "excluded", "needs_refresh"]
    checks: list[EligibilityCheck]
    evidence_refs: list[str]
```

Cada check contém:

```text
criterion
required_value
actual_value
passed
reason_code
```

A explicação em linguagem natural é posterior ao cálculo e não pode modificar o resultado.

---

## 9. Orquestrador do agente

### 9.1 Ciclo

```text
load context
→ interpret latest message/event
→ check missing fields
→ load policy and allowed tools
→ choose next tool
→ authorize through PolicyPort
→ execute typed tool
→ persist result/event
→ reassess state
→ stop at clarification, failure, terminal state or human checkpoint
```

### 9.2 Tools do MVP

Esta branch implementa diretamente:

```text
create_procurement_request
update_procurement_request
get_missing_required_fields
create_procurement_plan
update_procurement_plan
search_suppliers
evaluate_supplier_eligibility
select_rfq_recipients
create_recommendation_explanation
```

Esta branch chama adapters para tools do Dev 4:

```text
create_rfq_round
send_rfq
get_rfq_delivery_status
send_supplier_follow_up
get_quote_status
compare_quotes
run_negotiation_round
request_human_approval
send_award
get_award_status
```

### 9.3 Tool registry

Cada tool deve ter:

- nome estável;
- input Pydantic;
- output Pydantic;
- estados permitidos;
- política exigida;
- idempotency behavior;
- timeout;
- evento de auditoria;
- tratamento de erro conhecido.

### 9.4 Limites

O agente não pode:

- escrever diretamente em repositories;
- atualizar estado por texto;
- criar fornecedor inexistente;
- considerar delivery “enviado” sem ack;
- alterar orçamento máximo;
- remover requisito obrigatório;
- aprovar gasto;
- enviar award sem aprovação;
- inventar proposta concorrente;
- continuar em loop após checkpoint humano.

---

## 10. Modelo de execução e memória

Persistir:

```text
AgentRun
AgentStep
ToolCall
ToolResult
AgentMessage
correlation_id
causation_id
prompt_version
model_name
input_hash
output_hash
status
```

Não persistir chain-of-thought. Persistir apenas:

- decisão resumida;
- ferramenta escolhida;
- argumentos;
- resultado;
- evidências;
- motivo de parada.

### Stop conditions

```text
NEEDS_CLARIFICATION
AWAITING_PLAN_CONFIRMATION
NO_ELIGIBLE_SUPPLIERS
AWAITING_EXTERNAL_RESPONSE
AWAITING_APPROVAL
TERMINAL
ACTION_BLOCKED
MAX_STEPS_REACHED
```

Definir limite de passos por run para impedir loop.

---

## 11. UI do comprador

### Telas P0

1. **Nova requisição** — chat + opção de formulário.
2. **Resumo entendido** — campos, evidências e ambiguidades.
3. **Clarificação** — pergunta objetiva.
4. **Plano** — critérios, quantidade de fornecedores, negociação e aprovação.
5. **Sourcing** — elegíveis, excluídos e razão.
6. **Timeline do agente** — ações reais, bloqueios e espera externa.

Dev 4 implementará comparação/aprovação/award; esta branch deve exportar o shell e pontos de extensão.

### UX anti-slop

- não usar animação falsa de “pesquisando milhares de fornecedores”;
- mostrar quantidade real consultada;
- distinguir “planejado”, “executando”, “confirmado” e “aguardando”; 
- exibir tool calls em linguagem compreensível;
- não mostrar sucesso antes do evento persistido;
- apresentar evidências de inclusão/exclusão.

---

## 12. Eventos desta branch

```text
PROCUREMENT_REQUEST_CREATED
PROCUREMENT_MESSAGE_RECEIVED
PROCUREMENT_INTERPRETED
PROCUREMENT_CLARIFICATION_REQUESTED
PROCUREMENT_FIELD_CONFIRMED
PROCUREMENT_READY
PROCUREMENT_PLAN_CREATED
PROCUREMENT_PLAN_UPDATED
SOURCING_STARTED
SUPPLIER_DIRECTORY_QUERIED
SUPPLIER_ELIGIBILITY_EVALUATED
RFQ_RECIPIENTS_SELECTED
AGENT_RUN_STARTED
AGENT_TOOL_AUTHORIZED
AGENT_TOOL_EXECUTED
AGENT_ACTION_BLOCKED
AGENT_RUN_PAUSED
AGENT_RUN_COMPLETED
```

---

## 13. Estratégia TDD acelerada

### Regra

Todo fluxo do agente deve ser testável sem modelo real, sem rede e sem banco externo, usando:

- fake clock;
- fake LLM com respostas roteirizadas;
- fake supplier directory;
- fake RFQ/decision ports;
- in-memory event/audit store.

### Camadas

#### Unitários

- parsing pós-schema;
- missing fields;
- datas relativas;
- elegibilidade;
- seleção de recipients;
- allowed tools por estado;
- stop conditions.

#### Contract/evals

- provider sempre retorna schema válido;
- tool registry rejeita argumentos extras;
- fakes e adapters obedecem ports;
- cenários de tool choice.

#### Integração

- mensagem → request → clarification;
- request completo → sourcing;
- sourcing → draft de RFQ;
- policy denied → bloqueio auditado;
- run retomado após evento externo.

#### Frontend

- chat mostra campos entendidos;
- plano editável;
- razões de exclusão;
- timeline não antecipa sucesso;
- erro recuperável.

### Testes obrigatórios

```text
test_missing_location_requests_clarification_and_does_not_source
test_relative_date_uses_injected_clock
test_conflicting_people_count_requests_confirmation
test_budget_is_stored_in_integer_cents
test_dietary_counts_above_people_count_are_flagged
test_complete_request_moves_to_ready
test_agent_cannot_start_sourcing_before_ready
test_agent_uses_only_tools_allowed_for_current_state
test_policy_denial_stops_run_and_writes_audit_event
test_agent_stops_at_human_checkpoint
test_agent_stops_at_max_steps_without_side_effects
test_unknown_supplier_field_is_not_treated_as_eligible
test_supplier_without_invoice_is_excluded_when_invoice_required
test_supplier_outside_service_area_is_excluded
test_supplier_with_insufficient_capacity_is_excluded
test_exclusion_result_contains_reason_and_evidence
test_no_eligible_suppliers_moves_to_alternate_state
test_repeated_agent_run_does_not_duplicate_rfq_command
test_agent_resume_processes_new_external_event_once
test_tool_arguments_are_schema_validated_before_execution
```

### Avaliações do agente

Criar cenários dourados:

1. requisição completa;
2. local ausente;
3. data ambígua;
4. orçamento acima da política;
5. nenhum elegível;
6. tentativa de alterar requisito;
7. provider sugere tool proibida;
8. resposta externa ainda não chegou;
9. aprovação pendente;
10. retry da mesma mensagem.

Métrica principal: taxa de escolha correta da próxima tool e zero ações proibidas executadas.

---

## 14. Plano de execução por waves

### Wave A — Request domain adapter e fixtures

- importar contratos do Dev 1;
- criar provider fake;
- escrever testes de interpretação/clarificação;
- implementar request service.

### Wave B — Plan e policy snapshot

- defaults;
- edição;
- policy authorization;
- checkpoint antes de sourcing.

### Wave C — Sourcing

- fake supplier directory;
- filtros determinísticos;
- explicação;
- adapter real para Dev 2.

### Wave D — Orchestrator

- tool registry;
- run loop;
- stop conditions;
- retries;
- audit/correlation.

### Wave E — Buyer UI

- chat/form;
- summary;
- plan;
- sourcing;
- timeline;
- adapters fake/real.

### Wave F — Integração com Dev 4

- chamar create/send RFQ;
- aguardar external event;
- chamar compare/approval/award sem duplicação;
- contract tests entre ports.

---

## 15. Uso de multiagents pelo Dev 3

### Subagente A — Scenario/Test Designer

- transforma requisitos em testes e golden scenarios;
- escreve fakes roteirizados;
- não implementa loop principal.

### Subagente B — Request/Sourcing Implementer

- implementa parsing pós-schema, clarificação, plano e filtros;
- não toca UI.

### Subagente C — Orchestrator/Tooling

- implementa tool registry, run loop, stop conditions e adapters;
- não altera regras de elegibilidade.

### Subagente D — Buyer UI

- implementa chat, resumo, plano e sourcing com mock server;
- não inventa estados visuais.

### Subagente E — Safety Reviewer

- tenta prompt injection, tool proibida, loop, duplicação e bypass de approval;
- adiciona testes adversariais.

### Coordenação

- A congela cenários e contratos;
- B e C trabalham em módulos separados;
- D avança com fakes;
- E só revisa após fluxo verde;
- líder conecta ports do Dev 2 e Dev 4 sem alterar os módulos deles.

---

## 16. Contrato com Dev 4

### Comando para criar rodada

```python
class CreateRFQRoundCommand(BaseModel):
    procurement_request_id: str
    request_version: int
    recipient_supplier_ids: list[str]
    response_deadline: datetime
    requirements_snapshot: dict[str, Any]
    policy_snapshot: dict[str, Any]
    idempotency_key: str
```

### Comando para envio

```python
class SendRFQRoundCommand(BaseModel):
    rfq_round_id: str
    channel: Literal["email", "manual_link"]
    idempotency_key: str
```

### Resultado esperado

```python
class DeliveryBatchDTO(BaseModel):
    rfq_round_id: str
    deliveries: list[DeliveryDTO]
    all_confirmed: bool
```

A requisição não muda para `RFQ_ACTIVE` apenas porque o comando foi aceito. Deve aguardar evento de delivery confirmado emitido pelo Dev 4.

---

## 17. Critérios de aceite da branch

- [ ] Texto livre vira schema visível e versionado.
- [ ] Campo bloqueante ausente impede sourcing.
- [ ] O agente não repete perguntas já respondidas.
- [ ] Plano é revisável e policy-checked.
- [ ] Sourcing usa dados reais do `SupplierDirectoryPort`.
- [ ] Inclusões e exclusões possuem razão determinística.
- [ ] Tool registry valida inputs e estado.
- [ ] Toda tool passa pelo `PolicyPort`.
- [ ] Agent run possui limite e stop conditions.
- [ ] Retry não duplica comando externo.
- [ ] UI não apresenta ação como concluída antes do evento.
- [ ] Fluxo inteiro roda com fakes em testes.
- [ ] Adapter real passa contract tests com Dev 2 e Dev 4.
- [ ] Testes, lint e tipos passam.

---

## 18. Commits sugeridos

```text
test(agent): define procurement request and golden scenarios
feat(agent): add structured request interpretation and clarification
feat(agent): add editable procurement plan and policy snapshot
feat(sourcing): add deterministic supplier eligibility checks
feat(sourcing): implement supplier directory adapter
feat(agent): add typed tool registry and run loop
feat(agent): add stop conditions, retries and audit correlation
test(agent): block forbidden tools and duplicate side effects
feat(buyer-ui): add request, plan and sourcing experience
feat(agent): integrate RFQ and quote decision ports
docs(agent): document scenario evals and operator workflow
```

---

## 19. Crítica da frente

### Ponto positivo

Esta frente transforma o produto de “base estruturada de fornecedores” em um agente de procurement real, capaz de conduzir trabalho e não apenas responder perguntas.

### Ponto negativo

Um loop agentic genérico é o caminho mais rápido para instabilidade. Não crie um agente que decide tudo por texto. Use workflow explícito, tools tipadas, fakes roteirizados e regras determinísticas; a IA deve escolher e preencher ações dentro de um espaço pequeno.

### Decisão de escopo

O agente deve parecer inteligente por entender contexto e coordenar ações reais, não por possuir liberdade irrestrita.
