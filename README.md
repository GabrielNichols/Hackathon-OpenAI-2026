# Canal Agente

Protótipo inicial do **Dev 3 — Agent/Buyer Workflow** para o hackathon OpenAI 2026.

O corte implementado transforma um briefing em texto em uma requisição estruturada,
pede clarificações bloqueantes, apresenta um plano para confirmação humana, executa
sourcing determinístico e cria um draft idempotente de RFQ. Ele não apresenta o draft
como enviado e não muda a requisição para `RFQ_ACTIVE` sem um evento externo real.

## Executar

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
.\.venv\Scripts\python.exe -m uvicorn app.main:app --app-dir backend --reload
```

Abra [http://127.0.0.1:8000](http://127.0.0.1:8000). A API interativa fica em
[http://127.0.0.1:8000/docs](http://127.0.0.1:8000/docs).

> **Somente demo local:** este adapter HTTP ainda não implementa autenticação,
> isolamento por tenant, rate limit nem quota por comprador. Não exponha os modos
> `openai` ou `auto` em uma interface pública: qualquer chamada aceita consumiria
> créditos da conta configurada. Mantenha o bind em `127.0.0.1` até esses controles
> existirem.

Por padrão, a interpretação continua local e não consome créditos. Para usar a
Responses API com Structured Outputs:

```powershell
$env:PROCUREMENT_INTERPRETER='openai'
$env:OPENAI_PROCUREMENT_MODEL='gpt-5.6-luna'
.\.venv\Scripts\python.exe -m uvicorn app.main:app --app-dir backend --reload
```

A chave é resolvida primeiro de `OPENAI_API_KEY` e, apenas para desenvolvimento
local, de `.secrets/openai_api_key.txt`. A pasta `.secrets/` está no `.gitignore`;
nunca coloque a chave no frontend, em commits ou em logs. Como este repositório está
dentro do OneDrive, o arquivo ignorado pelo Git ainda pode ser sincronizado pelo
OneDrive; use variável protegida ou secret manager fora da demo local. O modo `auto`
usa OpenAI quando houver chave e faz fallback para o interpretador local em falha
sanitizada.

Também é possível rodar o fluxo sem navegador:

```powershell
.\.venv\Scripts\python.exe scripts\demo_dev3.py
```

## Validar

```powershell
.\.venv\Scripts\python.exe -m pytest backend/tests -q
.\.venv\Scripts\python.exe -m ruff check backend
.\.venv\Scripts\python.exe -m ruff format --check backend
.\.venv\Scripts\python.exe -m mypy backend/app
node --check frontend/app.js
```

O teste pago é separado e opt-in:

```powershell
$env:RUN_OPENAI_LIVE_TESTS='1'
.\.venv\Scripts\python.exe -m pytest backend/tests/live -q
Remove-Item Env:RUN_OPENAI_LIVE_TESTS
```

Esse comando envia somente o briefing fixo do teste, sem acionar sourcing ou RFQ.

A decisão arquitetural, os contratos e as limitações estão em
[`docs/DEV3_PROTOTYPE.md`](docs/DEV3_PROTOTYPE.md).
