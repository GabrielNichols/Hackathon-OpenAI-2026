export type MaterialStatus =
  | "RECEIVED"
  | "VALIDATED"
  | "STORED"
  | "EXTRACTION_QUEUED"
  | "EXTRACTING"
  | "EXTRACTED"
  | "EXTRACTION_FAILED"
  | "AWAITING_SUPPLIER_REVIEW";

export interface CreateSupplierInput {
  legalName: string;
  tradeName: string;
  contactName: string;
  contactEmail: string;
  contactPhone: string;
}

export interface MaterialRecord {
  documentId: string;
  originalFilename: string;
  mediaType: string;
  status: MaterialStatus;
  failureMessage?: string;
}

export interface SupplierOnboardingApi {
  createSupplier(input: CreateSupplierInput): Promise<{ supplierId: string }>;
  uploadFile(supplierId: string, file: File): Promise<MaterialRecord>;
  uploadText(supplierId: string, text: string): Promise<MaterialRecord>;
  getMaterial(
    supplierId: string,
    documentId: string,
    signal?: AbortSignal,
  ): Promise<MaterialRecord>;
  retryExtraction(supplierId: string, documentId: string): Promise<MaterialRecord>;
}

export const MATERIAL_STATUS_LABELS: Readonly<Record<MaterialStatus, string>> = {
  RECEIVED: "Material recebido",
  VALIDATED: "Material validado",
  STORED: "Original armazenado",
  EXTRACTION_QUEUED: "Extração na fila",
  EXTRACTING: "Extração em andamento",
  EXTRACTED: "Extração concluída",
  EXTRACTION_FAILED: "Extração não concluída",
  AWAITING_SUPPLIER_REVIEW: "Aguardando revisão do fornecedor",
};

export function isTerminalMaterialStatus(status: MaterialStatus): boolean {
  return ["EXTRACTED", "EXTRACTION_FAILED", "AWAITING_SUPPLIER_REVIEW"].includes(status);
}
