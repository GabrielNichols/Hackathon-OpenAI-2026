# Dev 4 — runbook da demo real

Este é o caminho oficial para a apresentação. Ele não usa
`FakeDeliveryGateway`, `InMemoryExecutionStore`, auto-ack, auto-approval nem
respostas de fornecedor pré-fabricadas.

## O que é real neste fluxo

- estado, idempotência e auditoria persistidos em PostgreSQL;
- estado sensível cifrado com AES-256-GCM;
- link opaco e individual por fornecedor;
- operador humano revela o link e registra o envio real por WhatsApp ou e-mail;
- `DELIVERED` somente após o fornecedor confirmar a abertura em um POST;
- proposta preenchida pelo fornecedor, incluindo preço, NF, dietas e plástico;
- pedido de esclarecimento visível e proposta versionada;
- comparação e score determinísticos, com no mínimo duas propostas válidas;
- matriz completa de comparação em rota autenticada e somente leitura;
- decisão de um aprovador autenticado;
- award enviado e aberto separadamente;
- termos completos do award exibidos antes do aceite;
- aceite explícito vinculado ao hash visível dos termos exibidos;
- reserva de capacidade confirmada em uma ação distinta;
- event log append-only com origem, rastreio da execução do agente e estados
  anterior/novo quando o evento representa uma transição;
- página autenticada e tenant-scoped que reúne a evidência da execução;
- restart preservando `READY_FOR_CONTRACTING` e toda a evidência.

## 1. Preparar infraestrutura

Requisitos:

- Python 3.12+;
- PostgreSQL acessível pelo servidor;
- hostname HTTPS público;
- proxy configurado para aceitar apenas o host público esperado;
- pelo menos três fornecedores reais cadastrados, dos quais dois participarão
  da rodada demonstrada, e um aprovador real.

Instale e valide:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m ruff check backend scripts
```

Copie `.env.live.example` para um arquivo local não versionado e substitua os
placeholders. Os três segredos de aplicação devem ser aleatórios, ter pelo
menos 32 bytes e ser diferentes. As credenciais de operador e aprovador também
devem ser diferentes.

O bootstrap do hackathon cria as tabelas ausentes de forma idempotente. Antes
de uma implantação posterior ao evento, substitua `create_all` por migrations
Alembic revisadas.

## 2. Subir o servidor

Carregue as variáveis no processo e execute:

```powershell
.\.venv\Scripts\python.exe -m uvicorn app.live.entrypoint:app `
  --host 127.0.0.1 --port 8000 `
  --proxy-headers --forwarded-allow-ips "IP_DO_PROXY" `
  --no-access-log
```

`--no-access-log` evita registrar capabilities na URL. O proxy externo também
deve redigir o request target ou desabilitar access logs para `/live/supplier`.
Não aceite `X-Forwarded-*` diretamente da internet.

Preflight:

```powershell
Invoke-RestMethod https://SEU-HOST-PUBLICO/health/live
Invoke-RestMethod https://SEU-HOST-PUBLICO/health/ready
```

Ambos precisam responder antes da banca. O processo live falha ao iniciar se o
banco não for PostgreSQL, a URL não for HTTPS, faltarem segredos ou houver um
adapter fake no grafo.

## 3. Preparar a execução

O composition root live injeta `app.state.dev3_execution_adapter` diretamente
no orchestrator do Dev 3. Envie o briefing autenticado com Bearer para
`POST /api/v1/procurement-requests/messages`; ao confirmar o plano em
`POST /api/v1/procurement-requests/{request_id}/plan/confirm`, o sourcing cria
a rodada durável no Dev 4 e prepara os links manuais de forma idempotente.
O teste `backend/tests/test_live_dev3_dev4_integration.py` prova esse handoff.
As operações posteriores continuam disponíveis em `app.state.execution_port`.

Congele no comando de criação:

- exatamente o `tenant_id` configurado;
- pelo menos três fornecedores reais cadastrados no diretório (critério do
  MVP) e dois deles selecionados pelo Dev 3 para a rodada demonstrada;
- prazo futuro;
- requisitos completos e orçamento em centavos;
- `minimum_confirmed_deliveries=2`;
- `minimum_valid_quotes=2`;
- aprovador igual a `CANAL_AGENT_APPROVER_USER_ID`.

Após `create_round`, o callback autenticado chama `send_round`. Isso apenas
prepara os links; não declara entrega.

## 4. Operar os checkpoints humanos

1. Abra `/live/operator/deliveries` e autentique com HTTP Basic:
   usuário `CANAL_AGENT_OPERATOR_USER_ID` e senha
   `CANAL_AGENT_OPERATOR_ACCESS_TOKEN`.
2. Para cada um dos dois fornecedores da demo, clique em revelar/copiar. Esse
   POST registra `LINK_COPIED` e somente então mostra o link.
3. Envie o link de verdade por WhatsApp ou e-mail e registre o canal/contato em
   outro POST. O estado continua `SENT_TO_GATEWAY`.
4. Cada fornecedor abre o link e confirma a abertura. Um GET de preview ou
   scanner não muda estado. O POST explícito cria `SUPPLIER_OPENED` e
   `DELIVERED`.
5. O fornecedor envia a proposta. Para provar esclarecimento, combine antes
   que um deles envie a primeira versão sem um requisito obrigatório; a tela
   mostra a pendência e aceita a versão 2 corrigida.
6. Com duas propostas finais, o Dev 3 chama `compare`. O operador autenticado
   abre `/live/operator/comparisons/{comparison_id}` e confere a matriz: valores
   normalizados, elegibilidade, riscos, evidências, score agregado e seus
   componentes. Depois o Dev 3 chama `request_approval`.
7. Abra `/live/approvals/{approval_id}` com as credenciais do aprovador. A
   identidade registrada vem do servidor, não do formulário. Aprove.
8. O Dev 3 chama `send_award`. Volte à lista de entregas, revele e envie o novo
   link ao fornecedor vencedor.
9. O fornecedor confirma a abertura e lê o snapshot completo: proposta e
   versão, fornecedor, total/moeda, itens, substituições, cancelamento, data,
   janela e número de pessoas. Confirme o hash visível e marque o aceite; em
   uma segunda ação, confirme data, horário e capacidade.
10. O resultado só fica `READY_FOR_CONTRACTING` após a reserva.

## 5. Evidência para a banca

Autentique como operador e abra
`/live/operator/runs/{procurement_request_id}`. A página é somente leitura,
filtrada pelo tenant autenticado e não expõe capabilities, contato, IP, user
agent ou tokens. Use também
`/live/operator/comparisons/{comparison_id}` para tornar o ranking observável.

Mostre, nesta ordem:

- duas entregas com `LINK_CREATED → LINK_COPIED → SEND_RECORDED → SUPPLIER_OPENED`;
- proposta v1 `NEEDS_CLARIFICATION` e v2 `FINAL`;
- duas propostas válidas e a matriz com score, componentes, motivos e
  evidências;
- aprovação com ator humano configurado;
- snapshot completo do award, hash correspondente, aceite e reserva confirmada;
- timeline segura com origem do evento, tipo, ator e detalhe;
- status final após reiniciar o processo.

Como evidência técnica separada da página, o event log persistido contém
`previous_state`, `new_state`, `origin`, `agent_run_id` e `idempotency_key`
quando aplicáveis. Campos não aplicáveis permanecem nulos, em vez de receber
valores inferidos. A rota do operador expõe uma projeção segura desse log; ela
não renderiza o envelope raw nem metadados sensíveis.

O teste `backend/tests/test_live_real_e2e.py` executa exatamente esse contrato
com SQL e adapters reais, usando SQLite somente como banco descartável de
teste. Ele também verifica que tokens e contatos não aparecem em texto puro no
arquivo persistido.

## 6. Falhas e fallback honesto

- Sem duas aberturas: não diga que a RFQ está ativa; use outro fornecedor real.
- Sem duas propostas válidas: não compare; corrija/esclareça a proposta.
- Quote expirada ou alterada: a aprovação anterior é invalidada.
- Award não aberto: não aceite e não reserve.
- Banco/rede indisponível: pare a demo; o modo live não troca para memória/fake.
- Fallback de palco: use apenas leitura de uma execução real já persistida,
  identificada por data/hora. Nunca execute `run_dev4_prototype.py` como se
  fosse uma ação externa real.

Após a apresentação, expire os links, rotacione credenciais expostas e aplique
a política acordada de retenção/anonimização dos dados.
