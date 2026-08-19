import { useEffect, useState, type FormEvent } from "react";

import {
  MATERIAL_STATUS_LABELS,
  isTerminalMaterialStatus,
  type CreateSupplierInput,
  type MaterialRecord,
  type SupplierOnboardingApi,
} from "./model";
import "./supplier-onboarding.css";

interface SupplierOnboardingPageProps {
  api: SupplierOnboardingApi;
  initialSupplierId?: string;
}

const EMPTY_SUPPLIER: CreateSupplierInput = {
  legalName: "",
  tradeName: "",
  contactName: "",
  contactEmail: "",
  contactPhone: "",
};

function statusTone(status: MaterialRecord["status"]): string {
  if (status === "EXTRACTION_FAILED") return "error";
  if (["EXTRACTED", "AWAITING_SUPPLIER_REVIEW"].includes(status)) return "success";
  return "progress";
}

export function SupplierOnboardingPage({ api, initialSupplierId }: SupplierOnboardingPageProps) {
  const [supplierId, setSupplierId] = useState(initialSupplierId ?? "");
  const [supplier, setSupplier] = useState<CreateSupplierInput>(EMPTY_SUPPLIER);
  const [mode, setMode] = useState<"file" | "text">("file");
  const [file, setFile] = useState<File | null>(null);
  const [text, setText] = useState("");
  const [material, setMaterial] = useState<MaterialRecord | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!material || !supplierId || isTerminalMaterialStatus(material.status)) return;
    const controller = new AbortController();
    const timer = window.setTimeout(() => {
      void api
        .getMaterial(supplierId, material.documentId, controller.signal)
        .then(setMaterial)
        .catch(() => undefined);
    }, 2500);
    return () => {
      controller.abort();
      window.clearTimeout(timer);
    };
  }, [api, material, supplierId]);

  async function createSupplier(event: FormEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const created = await api.createSupplier(supplier);
      setSupplierId(created.supplierId);
    } catch {
      setError("Não foi possível criar o fornecedor. Revise os dados e tente novamente.");
    } finally {
      setBusy(false);
    }
  }

  async function upload(event: FormEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault();
    if (!supplierId) return;
    if (mode === "file" && (!file || file.size === 0)) {
      setError("Escolha um arquivo não vazio nos formatos PDF, PNG, JPEG ou XLSX.");
      return;
    }
    if (mode === "text" && !text.trim()) {
      setError("Cole o texto do material antes de enviar.");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const uploaded =
        mode === "file" && file
          ? await api.uploadFile(supplierId, file)
          : await api.uploadText(supplierId, text.trim());
      setMaterial(uploaded);
      setFile(null);
      setText("");
    } catch {
      setError("O material não pôde ser enviado. O sistema não registrou uma confirmação falsa.");
    } finally {
      setBusy(false);
    }
  }

  if (!supplierId) {
    return (
      <main className="onboarding-shell">
        <header className="onboarding-hero">
          <p className="onboarding-eyebrow">Cadastro de fornecedor</p>
          <h1>Transforme seus materiais em um perfil comercial</h1>
          <p>Comece com os dados mínimos. Você poderá enviar PDF, imagem, planilha ou texto.</p>
        </header>
        <form className="supplier-form" onSubmit={(event) => void createSupplier(event)}>
          <h2>Dados do fornecedor</h2>
          <div className="form-grid">
            {(
              [
                ["legalName", "Razão social", "organization"],
                ["tradeName", "Nome comercial", "organization"],
                ["contactName", "Nome do contato", "name"],
                ["contactEmail", "E-mail", "email"],
                ["contactPhone", "Telefone", "tel"],
              ] as const
            ).map(([name, label, autoComplete]) => (
              <label key={name}>
                {label}
                <input
                  autoComplete={autoComplete}
                  onChange={(event) =>
                    setSupplier((current) => ({ ...current, [name]: event.target.value }))
                  }
                  required
                  type={name === "contactEmail" ? "email" : name === "contactPhone" ? "tel" : "text"}
                  value={supplier[name]}
                />
              </label>
            ))}
          </div>
          {error ? <p className="onboarding-error" role="alert">{error}</p> : null}
          <button className="onboarding-button primary" disabled={busy} type="submit">
            {busy ? "Criando…" : "Continuar"}
          </button>
        </form>
      </main>
    );
  }

  return (
    <main className="onboarding-shell">
      <header className="onboarding-hero compact">
        <p className="onboarding-eyebrow">Material comercial</p>
        <h1>Envie o que você já usa</h1>
        <p>O original será preservado mesmo se a extração não conseguir interpretar o conteúdo.</p>
      </header>

      <section className="material-card">
        <div className="mode-switch" aria-label="Tipo de material" role="group">
          <button aria-pressed={mode === "file"} onClick={() => setMode("file")} type="button">Enviar arquivo</button>
          <button aria-pressed={mode === "text"} onClick={() => setMode("text")} type="button">Colar texto</button>
        </div>
        <form onSubmit={(event) => void upload(event)}>
          {mode === "file" ? (
            <label className="file-picker">
              <span>Escolher material</span>
              <input
                aria-label="Escolher material"
                accept=".pdf,.png,.jpg,.jpeg,.xlsx,application/pdf,image/png,image/jpeg,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                onChange={(event) => setFile(event.target.files?.[0] ?? null)}
                type="file"
              />
              <small>{file ? file.name : "PDF, PNG, JPEG ou XLSX"}</small>
            </label>
          ) : (
            <label className="text-material">
              Texto do material
              <textarea
                onChange={(event) => setText(event.target.value)}
                placeholder="Cole aqui a mensagem ou descrição recebida por WhatsApp"
                rows={7}
                value={text}
              />
            </label>
          )}
          {error ? <p className="onboarding-error" role="alert">{error}</p> : null}
          <button className="onboarding-button primary" disabled={busy} type="submit">
            {busy ? "Enviando…" : "Enviar material"}
          </button>
        </form>
      </section>

      {material ? (
        material.status === "EXTRACTION_FAILED" ? (
          <section className="material-status failure" role="alert">
            <p className="status-kicker">{material.originalFilename}</p>
            <h2>Extração não concluída</h2>
            <p>{material.failureMessage ?? "Não foi possível interpretar este material com segurança."}</p>
            <p><strong>O documento original continua armazenado.</strong> Nenhum campo foi inventado.</p>
            <button
              className="onboarding-button secondary"
              disabled={busy}
              onClick={() => {
                setBusy(true);
                setError(null);
                void api
                  .retryExtraction(supplierId, material.documentId)
                  .then(setMaterial)
                  .catch(() => setError("Não foi possível colocar a extração novamente na fila."))
                  .finally(() => setBusy(false));
              }}
              type="button"
            >
              {busy ? "Enfileirando…" : "Tentar extração novamente"}
            </button>
          </section>
        ) : (
          <section aria-live="polite" className="material-status" data-tone={statusTone(material.status)}>
            <div className="status-mark" aria-hidden="true" />
            <div>
              <p className="status-kicker">{material.originalFilename}</p>
              <h2>{MATERIAL_STATUS_LABELS[material.status]}</h2>
              <p>
                {material.status === "AWAITING_SUPPLIER_REVIEW"
                  ? "A extração está pronta para ser conferida pelo fornecedor."
                  : "O status mostrado vem do serviço de processamento; não usamos progresso simulado."}
              </p>
            </div>
          </section>
        )
      ) : null}
    </main>
  );
}
