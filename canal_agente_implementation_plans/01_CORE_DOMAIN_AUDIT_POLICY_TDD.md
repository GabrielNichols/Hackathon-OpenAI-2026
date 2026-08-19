# Implementation Plan 01 — Core de Domínio, Estados, Auditoria e Policy Engine

**Projeto:** Canal Agente  
**Responsável:** Dev 1 — Platform/Core  
**Branch:** `feat/core-domain-audit-policy`  
**PR base:** `main`  
**Missão:** construir a fundação determinística e auditável sobre a qual os outros três módulos trabalharão.  
**Resultado esperado:** nenhuma IA, tela ou integração externa consegue alterar estados, aprovar gastos ou registrar ações sem passar pelas regras de domínio.

---

## 1. Contexto essencial do produto

O Canal Agente é uma plataforma de procurement com um agente comprador nativo. O MVP começa por alimentação para eventos corporativos em São Paulo. O fluxo central é:

```text
material real do fornecedor
→ extração com evidência
→ confirmação do fornecedor
→ requisição do comprador
→ sourcing
→ RFQs reais
→ propostas reais
→ comparação determinística
→ aprovação humana
→ award
→ aceite e reserva de capacidade
```

O agente interpreta e orquestra. O backend de domínio é a fonte de verdade. O modelo nunca atualiza estado diretamente.

### Princípios não negociáveis

1. Dinheiro é armazenado em centavos inteiros; nunca em `float`.
2. Datas internas usam UTC; a interface pode exibir `America/Sao_Paulo`.
3. Toda entidade mutável possui `version` para concorrência otimista.
4. Toda ação externa possui `idempotency_key`.
5. Toda transição de estado gera evento de auditoria na mesma transação.
6. Estado só muda por comando de domínio validado.
7. LLMs e canais externos ficam atrás de ports/adapters.
8. Testes unitários não acessam rede, relógio real ou APIs de IA.

---

## 2. Limites da branch

### Esta branch deve implementar

- bootstrap mínimo do backend;
- contratos compartilhados v0;
- tipos de ID, dinheiro, datas e erros;
- entidades-base e aggregates;
- máquinas de estado;
- command handlers de transição;
- event log append-only;
- outbox para ações externas;
- idempotência;
- policy engine;
- links assinados e expiráveis;
- interfaces de repositório e ports;
- persistência PostgreSQL e migrations do núcleo;
- factories, clocks e IDs determinísticos para testes;
- CI mínimo e comandos de qualidade;
- documentação dos contratos usados pelas demais branches.

### Esta branch não deve implementar

- parsing de documentos;
- onboarding visual de fornecedor;
- agente ou prompts;
- sourcing semântico;
- envio efetivo de RFQ;
- resposta de cotação;
- comparação visual;
- aprovação, award ou reserva como fluxos completos;
- frontend de negócio.

Pode modelar estados e comandos dessas áreas, mas não implementar suas experiências completas.

---

## 3. Stack e estrutura-alvo

Caso o repositório já possua stack equivalente, preserve-a. Em projeto greenfield, use:

- Python 3.12+
- FastAPI
- Pydantic v2
- SQLAlchemy 2
- Alembic
- PostgreSQL
- `pytest`, `pytest-asyncio`, `httpx`
- `ruff` e verificação estática de tipos
- Docker Compose para dependências locais

Estrutura sugerida:

```text
backend/
  app/
    api/
    contracts/
    domain/
      common/
      suppliers/
      procurement/
      quotes/
    application/
    infrastructure/
      db/
      outbox/
      security/
    platform/
  tests/
    unit/
    contract/
    integration/
shared/
  contracts/
scripts/
```

### Propriedade de arquivos

Dev 1 é o único responsável por:

```text
backend/app/contracts/**
backend/app/domain/**
backend/app/platform/**
backend/app/infrastructure/db/**
backend/app/infrastructure/outbox/**
backend/app/infrastructure/security/**
backend/tests/unit/domain/**
backend/tests/contract/core/**
backend/tests/integration/core/**
```

Não altere módulos de feature dos outros devs. Não registre todas as rotas em um arquivo central; cada feature deve exportar seu próprio router e o Dev 4 fará o wiring final.

---

## 4. Contrato compartilhado v0

Este é o primeiro artefato que deve ser commitado. Outros devs podem trabalhar contra fakes com os mesmos nomes, mas devem rebasedar nesse contrato antes do merge.

### 4.1 Convenções

```python
EntityId = str
MoneyCents = int
Version = int
IdempotencyKey = str
CorrelationId = str
```

- IDs devem ser opacos e prefixados: `sup_`, `pr_`, `rfq_`, `quo_`, `apr_`, `awd_`.
- `MoneyCents >= 0`.
- payloads externos usam ISO 8601.
- enums são strings estáveis; não renomear depois do contract freeze sem ADR.

### 4.2 Envelope de erro

```json
{
  "error": {
    "code": "INVALID_STATE_TRANSITION",
    "message": "Quote cannot move from REQUESTED to VALID",
    "details": {},
    "correlation_id": "cor_123"
  }
}
```

Códigos mínimos:

- `VALIDATION_ERROR`
- `NOT_FOUND`
- `CONFLICT`
- `INVALID_STATE_TRANSITION`
- `POLICY_DENIED`
- `LINK_EXPIRED`
- `LINK_INVALID`
- `IDEMPOTENCY_CONFLICT`
- `OPTIMISTIC_LOCK_CONFLICT`
- `EXTERNAL_DELIVERY_NOT_CONFIRMED`

### 4.3 Envelope de auditoria

```python
class AuditEventDTO(BaseModel):
    event_id: str
    event_type: str
    aggregate_type: str
    aggregate_id: str
    actor_type: Literal["human", "supplier", "agent", "system", "external_service"]
    actor_id: str | None
    occurred_at: datetime
    previous_state: str | None
    new_state: str | None
    correlation_id: str
    causation_id: str | None
    agent_run_id: str | None
    idempotency_key: str | None
    payload: dict[str, Any]
```

### 4.4 Ports que devem existir

```python
class Clock(Protocol):
    def now(self) -> datetime: ...

class IdGenerator(Protocol):
    def new(self, prefix: str) -> str: ...

class AuditPort(Protocol):
    async def append(self, events: Sequence[AuditEventDTO]) -> None: ...

class PolicyPort(Protocol):
    async def authorize(self, request: AuthorizationRequest) -> AuthorizationDecision: ...

class SupplierDirectoryPort(Protocol):
    async def search(self, criteria: SupplierSearchCriteria) -> list[SupplierCandidateDTO]: ...
    async def get(self, supplier_id: str) -> SupplierCandidateDTO | None: ...

class RFQExecutionPort(Protocol):
    async def create_round(self, command: CreateRFQRoundCommand) -> RFQRoundDTO: ...
    async def send_round(self, command: SendRFQRoundCommand) -> DeliveryBatchDTO: ...
    async def get_status(self, round_id: str) -> RFQRoundDTO: ...

class QuoteDecisionPort(Protocol):
    async def compare(self, procurement_request_id: str) -> QuoteComparisonDTO: ...
    async def request_approval(self, command: RequestApprovalCommand) -> ApprovalDTO: ...
    async def send_award(self, command: SendAwardCommand) -> AwardDTO: ...
```

Os DTOs precisam ser pequenos, serializáveis e sem dependência de ORM.

---

## 5. Modelo de domínio mínimo

### 5.1 Aggregates

Implementar aggregates com invariantes explícitas:

- `SupplierAggregate`
- `ProcurementRequestAggregate`
- `QuoteAggregate`
- `ApprovalAggregate`
- `AwardAggregate`

Os módulos dos outros devs poderão acrescentar dados, mas não devem contornar essas invariantes.

### 5.2 Estados

#### Fornecedor

```text
DRAFT
→ MATERIALS_UPLOADED
→ EXTRACTED
→ AWAITING_SUPPLIER_REVIEW
→ CONFIRMED
→ ACTIVE
→ SUSPENDED | EXPIRED
```

#### Requisição

```text
DRAFT
→ NEEDS_CLARIFICATION
→ READY
→ SOURCING
→ RFQ_ACTIVE
→ QUOTES_UNDER_REVIEW
→ NEGOTIATING
→ AWAITING_APPROVAL
→ APPROVED
→ AWARD_SENT
→ SUPPLIER_ACCEPTED
→ READY_FOR_CONTRACTING
→ CLOSED
```

Estados alternativos:

```text
CANCELLED
NO_ELIGIBLE_SUPPLIERS
NO_VALID_QUOTES
APPROVAL_REJECTED
SUPPLIER_DECLINED_AWARD
EXPIRED
```

#### Cotação

```text
REQUESTED
→ OPENED
→ DRAFT_RESPONSE
→ SUBMITTED
→ VALIDATING
→ NEEDS_CLARIFICATION
→ VALID
→ NEGOTIATING
→ FINAL
→ SELECTED | REJECTED | EXPIRED
```

### 5.3 Regras centrais

- Fornecedor só entra em `ACTIVE` após campos mínimos confirmados.
- Requisição só entra em `SOURCING` se estiver `READY`.
- `RFQ_SENT` é um evento, não um texto produzido pelo agente.
- Cotação só entra em `VALID` com preço, disponibilidade, itens, validade, requisitos e identidade do respondente.
- `APPROVED` exige aprovação humana persistida.
- `AWARD_SENT` exige confirmação real do gateway de entrega.
- `SUPPLIER_ACCEPTED` exige submissão real do link do fornecedor.
- `READY_FOR_CONTRACTING` exige aceite e reserva de capacidade.

---

## 6. Policy Engine

### 6.1 Entradas mínimas

```python
class AuthorizationRequest(BaseModel):
    actor_type: str
    actor_id: str | None
    action: str
    aggregate_type: str
    aggregate_id: str
    current_state: str
    arguments: dict[str, Any]
    procurement_policy: dict[str, Any]
```

### 6.2 Saída

```python
class AuthorizationDecision(BaseModel):
    allowed: bool
    reason_code: str
    reason: str
    constraints: dict[str, Any] = {}
```

### 6.3 Políticas P0

- orçamento máximo;
- campos obrigatórios antes de sourcing;
- ações permitidas por estado;
- limite de rodadas de negociação;
- atributos negociáveis;
- proibição de award sem aprovação;
- proibição de alteração de requisito eliminatório pelo agente;
- limite de follow-ups;
- horário de contato;
- proteção contra acesso cross-tenant;
- proibição de disclosure de dados concorrentes.

### 6.4 Comportamento de negação

Uma negação deve:

1. não alterar o aggregate;
2. registrar `AGENT_ACTION_BLOCKED` ou `USER_ACTION_BLOCKED`;
3. retornar código estável;
4. indicar se exige revisão humana.

---

## 7. Event log, outbox e idempotência

### 7.1 Event log

- append-only;
- ordenado por aggregate e versão;
- gravado na mesma transação da mudança de estado;
- reconstruível para timeline;
- sem payloads secretos.

### 7.2 Outbox

Ações externas — e-mail, mensagens, award — devem criar item na outbox antes do envio.

Campos mínimos:

```text
id
kind
aggregate_id
payload
idempotency_key
status: PENDING | PROCESSING | DELIVERED | FAILED
attempt_count
next_attempt_at
last_error
created_at
updated_at
```

### 7.3 Idempotência

- mesma chave + mesmo payload retorna resultado anterior;
- mesma chave + payload diferente retorna `IDEMPOTENCY_CONFLICT`;
- retry não pode criar outra RFQ, cotação, aprovação ou award;
- delivery ack deve ser persistido antes de mudar estado.

---

## 8. Links assinados

Criar serviço genérico usado por Dev 2 e Dev 4.

Payload mínimo:

```json
{
  "purpose": "supplier_profile_review",
  "subject_id": "sup_123",
  "recipient_id": "contact_123",
  "expires_at": "...",
  "nonce": "...",
  "tenant_id": "org_123"
}
```

Requisitos:

- assinatura HMAC ou mecanismo equivalente;
- expiração validada no servidor;
- purpose binding;
- tenant binding;
- comparação segura;
- nonce opcional de uso único para ações finais;
- segredo somente por variável de ambiente.

---

## 9. Estratégia TDD acelerada

### Regra operacional

Nenhum comportamento de domínio deve ser implementado sem um teste vermelho correspondente.

### Ciclo por comportamento

1. **Red:** escrever um teste com nome de negócio e falha correta.
2. **Green:** implementar apenas o necessário.
3. **Refactor:** remover duplicação sem mudar comportamento.
4. **Adversarial:** acrescentar pelo menos um teste de falha ou abuso.
5. **Contract:** confirmar serialização/assinatura pública.

### Prioridade de testes

1. unitários puros do domínio;
2. testes de policy;
3. contratos de DTO/ports;
4. persistência e transação;
5. concorrência e idempotência;
6. API apenas para health/erro padrão.

### Testes obrigatórios

Criar, no mínimo:

```text
test_supplier_cannot_skip_from_draft_to_active
test_supplier_can_activate_only_with_confirmed_required_fields
test_procurement_request_requires_ready_before_sourcing
test_procurement_request_cannot_award_without_human_approval
test_quote_cannot_be_valid_without_required_fields
test_supplier_acceptance_requires_real_submission_event
test_state_change_and_audit_event_are_atomic
test_failed_transaction_does_not_append_audit_event
test_same_idempotency_key_and_payload_returns_original_result
test_same_idempotency_key_with_different_payload_is_rejected
test_optimistic_lock_rejects_stale_version
test_policy_blocks_agent_from_approving_spend
test_policy_blocks_budget_above_maximum
test_policy_blocks_changing_mandatory_requirement
test_policy_blocks_cross_tenant_access
test_signed_link_rejects_expired_token
test_signed_link_rejects_wrong_purpose
test_outbox_retry_does_not_duplicate_business_event
```

### Testes property-based recomendados

- valores monetários nunca ficam negativos;
- qualquer transição não listada é rejeitada;
- serializar e desserializar evento preserva dados;
- ordem de retries não duplica efeito;
- score/política não aceita `NaN`, infinito ou float.

---

## 10. Plano de execução por waves

### Wave A — Contract freeze

- criar DTOs, enums, ports e error envelope;
- escrever testes de serialização;
- publicar commit isolado `contracts-v0`;
- avisar outros devs para rebase.

### Wave B — Domínio puro

- implementar value objects;
- implementar aggregates e transition tables;
- criar domain events;
- cobrir invariantes com testes unitários.

### Wave C — Persistência e atomicidade

- migrations;
- repositories;
- unit of work;
- event log;
- optimistic locking;
- testes com PostgreSQL real em integração.

### Wave D — Policy, outbox e security

- policy engine;
- idempotency registry;
- outbox;
- signed links;
- testes adversariais.

### Wave E — Handoff

- exemplos de uso dos ports;
- fake implementations para Devs 2–4;
- fixtures compartilhadas;
- contract test kit reutilizável.

---

## 11. Uso de multiagents pelo Dev 1

O agente líder deve delegar com papéis não sobrepostos:

### Subagente A — Contract/Test Designer

- escreve DTOs propostos e testes de contrato;
- não implementa produção;
- verifica compatibilidade entre módulos.

### Subagente B — Domain Implementer

- implementa value objects, aggregates e commands;
- só trabalha após testes vermelhos existirem.

### Subagente C — Persistence/Concurrency

- implementa repositories, unit of work, migrations, idempotência e outbox;
- executa testes com PostgreSQL.

### Subagente D — Adversarial Reviewer

- procura transições ilegais, race conditions, vazamento cross-tenant, duplicação e bypass de policy;
- adiciona testes, não reescreve arquitetura inteira.

### Ordem de coordenação

1. líder cria mapa de arquivos e contratos;
2. A escreve testes e contract freeze;
3. B e C trabalham em diretórios separados;
4. D revisa somente após suites locais verdes;
5. líder integra, roda tudo e resolve conflitos sem ampliar escopo.

---

## 12. Fixtures compartilhadas

Publicar factories estáveis:

```text
org_demo
buyer_gabriel
approver_demo
supplier_alpha
supplier_beta
pr_demo_coffee_break
quote_alpha_v1
quote_beta_v1
```

Relógio fixo padrão:

```text
2026-08-19T15:00:00Z
```

Valores:

```text
people_count = 80
maximum_total_cents = 450000
target_total_cents = 410000
```

Fixtures não devem conter respostas fictícias apresentadas como reais na demo; servem apenas para testes automatizados.

---

## 13. Critérios de aceite da branch

- [ ] Todos os enums e DTOs compartilhados estão versionados.
- [ ] Transições ilegais falham antes de persistência.
- [ ] Toda transição legal gera audit event atômico.
- [ ] Idempotência foi testada com retry e payload conflitante.
- [ ] Optimistic locking impede lost update.
- [ ] Policy engine bloqueia ações proibidas do agente.
- [ ] Links assinados validam purpose, tenant e expiração.
- [ ] Outbox não duplica efeitos em retry.
- [ ] Nenhum teste unitário usa rede ou relógio real.
- [ ] Contract test kit pode ser importado pelos outros módulos.
- [ ] Migrations sobem e descem em banco limpo.
- [ ] `ruff`, tipos e testes passam localmente.

---

## 14. Commits sugeridos

```text
test(core): freeze shared DTO and port contracts
feat(core): add value objects and aggregate state machines
test(core): cover invalid transitions and domain invariants
feat(core): add atomic event log and optimistic locking
feat(core): add idempotency registry and outbox
feat(core): add deterministic procurement policy engine
feat(core): add signed expirable action links
refactor(core): publish shared fakes and contract test kit
docs(core): document module integration boundaries
```

Um commit deve representar um comportamento coerente. Não misture mudanças de contrato com refactors cosméticos.

---

## 15. Handoff para as outras branches

### Para Dev 2

Entregar:

- `SupplierAggregate`;
- signed-link service;
- `SupplierDirectoryPort`;
- event types de supplier;
- repository base e test kit.

### Para Dev 3

Entregar:

- `ProcurementRequestAggregate`;
- `PolicyPort`;
- tool authorization DTOs;
- audit/correlation primitives;
- fake ports para supplier e RFQ.

### Para Dev 4

Entregar:

- `QuoteAggregate`, `ApprovalAggregate`, `AwardAggregate`;
- outbox/idempotency;
- signed-link service;
- state events e contract tests.

---

## 16. Crítica da frente

### Ponto positivo

Esta frente reduz o maior risco de “AI slop”: o modelo não consegue inventar sucesso, porque confirmação, aprovação, envio e aceite dependem de eventos tipados e persistidos.

### Ponto negativo

O core pode virar uma arquitetura excessiva e consumir o hackathon. Não implemente event sourcing completo, mensageria distribuída ou abstrações genéricas sem uso imediato. O alvo é um monólito modular auditável, não uma plataforma corporativa definitiva.

### Decisão de escopo

Prefira uma implementação simples e demonstrável com transações corretas a uma arquitetura distribuída incompleta.
