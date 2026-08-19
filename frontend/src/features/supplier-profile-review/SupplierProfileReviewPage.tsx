import { useCallback, useEffect, useRef, useState } from "react";

import { ReviewApiError } from "./api";
import {
  displayValue,
  labelForField,
  type CorrectionInput,
  type ReviewFieldStatus,
  type SupplierReviewApi,
  type SupplierReviewContext,
  type SupplierReviewField,
  type SupplierReviewSubmitResult,
} from "./model";
import "./supplier-profile-review.css";

interface SupplierProfileReviewPageProps {
  api: SupplierReviewApi;
  token: string;
}

interface StatusDefinition {
  label: string;
  symbol: string;
}

const STATUS: Readonly<Record<ReviewFieldStatus, StatusDefinition>> = {
  extracted: { label: "Extraído", symbol: "◇" },
  not_found: { label: "Não encontrado", symbol: "—" },
  needs_review: { label: "Revisão necessária", symbol: "!" },
  confirmed: { label: "Confirmado", symbol: "✓" },
  corrected: { label: "Corrigido", symbol: "↻" },
  not_applicable: { label: "Não se aplica", symbol: "∅" },
};

function fieldId(fieldName: string): string {
  return `review-field-${fieldName.replace(/[^a-zA-Z0-9_-]/g, "-")}`;
}

function StatusBadge({ status }: { status: ReviewFieldStatus }) {
  const definition = STATUS[status];
  return (
    <span className="review-status" data-state={status}>
      <span aria-hidden="true">{definition.symbol}</span>
      {definition.label}
    </span>
  );
}

function EvidencePanel({ field }: { field: SupplierReviewField }) {
  const evidence = field.evidence;
  if (!evidence) {
    return (
      <aside className="evidence-panel" aria-label={`Fonte de ${field.label}`}>
        <p className="evidence-title">Fonte</p>
        <p>Nenhuma evidência foi localizada no material.</p>
      </aside>
    );
  }

  const locations = [
    evidence.page === null ? null : `Página ${evidence.page}`,
    evidence.sheet ? `Planilha ${evidence.sheet}` : null,
    evidence.cellRange ? `Células ${evidence.cellRange}` : null,
  ].filter((value): value is string => value !== null);

  return (
    <aside className="evidence-panel" aria-label={`Fonte de ${field.label}`}>
      <div className="evidence-heading">
        <p className="evidence-title">Fonte original</p>
        {evidence.previewUrl ? (
          <a
            href={evidence.previewUrl}
            referrerPolicy="no-referrer"
            rel="noreferrer"
            target="_blank"
          >
            Abrir material
          </a>
        ) : null}
      </div>
      {evidence.excerpt ? <blockquote>“{evidence.excerpt}”</blockquote> : null}
      {locations.length > 0 ? <p className="source-location">{locations.join(" · ")}</p> : null}
    </aside>
  );
}

function editableValue(value: unknown): string {
  if (value === null || value === undefined) return "";
  if (typeof value === "boolean") return value ? "Sim" : "Não";
  if (Array.isArray(value)) return value.map(String).join("\n");
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}

function normalizeCorrection(raw: string, field: SupplierReviewField): unknown {
  const reference = field.normalizedValue ?? field.value;
  if (typeof reference === "boolean") {
    return ["sim", "true", "1"].includes(raw.trim().toLocaleLowerCase("pt-BR"));
  }
  if (typeof reference === "number") {
    const parsed = Number(raw.replace(",", "."));
    return Number.isFinite(parsed) ? parsed : raw.trim();
  }
  if (Array.isArray(reference)) {
    return raw
      .split(/\n|,/)
      .map((item) => item.trim())
      .filter(Boolean);
  }
  return raw.trim();
}

interface CorrectionEditorProps {
  field: SupplierReviewField;
  busy: boolean;
  onCancel: () => void;
  onSave: (correction: CorrectionInput) => Promise<void>;
}

function CorrectionEditor({ field, busy, onCancel, onSave }: CorrectionEditorProps) {
  const [value, setValue] = useState(editableValue(field.value));
  const [validation, setValidation] = useState<string | null>(null);
  const isList = Array.isArray(field.normalizedValue ?? field.value);

  return (
    <form
      className="correction-editor"
      onSubmit={(event) => {
        event.preventDefault();
        if (!value.trim()) {
          setValidation("Informe um valor ou use “Não se aplica”.");
          return;
        }
        const normalizedValue = normalizeCorrection(value, field);
        void onSave({
          expectedVersion: field.version,
          value: normalizedValue,
          normalizedValue,
        });
      }}
    >
      <label htmlFor={`${fieldId(field.fieldName)}-correction`}>
        Novo valor para {field.label}
      </label>
      {isList ? (
        <textarea
          id={`${fieldId(field.fieldName)}-correction`}
          onChange={(event) => setValue(event.target.value)}
          rows={4}
          value={value}
        />
      ) : (
        <input
          id={`${fieldId(field.fieldName)}-correction`}
          inputMode={
            typeof (field.normalizedValue ?? field.value) === "number"
              ? "decimal"
              : "text"
          }
          onChange={(event) => setValue(event.target.value)}
          value={value}
        />
      )}
      {validation ? <p className="field-error">{validation}</p> : null}
      <div className="editor-actions">
        <button className="button button-primary" disabled={busy} type="submit">
          {busy ? "Salvando…" : "Salvar correção"}
        </button>
        <button className="button button-quiet" disabled={busy} onClick={onCancel} type="button">
          Cancelar
        </button>
      </div>
    </form>
  );
}

interface ReviewFieldCardProps {
  field: SupplierReviewField;
  busyAction: string | null;
  editing: boolean;
  onConfirm: () => Promise<void>;
  onCorrect: (correction: CorrectionInput) => Promise<void>;
  onEdit: () => void;
  onCancelEdit: () => void;
  onNotApplicable: () => Promise<void>;
}

function ReviewFieldCard(props: ReviewFieldCardProps) {
  const { field, busyAction, editing } = props;
  const busy = busyAction !== null;
  const canConfirm =
    field.value !== null &&
    field.value !== undefined &&
    !["confirmed", "corrected", "not_applicable"].includes(field.status);
  const showOriginal =
    field.status === "corrected" &&
    field.originalValue !== undefined &&
    displayValue(field.originalValue) !== displayValue(field.value);

  return (
    <section
      aria-labelledby={`${fieldId(field.fieldName)}-title`}
      className="review-field-card"
      id={fieldId(field.fieldName)}
      role="group"
      tabIndex={-1}
    >
      <header className="field-heading">
        <div>
          <p className="field-eyebrow">{field.required ? "Obrigatório" : "Opcional"}</p>
          <h2 id={`${fieldId(field.fieldName)}-title`}>{field.label}</h2>
        </div>
        <StatusBadge status={field.status} />
      </header>

      <div className="field-content">
        <div className="field-value-panel">
          <p className="field-value-label">Valor atual</p>
          <p className="field-value">
            {field.status === "not_applicable" ? "Não se aplica" : displayValue(field.value)}
          </p>
          {showOriginal ? (
            <p className="original-value">
              Valor extraído originalmente: {displayValue(field.originalValue)}
            </p>
          ) : null}
          <p className="confidence">
            {field.confidence === null
              ? "Confiança não disponível"
              : `Confiança da extração: ${Math.round(field.confidence * 100)}%`}
          </p>
        </div>
        <EvidencePanel field={field} />
      </div>

      {editing ? (
        <CorrectionEditor
          busy={busy}
          field={field}
          onCancel={props.onCancelEdit}
          onSave={props.onCorrect}
        />
      ) : (
        <div className="field-actions">
          {canConfirm ? (
            <button
              className="button button-primary"
              disabled={busy}
              onClick={() => void props.onConfirm()}
              type="button"
            >
              {busyAction === "confirm" ? "Confirmando…" : "Confirmar"}
            </button>
          ) : null}
          <button
            className="button button-secondary"
            disabled={busy}
            onClick={props.onEdit}
            type="button"
          >
            Corrigir
          </button>
          {field.allowNotApplicable && field.status !== "not_applicable" ? (
            <button
              className="button button-quiet"
              disabled={busy}
              onClick={() => void props.onNotApplicable()}
              type="button"
            >
              {busyAction === "not_applicable" ? "Salvando…" : "Não se aplica"}
            </button>
          ) : null}
        </div>
      )}
    </section>
  );
}

function SafeLinkError({ error, onRetry }: { error: unknown; onRetry: () => void }) {
  const expired = error instanceof ReviewApiError && error.code === "LINK_EXPIRED";
  const invalid = error instanceof ReviewApiError && error.code === "LINK_INVALID";
  return (
    <main className="review-shell state-shell">
      <div className="state-card" role="alert">
        <span className="state-symbol" aria-hidden="true">{expired ? "⌛" : "○"}</span>
        <h1>
          {expired
            ? "Este link expirou"
            : invalid
              ? "Link inválido ou indisponível"
              : "Não foi possível carregar a revisão"}
        </h1>
        <p>
          {expired || invalid
            ? "Peça ao responsável pelo cadastro um novo link seguro. Nenhum dado foi alterado."
            : "Verifique sua conexão e tente novamente. Nenhum dado foi alterado."}
        </p>
        {!expired && !invalid ? (
          <button className="button button-primary" onClick={onRetry} type="button">
            Tentar novamente
          </button>
        ) : null}
      </div>
    </main>
  );
}

function mergeOriginalValues(
  next: SupplierReviewContext,
  originalValues: Map<string, unknown>,
): SupplierReviewContext {
  return {
    ...next,
    fields: next.fields.map((field) => {
      if (!originalValues.has(field.fieldName)) {
        originalValues.set(field.fieldName, field.originalValue ?? field.value);
      }
      return { ...field, originalValue: originalValues.get(field.fieldName) };
    }),
  };
}

export function SupplierProfileReviewPage({ api, token }: SupplierProfileReviewPageProps) {
  const [review, setReview] = useState<SupplierReviewContext | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<unknown>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [busyField, setBusyField] = useState<string | null>(null);
  const [busyAction, setBusyAction] = useState<string | null>(null);
  const [editingField, setEditingField] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [submitted, setSubmitted] = useState<SupplierReviewSubmitResult | null>(null);
  const originalValues = useRef(new Map<string, unknown>());

  const load = useCallback(
    async (signal?: AbortSignal) => {
      if (!token) {
        setLoadError(new ReviewApiError("LINK_INVALID", "invalid", 403));
        setLoading(false);
        return;
      }
      try {
        const next = await api.getReview(token, signal);
        if (!signal?.aborted) {
          setReview(mergeOriginalValues(next, originalValues.current));
          setLoadError(null);
        }
      } catch (error) {
        if (!signal?.aborted) setLoadError(error);
      } finally {
        if (!signal?.aborted) setLoading(false);
      }
    },
    [api, token],
  );

  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    void load(controller.signal);
    return () => controller.abort();
  }, [load]);

  async function refresh(): Promise<void> {
    const next = await api.getReview(token);
    setReview(mergeOriginalValues(next, originalValues.current));
  }

  async function mutateField(
    field: SupplierReviewField,
    action: string,
    mutation: () => Promise<unknown>,
  ): Promise<void> {
    setBusyField(field.fieldName);
    setBusyAction(action);
    setActionError(null);
    try {
      await mutation();
      await refresh();
      setEditingField(null);
    } catch (error) {
      if (error instanceof ReviewApiError && error.code === "OPTIMISTIC_LOCK_CONFLICT") {
        setActionError("Este campo mudou em outra sessão. Recarregamos o valor mais recente.");
        await refresh();
      } else {
        setActionError("Não foi possível salvar este campo. Tente novamente.");
      }
    } finally {
      setBusyField(null);
      setBusyAction(null);
    }
  }

  if (loading) {
    return (
      <main aria-busy="true" className="review-shell state-shell">
        <div className="state-card loading-card" role="status">
          <span className="loading-dot" aria-hidden="true" />
          <p>Carregando sua revisão segura…</p>
        </div>
      </main>
    );
  }
  if (loadError || !review) {
    return <SafeLinkError error={loadError} onRetry={() => void load()} />;
  }
  if (submitted) {
    return (
      <main className="review-shell state-shell">
        <div className="state-card success-card" role="status">
          <span className="state-symbol" aria-hidden="true">✓</span>
          <p className="eyebrow">Revisão recebida</p>
          <h1>Perfil enviado com sucesso</h1>
          <p>
            O estado confirmado pelo servidor é <strong>{submitted.status}</strong>. Você já pode
            fechar esta página.
          </p>
        </div>
      </main>
    );
  }

  const reviewed = review.fields.filter((field) =>
    ["confirmed", "corrected", "not_applicable"].includes(field.status),
  ).length;
  const missingLabels = review.missingRequiredFields.map((name) =>
    review.fields.find((field) => field.fieldName === name)?.label ?? labelForField(name),
  );

  return (
    <main className="review-shell">
      <header className="review-hero">
        <p className="eyebrow">Revisão segura · {review.supplierDisplayName}</p>
        <h1>Revise seu perfil comercial</h1>
        <p>
          Estes dados foram extraídos com IA. Confira cada valor na fonte antes de confirmar ou
          corrigir.
        </p>
        <div className="progress-summary" aria-label={`${reviewed} de ${review.fields.length} campos revisados`}>
          <span>{reviewed} de {review.fields.length} revisados</span>
          <progress max={Math.max(review.fields.length, 1)} value={reviewed} />
        </div>
      </header>

      {actionError ? <div className="action-error" role="alert">{actionError}</div> : null}

      <div className="fields-list">
        {review.fields.map((field) => (
          <ReviewFieldCard
            busyAction={busyField === field.fieldName ? busyAction : null}
            editing={editingField === field.fieldName}
            field={field}
            key={field.fieldName}
            onCancelEdit={() => setEditingField(null)}
            onConfirm={() =>
              mutateField(field, "confirm", () =>
                api.confirmField(token, field.fieldName, field.version),
              )
            }
            onCorrect={(correction) =>
              mutateField(field, "correct", () =>
                api.correctField(token, field.fieldName, correction),
              )
            }
            onEdit={() => setEditingField(field.fieldName)}
            onNotApplicable={() =>
              mutateField(field, "not_applicable", () =>
                api.markNotApplicable(token, field.fieldName, field.version),
              )
            }
          />
        ))}
      </div>

      <section className="submit-panel">
        {missingLabels.length > 0 ? (
          <div aria-label="Pendências da revisão" className="submit-blockers" role="region">
            <h2>
              {missingLabels.length} {missingLabels.length === 1 ? "campo obrigatório pendente" : "campos obrigatórios pendentes"}
            </h2>
            <p>Revise estes campos antes de enviar:</p>
            <div className="blocker-links">
              {review.missingRequiredFields.map((name, index) => (
                <button
                  key={name}
                  onClick={() => document.getElementById(fieldId(name))?.focus()}
                  type="button"
                >
                  {missingLabels[index]}
                </button>
              ))}
            </div>
          </div>
        ) : (
          <div className="ready-message" role="status">Todos os campos obrigatórios foram revisados.</div>
        )}
        <button
          aria-describedby={missingLabels.length ? "submit-help" : undefined}
          className="button button-submit"
          disabled={!review.canSubmit || missingLabels.length > 0 || submitting}
          onClick={() => {
            setSubmitting(true);
            setActionError(null);
            void api
              .submitReview(token)
              .then(setSubmitted)
              .catch((error: unknown) => {
                if (error instanceof ReviewApiError && error.code === "REVIEW_INCOMPLETE") {
                  const missing = error.details.missing_fields;
                  if (Array.isArray(missing)) {
                    setReview((current) =>
                      current
                        ? {
                            ...current,
                            canSubmit: false,
                            missingRequiredFields: missing.filter(
                              (value): value is string => typeof value === "string",
                            ),
                          }
                        : current,
                    );
                  }
                }
                setActionError("A revisão ainda não pôde ser enviada. Confira as pendências.");
              })
              .finally(() => setSubmitting(false));
          }}
          type="button"
        >
          {submitting ? "Enviando revisão…" : "Enviar revisão"}
        </button>
        {missingLabels.length ? <p id="submit-help">O envio será liberado após revisar os campos acima.</p> : null}
      </section>
    </main>
  );
}
