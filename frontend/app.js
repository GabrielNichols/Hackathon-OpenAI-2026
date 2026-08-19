function newIdempotencyKey() {
  if (globalThis.crypto?.randomUUID) return `buyer-${globalThis.crypto.randomUUID()}`;
  return `buyer-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

const state = { requestId: null, view: null, creationKey: newIdempotencyKey() };

const labels = {
  category: "Categoria",
  event_date: "Data",
  delivery_time: "Entrega",
  location_city: "Cidade",
  location_district: "Bairro",
  full_address: "Endereço",
  people_count: "Pessoas",
  maximum_total_cents: "Orçamento",
  vegetarian_count: "Vegetarianos",
  vegan_count: "Veganos",
  gluten_free_count: "Sem glúten",
  invoice_required: "Nota fiscal",
  no_single_use_plastic: "Sem plástico",
  response_deadline: "Prazo de resposta",
  desired_quote_count: "Cotações",
  approver_user_id: "Aprovador",
};

const statusLabels = {
  READY: "Pedido pronto para revisão",
  SOURCING: "Buscando fornecedores",
  RFQ_CREATED: "Convites de cotação preparados",
};

const stopReasonLabels = {
  AWAITING_PLAN_CONFIRMATION: "Aguardando sua confirmação",
  AWAITING_EXTERNAL_RESPONSE: "Aguardando propostas dos fornecedores",
  NO_ELIGIBLE_SUPPLIERS: "Revisão necessária",
};

const decisionLabels = {
  eligible: "Apto para cotação",
  excluded: "Não atende aos requisitos",
  needs_refresh: "Dados a confirmar",
};

const criterionLabels = {
  supplier_active: "Fornecedor ativo",
  category_match: "Categoria atendida",
  service_area_match: "Região atendida",
  capacity_sufficient: "Capacidade disponível",
  lead_time_sufficient: "Prazo atendido",
  critical_fields_current: "Dados atualizados",
  invoice_available: "Nota fiscal disponível",
  no_single_use_plastic: "Sem descartáveis de uso único",
  vegetarian_requirement: "Opções vegetarianas",
  vegan_requirement: "Opções veganas",
  gluten_free_requirement: "Opções sem glúten",
  maximum_total_cents: "Dentro do orçamento",
};

const eventLabels = {
  AGENT_RUN_STARTED: "Análise iniciada",
  PROCUREMENT_REQUEST_CREATED: "Solicitação criada",
  PROCUREMENT_MESSAGE_RECEIVED: "Pedido recebido",
  PROCUREMENT_INTERPRETED: "Pedido estruturado",
  PROCUREMENT_READY: "Pedido pronto para revisão",
  PROCUREMENT_PLAN_CREATED: "Plano de compra preparado",
  AGENT_RUN_PAUSED: "Aguardando confirmação",
  SOURCING_STARTED: "Busca de fornecedores iniciada",
  SUPPLIERS_EVALUATED: "Fornecedores avaliados",
  RFQ_ROUND_CREATED: "Convites de cotação preparados",
};

const byId = (id) => document.getElementById(id);

function node(tag, className, text) {
  const item = document.createElement(tag);
  if (className) item.className = className;
  if (text !== undefined && text !== null) item.textContent = String(text);
  return item;
}

function show(id, visible = true) {
  byId(id).hidden = !visible;
}

function formatValue(key, value) {
  if (value === null || value === undefined || value === "") return "A confirmar";
  if (key.endsWith("_cents")) return new Intl.NumberFormat("pt-BR", { style: "currency", currency: "BRL" }).format(value / 100);
  if (typeof value === "boolean") return value ? "Sim" : "Não";
  if (Array.isArray(value)) return value.join(", ") || "Nenhum";
  if (typeof value === "object") return JSON.stringify(value);
  return value;
}

async function api(path, options = {}) {
  const response = await fetch(path, {
    ...options,
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
  });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(payload.detail || payload.error?.message || "Não foi possível concluir a ação.");
  return payload;
}

function setBusy(busy, label = "Interpretar briefing") {
  const button = byId("send-button");
  const planReady = state.view?.stop_reason === "AWAITING_PLAN_CONFIRMATION";
  button.disabled = busy || planReady;
  button.textContent = busy ? "Interpretando…" : planReady ? "Plano pronto para revisão" : label;
  byId("confirm-plan").disabled = busy;
}

function renderAnswer(view) {
  const answer = byId("agent-answer");
  answer.classList.remove("error");
  if (view.clarification_question) {
    answer.textContent = view.clarification_question;
  } else if (view.stop_reason === "AWAITING_PLAN_CONFIRMATION") {
    answer.textContent = "O briefing está completo. Revise o plano antes de eu consultar o diretório de fornecedores.";
  } else if (view.stop_reason === "NO_ELIGIBLE_SUPPLIERS") {
    answer.textContent = "Nenhum fornecedor passou pelos requisitos conhecidos. Os motivos estão detalhados abaixo.";
  } else if (view.stop_reason === "AWAITING_EXTERNAL_RESPONSE") {
    answer.textContent = "Fornecedores selecionados e convites de cotação preparados. Aguardando o envio e as propostas.";
  } else {
    answer.textContent = "Estado atualizado com base nos eventos persistidos.";
  }
  show("agent-answer");
}

function renderSummary(view) {
  const draft = view.draft || {};
  const grid = byId("summary-grid");
  grid.replaceChildren();
  Object.entries(labels).forEach(([key, label]) => {
    const wrapper = node("div", "summary-item");
    wrapper.append(node("dt", null, label), node("dd", null, formatValue(key, draft[key])));
    grid.append(wrapper);
  });

  const evidenceList = byId("evidence-list");
  evidenceList.replaceChildren();
  Object.entries(view.evidence || {}).forEach(([field, excerpt]) => {
    const li = node("li");
    const strong = node("strong", null, `${labels[field] || field}: `);
    li.append(strong, document.createTextNode(String(excerpt)));
    evidenceList.append(li);
  });
  if (!evidenceList.children.length) evidenceList.append(node("li", null, "As informações identificadas no pedido aparecerão aqui."));
  show("summary-panel");
}

function renderPlan(view) {
  const panel = byId("plan-panel");
  const details = byId("plan-details");
  details.replaceChildren();
  if (!view.plan) {
    panel.hidden = true;
    return;
  }
  const planRows = [
    ["Fornecedores-alvo", view.plan.target_supplier_count],
    ["Prazo", formatValue("response_deadline", view.plan.response_deadline)],
    ["Requisitos obrigatórios", (view.plan.eliminatory_criteria || []).map((item) => criterionLabels[item] || item).join(", ")],
    ["Ajustes de proposta", view.plan.negotiation_enabled ? "Permitidos conforme política" : "Não previstos para este pedido"],
    ["Aprovação", "Necessária antes da contratação"],
  ];
  planRows.forEach(([label, value]) => {
    const row = node("div", "plan-row");
    row.append(node("span", null, label), node("strong", null, value));
    details.append(row);
  });
  const canConfirm = view.stop_reason === "AWAITING_PLAN_CONFIRMATION";
  show("confirm-plan", canConfirm);
  show("plan-panel");
}

function checkOutcome(check) {
  if (check.outcome) return String(check.outcome).toLowerCase();
  if (check.passed === true) return "pass";
  if (check.passed === false) return "fail";
  return "unknown";
}

function renderSourcing(view) {
  const results = view.eligibility_results || [];
  if (!results.length) {
    show("sourcing-panel", false);
    return;
  }
  const list = byId("supplier-list");
  list.replaceChildren();
  results.forEach((result) => {
    const card = node("article", "supplier-card");
    const identity = node("div");
    identity.append(
      node("span", `decision ${result.decision}`, decisionLabels[result.decision] || "Em análise"),
      node("h3", null, result.display_name || result.supplier_id),
    );
    const checks = node("div", "check-list");
    (result.checks || []).forEach((check) => {
      const outcome = checkOutcome(check);
      const item = node("div", `check ${outcome}`);
      const criterion = criterionLabels[check.criterion] || "Requisito avaliado";
      const outcomeLabel = outcome === "pass" ? "Atende" : outcome === "fail" ? "Não atende" : "A confirmar";
      item.textContent = `${criterion}: ${outcomeLabel}`;
      checks.append(item);
    });
    card.append(identity, checks);
    list.append(card);
  });
  byId("sourcing-counter").textContent = `${results.length} consultado${results.length === 1 ? "" : "s"}`;

  const handoff = byId("rfq-handoff");
  if (view.rfq_round_id) {
    handoff.replaceChildren(
      node("strong", null, "Convites de cotação preparados"),
      node("span", null, "Os fornecedores selecionados estão prontos para receber os convites e enviar suas propostas."),
    );
    handoff.hidden = false;
  } else {
    handoff.hidden = true;
  }
  show("sourcing-panel");
}

function renderTimeline(view) {
  const timeline = byId("timeline");
  timeline.replaceChildren();
  (view.timeline || []).forEach((event) => {
    const item = node("li");
    const code = node("code", null, eventLabels[event.event_type] || "Etapa atualizada");
    const actor = event.actor_type === "human" ? "Equipe de compras" : event.actor_type === "agent" ? "Canal Agente" : "Sistema";
    const description = node("p", null, actor);
    const time = node("time", null, event.occurred_at ? new Date(event.occurred_at).toLocaleTimeString("pt-BR") : "");
    item.append(code, description, time);
    timeline.append(item);
  });
  show("timeline-panel", timeline.children.length > 0);
}

function updateWorkflow(view) {
  const sourcing = (view.eligibility_results || []).length > 0;
  const rfq = Boolean(view.rfq_round_id);
  const plan = Boolean(view.plan);
  document.querySelectorAll("#workflow-list li").forEach((item) => item.classList.remove("active", "done"));
  const states = { request: true, plan, sourcing, rfq };
  let latest = "request";
  Object.entries(states).forEach(([step, reached]) => { if (reached) latest = step; });
  let passedLatest = false;
  document.querySelectorAll("#workflow-list li").forEach((item) => {
    const step = item.dataset.step;
    if (step === latest) { item.classList.add("active"); passedLatest = true; }
    else if (!passedLatest && states[step]) item.classList.add("done");
  });
}

function render(view) {
  state.view = view;
  state.requestId = view.request_id;
  byId("request-reference").textContent = "Solicitação criada e pronta para acompanhamento";
  byId("status-pill").textContent = stopReasonLabels[view.stop_reason] || statusLabels[view.status] || "Pedido em andamento";
  renderAnswer(view);
  renderSummary(view);
  renderPlan(view);
  renderSourcing(view);
  renderTimeline(view);
  updateWorkflow(view);
}

function showError(error) {
  const answer = byId("agent-answer");
  answer.textContent = /plan already exists/i.test(String(error.message))
    ? "O plano já está pronto. Revise-o e confirme para iniciar a busca de fornecedores."
    : "Não foi possível concluir esta etapa agora. Tente novamente.";
  answer.classList.add("error");
  show("agent-answer");
}

byId("message-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  setBusy(true);
  try {
    const payload = { message: byId("message").value.trim() };
    if (state.requestId) payload.request_id = state.requestId;
    const options = { method: "POST", body: JSON.stringify(payload) };
    if (!state.requestId) options.headers = { "Idempotency-Key": state.creationKey };
    const view = await api("/api/v1/procurement-requests/messages", options);
    render(view);
  } catch (error) {
    showError(error);
  } finally {
    setBusy(false);
  }
});

byId("confirm-plan").addEventListener("click", async () => {
  if (!state.requestId) return;
  setBusy(true);
  try {
    const view = await api(`/api/v1/procurement-requests/${state.requestId}/plan/confirm`, { method: "POST", body: "{}" });
    render(view);
  } catch (error) {
    showError(error);
  } finally {
    setBusy(false);
  }
});
