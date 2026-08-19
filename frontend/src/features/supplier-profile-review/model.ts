export type ReviewFieldStatus =
  | "extracted"
  | "not_found"
  | "needs_review"
  | "confirmed"
  | "corrected"
  | "not_applicable";

export type ReviewDecision = "confirmed" | "corrected" | "not_applicable";

export interface FieldEvidence {
  documentId: string;
  page: number | null;
  sheet: string | null;
  cellRange: string | null;
  excerpt: string | null;
  previewUrl?: string;
}

export interface SupplierReviewField {
  fieldName: string;
  label: string;
  value: unknown;
  normalizedValue: unknown;
  originalValue?: unknown;
  status: ReviewFieldStatus;
  confidence: number | null;
  required: boolean;
  allowNotApplicable: boolean;
  version: number;
  evidence: FieldEvidence | null;
}

export interface SupplierReviewContext {
  supplierDisplayName: string;
  profileVersion: number;
  status: string;
  fields: SupplierReviewField[];
  missingRequiredFields: string[];
  canSubmit: boolean;
}

export interface CorrectionInput {
  expectedVersion: number;
  value: unknown;
  normalizedValue: unknown;
}

export interface ReviewMutationResult {
  fieldName: string;
  decision: ReviewDecision;
  version: number;
}

export interface SupplierReviewSubmitResult {
  status: string;
  reviewSubmissionId: string | null;
}

export interface SupplierReviewApi {
  getReview(token: string, signal?: AbortSignal): Promise<SupplierReviewContext>;
  confirmField(
    token: string,
    fieldName: string,
    expectedVersion: number,
  ): Promise<ReviewMutationResult>;
  correctField(
    token: string,
    fieldName: string,
    correction: CorrectionInput,
  ): Promise<ReviewMutationResult>;
  markNotApplicable(
    token: string,
    fieldName: string,
    expectedVersion: number,
  ): Promise<ReviewMutationResult>;
  submitReview(token: string): Promise<SupplierReviewSubmitResult>;
}

export const FIELD_LABELS: Readonly<Record<string, string>> = {
  legal_name: "Razão social",
  trade_name: "Nome comercial",
  cnpj: "CNPJ",
  contact_name: "Nome do contato",
  contact_email: "E-mail",
  contact_phone: "Telefone",
  categories: "Categorias",
  service_cities: "Cidades atendidas",
  service_districts: "Bairros atendidos",
  minimum_people: "Quantidade mínima de pessoas",
  maximum_people: "Capacidade aproximada",
  lead_time_hours: "Antecedência mínima",
  delivery_windows: "Janelas de entrega",
  invoice_available: "Emite nota fiscal",
  pricing_model: "Forma de precificação",
  capacity_notes: "Observações de capacidade",
  cancellation_terms: "Termos de cancelamento",
  sustainability_tags: "Práticas de sustentabilidade",
  vegetarian_supported: "Opção vegetariana",
  vegan_supported: "Opção vegana",
  gluten_free_supported: "Opção sem glúten",
  cross_contamination_warning: "Aviso de contaminação cruzada",
  other_dietary_capabilities: "Outras capacidades alimentares",
};

export function labelForField(fieldName: string): string {
  return (
    FIELD_LABELS[fieldName] ??
    fieldName
      .split("_")
      .filter(Boolean)
      .map((part) => `${part.charAt(0).toUpperCase()}${part.slice(1)}`)
      .join(" ")
  );
}

export function displayValue(value: unknown): string {
  if (value === null || value === undefined || value === "") return "Não encontrado";
  if (typeof value === "boolean") return value ? "Sim" : "Não";
  if (Array.isArray(value)) return value.map(displayValue).join(", ");
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}
