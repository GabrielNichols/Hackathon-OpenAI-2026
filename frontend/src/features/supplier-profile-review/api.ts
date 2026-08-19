import {
  labelForField,
  type ReviewDecision,
  type ReviewFieldStatus,
  type ReviewMutationResult,
  type SupplierReviewApi,
  type SupplierReviewContext,
  type SupplierReviewField,
} from "./model";

export type ReviewErrorCode =
  | "LINK_EXPIRED"
  | "LINK_INVALID"
  | "REVIEW_INCOMPLETE"
  | "OPTIMISTIC_LOCK_CONFLICT"
  | "NETWORK_ERROR"
  | "UNEXPECTED_RESPONSE";

export class ReviewApiError extends Error {
  readonly code: ReviewErrorCode | string;
  readonly status: number;
  readonly details: Readonly<Record<string, unknown>>;

  constructor(
    code: ReviewErrorCode | string,
    message: string,
    status: number,
    details: Readonly<Record<string, unknown>> = {},
  ) {
    super(message);
    this.name = "ReviewApiError";
    this.code = code;
    this.status = status;
    this.details = details;
  }
}

type FetchLike = (
  input: RequestInfo | URL,
  init?: RequestInit,
) => Promise<Response>;

interface FetchReviewApiOptions {
  baseUrl?: string;
  fetcher?: FetchLike;
  idempotencyKey?: () => string;
}

function record(value: unknown): Record<string, unknown> {
  return typeof value === "object" && value !== null
    ? (value as Record<string, unknown>)
    : {};
}

function stringValue(value: unknown, fallback = ""): string {
  return typeof value === "string" ? value : fallback;
}

function numberValue(value: unknown, fallback: number): number {
  return typeof value === "number" && Number.isFinite(value) ? value : fallback;
}

function stringList(value: unknown): string[] {
  return Array.isArray(value)
    ? value.filter((item): item is string => typeof item === "string")
    : [];
}

const REVIEW_STATUSES = new Set<ReviewFieldStatus>([
  "extracted",
  "not_found",
  "needs_review",
  "confirmed",
  "corrected",
  "not_applicable",
]);

function normalizeStatus(value: unknown): ReviewFieldStatus {
  return typeof value === "string" && REVIEW_STATUSES.has(value as ReviewFieldStatus)
    ? (value as ReviewFieldStatus)
    : "needs_review";
}

function normalizeField(
  rawValue: unknown,
  requiredFields: ReadonlySet<string>,
): SupplierReviewField {
  const raw = record(rawValue);
  const fieldName = stringValue(raw.field_name, stringValue(raw.fieldName));
  const page = raw.source_page ?? raw.page;
  const documentId = stringValue(
    raw.source_document_id,
    stringValue(raw.document_id),
  );
  const excerpt = raw.source_excerpt ?? raw.excerpt;
  const evidence = documentId
    ? {
        documentId,
        page: typeof page === "number" ? page : null,
        sheet:
          typeof raw.source_sheet === "string" ? raw.source_sheet : null,
        cellRange:
          typeof raw.source_cell_range === "string"
            ? raw.source_cell_range
            : null,
        excerpt: typeof excerpt === "string" ? excerpt : null,
        ...(typeof raw.evidence_url === "string"
          ? { previewUrl: raw.evidence_url }
          : {}),
      }
    : null;

  return {
    fieldName,
    label: stringValue(raw.label, labelForField(fieldName)),
    value: raw.value,
    normalizedValue: raw.normalized_value ?? raw.normalizedValue,
    ...(Object.hasOwn(raw, "original_value")
      ? { originalValue: raw.original_value }
      : {}),
    status: normalizeStatus(raw.decision ?? raw.status),
    confidence:
      typeof raw.confidence === "number" ? raw.confidence : null,
    required:
      typeof raw.required === "boolean"
        ? raw.required
        : requiredFields.has(fieldName),
    allowNotApplicable:
      typeof raw.allow_not_applicable === "boolean"
        ? raw.allow_not_applicable
        : typeof raw.allowNotApplicable === "boolean"
          ? raw.allowNotApplicable
          : true,
    version: numberValue(raw.version, 1),
    evidence,
  };
}

export function normalizeReviewContext(payload: unknown): SupplierReviewContext {
  const raw = record(payload);
  const missingRequiredFields = stringList(
    raw.missing_required_fields ?? raw.missingRequiredFields,
  );
  const required = new Set([
    ...stringList(raw.required_fields ?? raw.requiredFields),
    ...missingRequiredFields,
  ]);
  const rawFields = Array.isArray(raw.fields) ? raw.fields : [];
  const fields = rawFields.map((field) => normalizeField(field, required));
  const canSubmit =
    typeof raw.can_submit === "boolean"
      ? raw.can_submit
      : typeof raw.canSubmit === "boolean"
        ? raw.canSubmit
        : missingRequiredFields.length === 0;

  return {
    supplierDisplayName: stringValue(
      raw.supplier_display_name ?? raw.trade_name,
      "seu fornecedor",
    ),
    profileVersion: numberValue(raw.profile_version ?? raw.version, 1),
    status: stringValue(raw.status, "AWAITING_SUPPLIER_REVIEW"),
    fields,
    missingRequiredFields,
    canSubmit,
  };
}

function defaultIdempotencyKey(): string {
  if (typeof crypto !== "undefined" && "randomUUID" in crypto) {
    return crypto.randomUUID();
  }
  return `review-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

export function createFetchSupplierReviewApi(
  options: FetchReviewApiOptions = {},
): SupplierReviewApi {
  const fetcher = options.fetcher ?? globalThis.fetch.bind(globalThis);
  const baseUrl = (options.baseUrl ?? "").replace(/\/$/, "");
  const makeIdempotencyKey = options.idempotencyKey ?? defaultIdempotencyKey;

  async function request(
    path: string,
    init: RequestInit = {},
  ): Promise<unknown> {
    let response: Response;
    try {
      response = await fetcher(`${baseUrl}${path}`, {
        cache: "no-store",
        credentials: "same-origin",
        ...init,
        headers: {
          Accept: "application/json",
          ...init.headers,
        },
      });
    } catch {
      throw new ReviewApiError(
        "NETWORK_ERROR",
        "Não foi possível conectar ao serviço de revisão.",
        0,
      );
    }

    const payload: unknown = await response.json().catch(() => ({}));
    if (!response.ok) {
      const error = record(record(payload).error);
      throw new ReviewApiError(
        stringValue(error.code, "UNEXPECTED_RESPONSE"),
        stringValue(error.message, "Não foi possível concluir esta ação."),
        response.status,
        record(error.details),
      );
    }
    return payload;
  }

  function mutationHeaders(): HeadersInit {
    return {
      "Content-Type": "application/json",
      "Idempotency-Key": makeIdempotencyKey(),
    };
  }

  function normalizeMutation(
    payload: unknown,
    fieldName: string,
    fallbackDecision: ReviewDecision,
    fallbackVersion: number,
  ): ReviewMutationResult {
    const raw = record(payload);
    const decision = stringValue(raw.decision, fallbackDecision) as ReviewDecision;
    return {
      fieldName: stringValue(raw.field_name, fieldName),
      decision,
      version: numberValue(raw.version, fallbackVersion),
    };
  }

  return {
    async getReview(token, signal) {
      const payload = await request(
        `/api/v1/supplier-review/${encodeURIComponent(token)}`,
        { method: "GET", ...(signal ? { signal } : {}) },
      );
      return normalizeReviewContext(payload);
    },

    async confirmField(token, fieldName, expectedVersion) {
      const payload = await request(
        `/api/v1/supplier-review/${encodeURIComponent(token)}/fields/${encodeURIComponent(fieldName)}/confirm`,
        {
          method: "POST",
          headers: mutationHeaders(),
          body: JSON.stringify({ expected_version: expectedVersion }),
        },
      );
      return normalizeMutation(
        payload,
        fieldName,
        "confirmed",
        expectedVersion + 1,
      );
    },

    async correctField(token, fieldName, correction) {
      const payload = await request(
        `/api/v1/supplier-review/${encodeURIComponent(token)}/fields/${encodeURIComponent(fieldName)}/correct`,
        {
          method: "POST",
          headers: mutationHeaders(),
          body: JSON.stringify({
            expected_version: correction.expectedVersion,
            value: correction.value,
            normalized_value: correction.normalizedValue,
          }),
        },
      );
      return normalizeMutation(
        payload,
        fieldName,
        "corrected",
        correction.expectedVersion + 1,
      );
    },

    async markNotApplicable(token, fieldName, expectedVersion) {
      const payload = await request(
        `/api/v1/supplier-review/${encodeURIComponent(token)}/fields/${encodeURIComponent(fieldName)}/not-applicable`,
        {
          method: "POST",
          headers: mutationHeaders(),
          body: JSON.stringify({ expected_version: expectedVersion }),
        },
      );
      return normalizeMutation(
        payload,
        fieldName,
        "not_applicable",
        expectedVersion + 1,
      );
    },

    async submitReview(token) {
      const payload = record(
        await request(`/api/v1/supplier-review/${encodeURIComponent(token)}/submit`, {
          method: "POST",
          headers: { "Idempotency-Key": makeIdempotencyKey() },
        }),
      );
      return {
        status: stringValue(payload.status, "SUBMITTED"),
        reviewSubmissionId:
          typeof payload.review_submission_id === "string"
            ? payload.review_submission_id
            : null,
      };
    },
  };
}
