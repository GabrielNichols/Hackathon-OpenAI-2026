# Dev 3 — decisão e protótipo inicial

## Decisão

O Dev 3 será implementado como um **workflow explícito e limitado**, dentro de um
monólito modular Python 3.12/FastAPI. O modelo de linguagem não controla estado nem
escreve em repositórios: ele fica atrás de `ProcurementInterpretationPort` e somente
propõe fatos estruturados com evidência. Regras determinísticas controlam readiness,
política, elegibilidade, seleção, idempotência e checkpoints humanos.

Essa forma foi escolhida porque os planos dos Devs 1, 2 e 4 ainda são contratos em
documentação. Ports locais e adapters em memória permitem demonstrar o fluxo agora e
trocar as implementações sem reescrever o orquestrador.

## Vertical slice entregue

```text
mensagem do comprador
→ interpretação conservadora com evidência
→ draft versionado
→ clarificação ou READY
→ plano + policy snapshot
→ confirmação humana
→ SupplierDirectoryPort
→ elegibilidade PASS / FAIL / UNKNOWN
→ recipients determinísticos
→ CreateRFQRoundCommand idempotente
→ AWAITING_EXTERNAL_RESPONSE
```

O estado persiste como `SOURCING` depois do draft. Somente o futuro adapter do Dev 4,
após confirmação real de entrega, poderá emitir `RFQ_DELIVERY_CONFIRMED` e promover o
processo a `RFQ_ACTIVE`.

## Módulos

- `procurement_requests`: `Patch`, `Draft` e `Ready` separados; interpretadores local
  e OpenAI substituíveis, clarificação, conflitos, plano e policy snapshot.
- `sourcing`: contrato do diretório do Dev 2, filtros determinísticos, resultado
  tri-state e seleção fail-closed.
- `procurement_agent`: stop reasons separados do estado de domínio, tool registry,
  policy authorization, bounded runs, idempotência, ports e orquestração.
- `buyer_timeline`: envelope de auditoria compatível com o plano do Dev 1.
- `api` + `frontend`: três operações HTTP e uma UI sem build step.

## API do protótipo

- `POST /api/v1/procurement-requests/messages` (`Idempotency-Key` obrigatório na criação)
- `POST /api/v1/procurement-requests/{request_id}/plan/confirm`
- `GET /api/v1/procurement-requests/{request_id}`
- `GET /health`

## Decisões de segurança e consistência

- `Draft` parcial nunca é tratado como `Ready`.
- `AgentStopReason` nunca é persistido como status da requisição.
- `None` de supplier produz `UNKNOWN/needs_refresh`, nunca aprovação implícita.
- Uma incompatibilidade confirmada sempre prevalece sobre `UNKNOWN`.
- A decisão de elegibilidade é validada contra os checks; um payload não pode se
  autodeclarar `eligible` contendo `FAIL`.
- Toda tool valida schema, estado e `PolicyPort` antes de executar.
- O hash idempotente ignora somente IDs transitórios do run; mudanças semânticas no
  request, plano, recipients ou policy geram conflito.
- A criação HTTP também exige idempotency key; retry do mesmo briefing devolve a
  requisição original e o reuso com outro payload falha.
- Conflitos de fatos não sobrescrevem o valor anterior; uma confirmação explícita é
  necessária e auditada.
- O adapter em memória usa uma unidade de trabalho serializada. PostgreSQL deve usar
  transação e optimistic locking no adapter de produção.
- Falha/timeout de port encerra o run, grava evento e devolve a requisição a `READY`
  para retry; nenhum draft externo é criado nesses cenários.
- A OpenAI recebe apenas a mensagem e o relógio de referência. O draft atual não é
  enviado: conflitos são detectados localmente. A chamada usa Responses API, schema
  Pydantic, `store=False`, sem tools e com saída limitada.
- Fatos retornados pelo modelo sem evidência literal na mensagem são descartados.
  Readiness, conflitos, políticas, estado, sourcing e RFQ continuam fora da autoridade
  do modelo.
- Exceções do provider são convertidas em reason codes estáveis; headers, prompts,
  respostas brutas e chaves não entram na API nem na timeline.

## Provider OpenAI e credenciais

O composition root aceita três modos por `PROCUREMENT_INTERPRETER`:

- `local` (default): parser determinístico, totalmente offline e sem custo;
- `openai`: provider obrigatório; falha sanitizada retorna `503`;
- `auto`: usa OpenAI quando configurada e faz fallback local apenas para falha tipada
  do provider.

A chave tem precedência por `OPENAI_API_KEY` ou `CANAL_AGENTE_OPENAI_API_KEY`. O
fallback local de desenvolvimento é `.secrets/openai_api_key.txt`, diretório ignorado
integralmente pelo Git. Como este workspace está dentro do OneDrive, o arquivo pode ser
sincronizado pelo sistema mesmo sem entrar no Git; em produção deve ser substituído por
variável protegida ou secret manager.

O default é `gpt-5.6-luna`, configurável por `OPENAI_PROCUREMENT_MODEL`, com timeout,
retry e limite de saída também configuráveis. Cada interpretação auditada registra
somente provider, modelo, response ID, versão/hash do prompt, hashes canônicos do
input/output/schema e contagem de tokens; texto bruto não entra nesse evento.

## Fixtures e verdade da demo

O modo retornado pela API é `demo_fake`, `demo_openai_interpreter` ou
`demo_openai_with_local_fallback`. Mesmo com interpretação OpenAI, fornecedores e RFQ
continuam simulados. O relógio está fixado em `2026-08-19T15:00:00Z`, e os cinco
fornecedores são fixtures automatizadas:

- dois elegíveis para receber RFQ;
- um excluído por NF;
- um excluído por região;
- um `needs_refresh` por capacidade vegana desconhecida.

Esses dados não podem ser apresentados como fornecedores ou entregas reais. A UI
identifica o modo e chama o resultado externo de **draft**.

## Handoffs

Para o Dev 2, substituir `InMemorySupplierDirectory` por `SupplierDirectoryPort`,
preservando os contract tests de `SupplierSearchCriteria` e `SupplierCandidateDTO`.

Para o Dev 4, substituir `InMemoryRFQExecutionAdapter` por `RFQExecutionPort`. O comando
congela request version, plan version, supplier IDs ordenados, requirements, policy,
contexto e idempotency key. Envio, delivery ack, quotes, comparação e approval ficam
fora deste corte.

Para o Dev 1, trocar audit/policy/repositórios em memória pelos contratos compartilhados
e por persistência transacional.

## Limitações deliberadas

- provider OpenAI restrito à interpretação; não há tool calling autônomo nem memória
  remota do modelo;
- dados somente em memória;
- uma instância/processo de servidor;
- sem autenticação real ou isolamento multi-tenant no adapter HTTP de demo;
- sem rate limit ou quota de consumo OpenAI por comprador;
- plano visível, mas ainda sem editor na UI;
- nenhuma mensagem externa, quote, approval ou award real;
- freshness global do perfil (90 dias), porque o DTO atual não fornece validade por
  campo;
- `service_areas` e capacidades dietárias ainda usam strings do contrato v0.

Antes de qualquer demo ser chamada de ponta a ponta real, os adapters dos Devs 2 e 4,
autenticação, persistência e fornecedores autorizados precisam substituir as fixtures.
Até autenticação, isolamento por tenant, rate limit e quotas existirem, os modos pagos
devem ficar restritos a `127.0.0.1` e nunca ser publicados na internet.
