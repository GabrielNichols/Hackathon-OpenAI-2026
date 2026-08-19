import type {
  MaterialRecord,
  MaterialStatus,
  SupplierOnboardingApi,
} from "./model";

type FetchLike = (input: RequestInfo | URL, init?: RequestInit) => Promise<Response>;

export class SupplierOnboardingApiError extends Error {
  readonly code: string;
  readonly status: number;

  constructor(code: string, message: string, status: number) {
    super(message);
    this.name = "SupplierOnboardingApiError";
    this.code = code;
    this.status = status;
  }
}

interface FetchOnboardingApiOptions {
  baseUrl?: string;
  fetcher?: FetchLike;
}

function record(value: unknown): Record<string, unknown> {
  return typeof value === "object" && value !== null
    ? (value as Record<string, unknown>)
    : {};
}

function stringValue(value: unknown, fallback = ""): string {
  return typeof value === "string" ? value : fallback;
}

const MATERIAL_STATUSES = new Set<MaterialStatus>([
  "RECEIVED",
  "VALIDATED",
  "STORED",
  "EXTRACTION_QUEUED",
  "EXTRACTING",
  "EXTRACTED",
  "EXTRACTION_FAILED",
  "AWAITING_SUPPLIER_REVIEW",
]);

function normalizeMaterial(payload: unknown): MaterialRecord {
  const raw = record(payload);
  const rawStatus = stringValue(raw.status, "RECEIVED");
  const status = MATERIAL_STATUSES.has(rawStatus as MaterialStatus)
    ? (rawStatus as MaterialStatus)
    : "RECEIVED";
  return {
    documentId: stringValue(raw.document_id ?? raw.documentId),
    originalFilename: stringValue(
      raw.original_filename ?? raw.sanitized_filename ?? raw.originalFilename,
      "material",
    ),
    mediaType: stringValue(raw.media_type ?? raw.mediaType, "application/octet-stream"),
    status,
    ...(typeof raw.failure_message === "string"
      ? { failureMessage: raw.failure_message }
      : typeof raw.failureMessage === "string"
        ? { failureMessage: raw.failureMessage }
        : {}),
  };
}

export function createFetchSupplierOnboardingApi(
  options: FetchOnboardingApiOptions = {},
): SupplierOnboardingApi {
  const fetcher = options.fetcher ?? globalThis.fetch.bind(globalThis);
  const baseUrl = (options.baseUrl ?? "").replace(/\/$/, "");

  async function request(path: string, init: RequestInit): Promise<unknown> {
    let response: Response;
    try {
      response = await fetcher(`${baseUrl}${path}`, {
        credentials: "same-origin",
        ...init,
        headers: { Accept: "application/json", ...init.headers },
      });
    } catch {
      throw new SupplierOnboardingApiError(
        "NETWORK_ERROR",
        "Não foi possível conectar ao serviço de fornecedores.",
        0,
      );
    }
    const payload: unknown = await response.json().catch(() => ({}));
    if (!response.ok) {
      const error = record(record(payload).error);
      throw new SupplierOnboardingApiError(
        stringValue(error.code, "UNEXPECTED_RESPONSE"),
        stringValue(error.message, "Não foi possível concluir esta ação."),
        response.status,
      );
    }
    return payload;
  }

  return {
    async createSupplier(input) {
      const payload = record(
        await request("/api/v1/suppliers", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            legal_name: input.legalName,
            trade_name: input.tradeName,
            contact_name: input.contactName,
            contact_email: input.contactEmail,
            contact_phone: input.contactPhone,
          }),
        }),
      );
      return { supplierId: stringValue(payload.supplier_id ?? payload.supplierId) };
    },

    async uploadFile(supplierId, file) {
      const body = new FormData();
      body.append("file", file, file.name);
      return normalizeMaterial(
        await request(`/api/v1/suppliers/${encodeURIComponent(supplierId)}/materials`, {
          method: "POST",
          body,
        }),
      );
    },

    async uploadText(supplierId, text) {
      return normalizeMaterial(
        await request(`/api/v1/suppliers/${encodeURIComponent(supplierId)}/materials`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ text, filename: "whatsapp.txt" }),
        }),
      );
    },

    async getMaterial(supplierId, documentId, signal) {
      const payload = record(
        await request(`/api/v1/suppliers/${encodeURIComponent(supplierId)}`, {
          method: "GET",
          ...(signal ? { signal } : {}),
          cache: "no-store",
        }),
      );
      const materials = Array.isArray(payload.materials) ? payload.materials : [];
      const material = materials.find((candidate) => {
        const item = record(candidate);
        return stringValue(item.document_id ?? item.documentId) === documentId;
      });
      if (!material) {
        throw new SupplierOnboardingApiError(
          "MATERIAL_NOT_FOUND",
          "O status deste material ainda não está disponível.",
          404,
        );
      }
      return normalizeMaterial(material);
    },

    async retryExtraction(supplierId, documentId) {
      return normalizeMaterial(
        await request(`/api/v1/suppliers/${encodeURIComponent(supplierId)}/extractions`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ document_id: documentId }),
        }),
      );
    },
  };
}
