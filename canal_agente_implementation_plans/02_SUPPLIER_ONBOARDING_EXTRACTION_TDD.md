# Implementation Plan 02 — Onboarding de Fornecedores, Extração e Confirmação

**Projeto:** Canal Agente  
**Responsável:** Dev 2 — Supplier Intelligence  
**Branch:** `feat/supplier-onboarding-extraction`  
**PR base:** `main`, rebasedar no commit `contracts-v0` do Dev 1 antes do merge  
**Missão:** transformar materiais reais e desestruturados de pequenos fornecedores em perfis comerciais estruturados, rastreáveis e confirmados.  
**Resultado esperado:** um fornecedor envia PDF, imagem, planilha ou texto; a IA extrai campos com evidência; o fornecedor corrige/confirma por link; somente então o perfil fica elegível para sourcing.

---

## 1. Contexto essencial do produto

O Canal Agente atende fornecedores que hoje operam por WhatsApp, PDF, planilha, catálogo ou indicação. O produto não pode exigir que eles criem um e-commerce nem pode inventar estoque, preço ou capacidade.

Esta branch implementa a primeira metade da prova central:

```text
material real
→ armazenamento e hash
→ extração estruturada
→ evidência por campo
→ revisão no celular
→ confirmação/versionamento
→ fornecedor ACTIVE e pesquisável
```

A diferenciação do projeto depende desta etapa funcionar de verdade. Sem ela, o sistema vira somente um agente de cotação sobre fornecedores previamente cadastrados.

---

## 2. Limites da branch

### Esta branch deve implementar

- cadastro mínimo de fornecedor e contato;
- upload de PDF, imagem e planilha;
- entrada de texto copiado de WhatsApp;
- armazenamento, hash e metadados do material;
- pipeline assíncrono de extração;
- provider de extração por IA atrás de interface;
- fake provider determinístico para testes;
- schema canônico para alimentação corporativa;
- proveniência e confiança por campo;
- regra explícita de `not_found`;
- tela lado a lado de revisão;
- link assinado para fornecedor;
- confirmação, correção e versionamento;
- cálculo de completude e ativação;
- implementação de `SupplierDirectoryPort` consumida pelo Dev 3;
- eventos de auditoria do fluxo;
- seed de três fornecedores de demonstração com materiais autorizados.

### Esta branch não deve implementar

- requisição do comprador;
- orquestrador do agente;
- RFQ e envio;
- resposta de cotação;
- negociação;
- ranking final;
- aprovação ou award;
- scraping amplo de fornecedores;
- homologação jurídica completa;
- score opaco de qualidade.

---

## 3. Estrutura e propriedade de arquivos

```text
backend/app/modules/suppliers/
  api/
  application/
  domain_extensions/
  extraction/
  persistence/
  search/
frontend/src/features/supplier-onboarding/
frontend/src/features/supplier-profile-review/
backend/tests/unit/suppliers/
backend/tests/contract/suppliers/
backend/tests/integration/suppliers/
frontend/src/features/**/__tests__/
```

Dev 2 não altera `backend/app/contracts/**` nem as máquinas de estado centrais. Caso o contrato v0 seja insuficiente, abra uma proposta pequena de alteração com teste de contrato; não altere silenciosamente.

Cada feature deve exportar seu router/componente. Não edite o router central; o Dev 4 fará o wiring final.

---

## 4. Modelo canônico de fornecedor

### 4.1 Campos cadastrais

```text
supplier_id
organization_id
legal_name
trade_name
cnpj
contact_name
contact_email
contact_phone
status
last_confirmed_at
```

### 4.2 Capacidade comercial para alimentação

```text
categories[]
service_cities[]
service_districts[]
minimum_people
maximum_people
lead_time_hours
delivery_windows[]
invoice_available
pricing_model
capacity_notes
cancellation_terms
sustainability_tags[]
```

### 4.3 Restrições e capacidades alimentares

```text
vegetarian_supported
vegan_supported
gluten_free_supported
cross_contamination_warning
other_dietary_capabilities[]
```

Nunca interpretar “sem glúten” como ausência certificada de contaminação cruzada sem evidência explícita.

### 4.4 Ofertas

```text
offer_id
name
description
price_type: per_person | fixed_package | custom_quote
base_price_cents
minimum_quantity
included_items[]
optional_items[]
delivery_fee_rule
valid_from
valid_until
```

No MVP, oferta é orientação comercial. Disponibilidade e preço final ainda serão confirmados por RFQ.

---

## 5. Proveniência por campo

Cada campo crítico deve ser representado como dado + evidência, nunca apenas como coluna final.

```python
class ExtractedFieldDTO(BaseModel):
    field_name: str
    value: Any | None
    normalized_value: Any | None
    status: Literal["extracted", "not_found", "needs_review", "confirmed", "corrected"]
    confidence: float | None
    source_document_id: str
    source_page: int | None
    source_sheet: str | None
    source_cell_range: str | None
    source_excerpt: str | None
    extraction_run_id: str
    confirmed_by: str | None
    confirmed_at: datetime | None
    version: int
```

### Regras

- campo crítico sem evidência deve ser `not_found`;
- confidence baixa deve gerar `needs_review`, não uma confirmação automática;
- valor corrigido preserva a versão extraída anterior;
- fonte original nunca é sobrescrita;
- qualquer normalização deve guardar valor bruto e normalizado;
- preço usa centavos inteiros;
- evidência de imagem/PDF deve apontar página e trecho quando disponível;
- planilha deve apontar sheet e intervalo de célula.

---

## 6. Pipeline de ingestão

### 6.1 Tipos P0

- `application/pdf`
- `image/png`
- `image/jpeg`
- `.xlsx`
- texto simples

### 6.2 Etapas

```text
RECEIVED
→ VALIDATED
→ STORED
→ EXTRACTION_QUEUED
→ EXTRACTING
→ EXTRACTED | EXTRACTION_FAILED
→ AWAITING_SUPPLIER_REVIEW
```

### 6.3 Validação

- MIME e extensão compatíveis;
- tamanho máximo configurável;
- hash SHA-256;
- rejeitar arquivo vazio;
- manter original mesmo se extração falhar;
- deduplicar por tenant + hash sem perder a referência de upload;
- sanitizar nome de arquivo;
- não registrar conteúdo sensível em logs.

### 6.4 Assinatura do provider de extração

```python
class SupplierExtractionPort(Protocol):
    async def extract(
        self,
        document: SourceDocumentDTO,
        schema: SupplierExtractionSchema,
    ) -> SupplierExtractionResultDTO: ...
```

Implementações:

- `FakeSupplierExtractionProvider` para testes;
- `LLMSupplierExtractionProvider` para execução real;
- parser determinístico auxiliar para planilhas, quando possível.

O provider real deve usar saída estruturada validada por schema. Não permitir texto livre ser persistido diretamente como campo confirmado.

---

## 7. Fluxo de revisão do fornecedor

### 7.1 APIs da feature

```text
POST /api/v1/suppliers
POST /api/v1/suppliers/{supplier_id}/materials
GET  /api/v1/suppliers/{supplier_id}/materials/{document_id}
POST /api/v1/suppliers/{supplier_id}/extractions
GET  /api/v1/supplier-review/{token}
POST /api/v1/supplier-review/{token}/fields/{field_name}/confirm
POST /api/v1/supplier-review/{token}/fields/{field_name}/correct
POST /api/v1/supplier-review/{token}/submit
GET  /api/v1/suppliers/{supplier_id}
```

### 7.2 Requisitos de UX

A tela de revisão deve:

- funcionar no celular;
- mostrar valor extraído;
- mostrar fonte e confiança;
- permitir confirmar, corrigir ou marcar não aplicável;
- destacar campos obrigatórios faltantes;
- impedir submissão incompleta sem explicar o bloqueio;
- mostrar claramente “extraído”, “confirmado” e “corrigido”;
- não exigir criação de senha no MVP;
- manter o link inválido/expirado em uma tela segura e explícita.

### 7.3 Ativação

Campos mínimos confirmados:

- identidade comercial;
- contato;
- categoria;
- região atendida;
- quantidade mínima;
- capacidade aproximada;
- antecedência mínima;
- emissão de NF;
- restrições suportadas;
- forma de precificação;
- data de atualização.

O serviço de aplicação envia comando de ativação ao aggregate do Dev 1. Não atualize `status = ACTIVE` diretamente no ORM.

---

## 8. Contrato de busca para o Dev 3

Implementar `SupplierDirectoryPort` conforme o contract freeze.

### Entrada

```python
class SupplierSearchCriteria(BaseModel):
    tenant_id: str
    category: str
    city: str
    district: str | None
    event_date: date
    delivery_time: time | None
    people_count: int
    invoice_required: bool
    dietary_requirements: dict[str, int]
    mandatory_tags: list[str]
    maximum_lead_time_hours: int | None
```

### Saída

```python
class SupplierCandidateDTO(BaseModel):
    supplier_id: str
    display_name: str
    status: str
    categories: list[str]
    service_areas: list[str]
    minimum_people: int | None
    maximum_people: int | None
    lead_time_hours: int | None
    invoice_available: bool | None
    dietary_capabilities: dict[str, str]
    sustainability_tags: list[str]
    last_confirmed_at: datetime | None
    evidence_refs: list[str]
    missing_fields: list[str]
```

### Regra importante

A busca pode ampliar recall, mas não decide elegibilidade final. Ela retorna dados estruturados e evidências; o filtro determinístico do Dev 3 classifica incluído/excluído.

Fornecedores não `ACTIVE` não devem ser retornados como elegíveis. Opcionalmente podem aparecer em um canal separado `needs_refresh`, nunca misturados.

---

## 9. Eventos desta branch

Emitir pelo core do Dev 1:

```text
SUPPLIER_CREATED
SOURCE_DOCUMENT_STORED
SUPPLIER_EXTRACTION_STARTED
SUPPLIER_EXTRACTION_COMPLETED
SUPPLIER_EXTRACTION_FAILED
SUPPLIER_REVIEW_LINK_CREATED
SUPPLIER_FIELD_CONFIRMED
SUPPLIER_FIELD_CORRECTED
SUPPLIER_REVIEW_SUBMITTED
SUPPLIER_ACTIVATED
SUPPLIER_ACTIVATION_BLOCKED
SUPPLIER_PROFILE_EXPIRED
```

Payload deve conter IDs e metadados, não o documento inteiro.

---

## 10. Estratégia TDD acelerada

### Regra

Escreva primeiro testes do comportamento e use provider fake. Integre o modelo real somente quando o pipeline determinístico estiver verde.

### Camadas de teste

#### Unitários

- normalização de preço e quantidade;
- mapeamento de dieta;
- completude;
- elegibilidade para ativação;
- versionamento de correções;
- seleção de evidência;
- deduplicação por hash.

#### Contrato

- schema de saída do provider;
- implementação de `SupplierDirectoryPort`;
- serialização de DTOs;
- API de review;
- contrato de storage.

#### Integração

- upload → armazenamento → fila → extração fake → persistência;
- token válido/expirado;
- confirmação → aggregate → event log;
- consulta de fornecedor ativo.

#### Frontend

- render de evidência;
- confirmação de campo;
- correção;
- erro de link expirado;
- bloqueio de submit com campos obrigatórios.

### Testes obrigatórios

```text
test_upload_records_sha256_and_original_metadata
test_empty_file_is_rejected
test_extraction_failure_keeps_original_document
test_duplicate_document_reuses_blob_without_losing_upload_reference
test_missing_critical_field_is_persisted_as_not_found
test_low_confidence_field_requires_review
test_extracted_field_keeps_raw_and_normalized_values
test_price_is_normalized_to_integer_cents
test_correction_creates_new_version_and_preserves_original
test_supplier_review_link_rejects_wrong_supplier
test_supplier_review_link_rejects_expired_token
test_supplier_cannot_submit_review_with_missing_required_fields
test_supplier_becomes_active_only_after_real_review_submission
test_supplier_directory_returns_only_active_confirmed_suppliers
test_supplier_directory_never_marks_unknown_invoice_status_as_true
test_gluten_free_does_not_imply_no_cross_contamination
test_supplier_search_result_contains_evidence_refs
```

### Avaliação da IA

Criar dataset de teste com fixtures autorizadas:

- PDFs com tabelas;
- imagem de cardápio;
- planilha;
- texto de WhatsApp;
- campo ausente;
- preço ambíguo;
- informação contraditória.

Métricas mínimas executáveis:

- precisão de preço;
- precisão de quantidade mínima;
- detecção de `not_found`;
- hallucination rate de campos críticos;
- schema-valid rate.

Não publicar número de acurácia sem rodar o conjunto e guardar o resultado.

---

## 11. Plano de execução por waves

### Wave A — Schemas e testes

- modelar campos canônicos;
- criar fixtures e expected outputs;
- escrever contract tests do provider;
- implementar fake provider.

### Wave B — Upload e storage

- endpoints;
- hash e metadata;
- storage adapter;
- fila/status;
- testes de erro e deduplicação.

### Wave C — Extração

- parser de planilha;
- adapter de IA;
- validação estruturada;
- persistência de proveniência;
- tela de status.

### Wave D — Review e ativação

- token assinado do Dev 1;
- UI mobile;
- confirmação/correção;
- submissão;
- ativação por command handler.

### Wave E — Directory contract e demo data

- busca interna;
- evidence refs;
- seeds de fornecedor;
- contract tests consumíveis pelo Dev 3.

---

## 12. Uso de multiagents pelo Dev 2

### Subagente A — Fixture/Test Author

- prepara documentos de teste autorizados;
- escreve expected outputs e testes vermelhos;
- mede hallucination rate;
- não implementa provider real.

### Subagente B — Ingestion/Storage

- implementa uploads, hash, storage, status e retries;
- não toca UI nem prompts.

### Subagente C — Extraction/Normalization

- implementa schema, provider fake, provider real e normalizadores;
- trabalha apenas contra contract tests definidos.

### Subagente D — Supplier Portal

- implementa review mobile, evidência, correção e submit;
- usa APIs/fakes; não altera domínio central.

### Subagente E — Adversarial Reviewer

- tenta produzir campo inventado, token reutilizado, confirmação indevida e cross-tenant leak;
- adiciona testes de regressão.

### Coordenação

- A começa pelos contratos;
- B e C podem trabalhar em paralelo;
- D usa MSW/fakes até APIs estabilizarem;
- E revisa depois do primeiro fluxo verde;
- líder integra e entrega `SupplierDirectoryPort` antes do PR final.

---

## 13. Dados reais e demo

Para a demo, preparar no mínimo três fornecedores reais ou autorizados:

1. um com PDF;
2. um com imagem ou texto;
3. um com planilha.

Pelo menos um campo deve ser corrigido pelo fornecedor para provar versionamento.

Não incluir em seed público:

- telefone pessoal sem autorização;
- CNPJ ou documento sensível desnecessário;
- proposta privada não autorizada;
- avaliações inventadas.

Um replay pode ser usado somente se derivar de execução real e estiver marcado como replay.

---

## 14. Critérios de aceite da branch

- [ ] Quatro formatos P0 entram no pipeline.
- [ ] Documento original permanece disponível após falha.
- [ ] Todo campo crítico possui evidência ou `not_found`.
- [ ] Nenhum campo vira “confirmado” por causa da confiança do modelo.
- [ ] Fornecedor consegue revisar pelo celular sem conta complexa.
- [ ] Correções são versionadas.
- [ ] Link expira e é tenant-bound.
- [ ] Ativação passa pela máquina de estado.
- [ ] `SupplierDirectoryPort` passa contract tests.
- [ ] Busca não retorna fornecedor incompleto como elegível.
- [ ] Dataset de avaliação pode ser executado por comando único.
- [ ] Frontend distingue visualmente extraído, confirmado e corrigido.
- [ ] Testes, lint e tipos passam.

---

## 15. Commits sugeridos

```text
test(suppliers): define canonical extraction fixtures and contracts
feat(suppliers): add material upload, hashing and storage lifecycle
feat(suppliers): add deterministic spreadsheet normalization
feat(suppliers): add structured AI extraction adapter with provenance
test(suppliers): cover not_found and critical-field hallucination cases
feat(suppliers): add signed mobile review flow
feat(suppliers): version confirmations and corrections
feat(suppliers): activate only complete confirmed profiles
feat(suppliers): implement supplier directory contract
feat(suppliers-ui): add evidence-first review experience
docs(suppliers): add demo data and evaluation instructions
```

---

## 16. Handoff

### Para Dev 3

Entregar:

- implementação pronta de `SupplierDirectoryPort`;
- fixture de candidatos ativos, excluídos e desatualizados;
- campos e evidence refs estáveis;
- exemplos de busca por alimentação corporativa.

### Para Dev 4

Entregar:

- dados de contato autorizados;
- `supplier_id` e contato principal;
- componente/rota base do portal do fornecedor;
- serviço para validar token e recuperar contexto do fornecedor.

---

## 17. Crítica da frente

### Ponto positivo

Esta é a parte mais diferenciadora do Canal Agente: mostra um fornecedor “invisível” tornando-se operacionalmente acessível sem migrar para um e-commerce.

### Ponto negativo

Extração multimodal pode consumir toda a implementação e ainda falhar em layouts exóticos. O MVP deve suportar muito bem os documentos reais escolhidos e falhar de forma honesta em outros, marcando `needs_review` ou `not_found`, em vez de tentar parecer universal.

### Decisão de escopo

Priorize proveniência, revisão e ausência de alucinação sobre quantidade de formatos ou automação total.
