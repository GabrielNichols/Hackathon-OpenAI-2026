import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import {
  ReviewApiError,
  SupplierProfileReviewPage,
  createSupplierProfileReviewRoute,
  type CorrectionInput,
  type ReviewMutationResult,
  type SupplierReviewApi,
  type SupplierReviewContext,
  type SupplierReviewSubmitResult,
} from "../index";

function context(
  overrides: Partial<SupplierReviewContext> = {},
): SupplierReviewContext {
  return {
    supplierDisplayName: "Sabor da Vila",
    profileVersion: 1,
    status: "AWAITING_SUPPLIER_REVIEW",
    fields: [
      {
        fieldName: "trade_name",
        label: "Nome comercial",
        value: "Sabor da Vila",
        normalizedValue: "Sabor da Vila",
        status: "extracted",
        confidence: 0.94,
        required: true,
        allowNotApplicable: false,
        version: 1,
        evidence: {
          documentId: "doc_1",
          page: 2,
          sheet: null,
          cellRange: null,
          excerpt: "Buffet Sabor da Vila para eventos corporativos",
        },
      },
      {
        fieldName: "invoice_available",
        label: "Emite nota fiscal",
        value: true,
        normalizedValue: true,
        status: "needs_review",
        confidence: 0.61,
        required: true,
        allowNotApplicable: true,
        version: 1,
        evidence: {
          documentId: "doc_1",
          page: 3,
          sheet: null,
          cellRange: null,
          excerpt: "Nota fiscal sob consulta",
        },
      },
    ],
    missingRequiredFields: ["trade_name", "invoice_available"],
    canSubmit: false,
    ...overrides,
  };
}

class FakeReviewApi implements SupplierReviewApi {
  current = context();
  readonly calls: string[] = [];
  loadError: ReviewApiError | null = null;

  async getReview(): Promise<SupplierReviewContext> {
    if (this.loadError) throw this.loadError;
    return structuredClone(this.current);
  }

  async confirmField(
    _token: string,
    fieldName: string,
    expectedVersion: number,
  ): Promise<ReviewMutationResult> {
    this.calls.push(`confirm:${fieldName}:${expectedVersion}`);
    this.update(fieldName, "confirmed");
    return { fieldName, decision: "confirmed", version: expectedVersion + 1 };
  }

  async correctField(
    _token: string,
    fieldName: string,
    correction: CorrectionInput,
  ): Promise<ReviewMutationResult> {
    this.calls.push(`correct:${fieldName}:${String(correction.value)}`);
    this.update(fieldName, "corrected", correction.value);
    return {
      fieldName,
      decision: "corrected",
      version: correction.expectedVersion + 1,
    };
  }

  async markNotApplicable(
    _token: string,
    fieldName: string,
    expectedVersion: number,
  ): Promise<ReviewMutationResult> {
    this.calls.push(`not-applicable:${fieldName}:${expectedVersion}`);
    this.update(fieldName, "not_applicable", null);
    return {
      fieldName,
      decision: "not_applicable",
      version: expectedVersion + 1,
    };
  }

  async submitReview(): Promise<SupplierReviewSubmitResult> {
    this.calls.push("submit");
    return { status: "ACTIVE", reviewSubmissionId: "submission_1" };
  }

  private update(
    fieldName: string,
    status: "confirmed" | "corrected" | "not_applicable",
    value?: unknown,
  ): void {
    this.current.fields = this.current.fields.map((field) =>
      field.fieldName === fieldName
        ? {
            ...field,
            status,
            value: value === undefined ? field.value : value,
            normalizedValue:
              value === undefined ? field.normalizedValue : value,
            version: field.version + 1,
          }
        : field,
    );
    this.current.missingRequiredFields =
      this.current.missingRequiredFields.filter((name) => name !== fieldName);
    this.current.canSubmit = this.current.missingRequiredFields.length === 0;
  }
}

describe("SupplierProfileReviewPage", () => {
  it("renders extracted values, confidence, status text, and source evidence", async () => {
    render(<SupplierProfileReviewPage api={new FakeReviewApi()} token="secret" />);

    expect(
      await screen.findByRole("heading", { name: /revise seu perfil/i }),
    ).toBeVisible();
    const field = screen.getByRole("group", { name: "Nome comercial" });
    expect(within(field).getByText("Extraído")).toBeVisible();
    expect(within(field).getByText("Confiança da extração: 94%")).toBeVisible();
    expect(within(field).getByText(/Buffet Sabor da Vila/)).toBeVisible();
    expect(within(field).getByText(/Página 2/)).toBeVisible();
  });

  it("waits for the persisted response before showing a field as confirmed", async () => {
    const api = new FakeReviewApi();
    let release: (() => void) | undefined;
    const pending = new Promise<void>((resolve) => {
      release = resolve;
    });
    const original = api.confirmField.bind(api);
    vi.spyOn(api, "confirmField").mockImplementation(async (...args) => {
      await pending;
      return original(...args);
    });
    const user = userEvent.setup();
    render(<SupplierProfileReviewPage api={api} token="secret" />);

    const group = await screen.findByRole("group", { name: "Nome comercial" });
    await user.click(within(group).getByRole("button", { name: "Confirmar" }));

    expect(within(group).getByText("Extraído")).toBeVisible();
    expect(
      within(group).getByRole("button", { name: "Confirmando…" }),
    ).toBeDisabled();
    release?.();
    expect(await within(group).findByText("Confirmado")).toBeVisible();
    expect(api.calls).toContain("confirm:trade_name:1");
  });

  it("corrects a value and renders the versioned corrected state", async () => {
    const api = new FakeReviewApi();
    const user = userEvent.setup();
    render(<SupplierProfileReviewPage api={api} token="secret" />);
    const group = await screen.findByRole("group", { name: "Nome comercial" });

    await user.click(within(group).getByRole("button", { name: "Corrigir" }));
    const input = within(group).getByLabelText("Novo valor para Nome comercial");
    await user.clear(input);
    await user.type(input, "Sabor da Vila Catering");
    await user.click(
      within(group).getByRole("button", { name: "Salvar correção" }),
    );

    expect(await within(group).findByText("Corrigido")).toBeVisible();
    expect(within(group).getByText("Sabor da Vila Catering")).toBeVisible();
    expect(within(group).getByText(/Valor extraído originalmente/)).toBeVisible();
    expect(api.calls).toContain("correct:trade_name:Sabor da Vila Catering");
  });

  it("marks an allowed field as not applicable without treating it as not found", async () => {
    const api = new FakeReviewApi();
    const user = userEvent.setup();
    render(<SupplierProfileReviewPage api={api} token="secret" />);
    const group = await screen.findByRole("group", { name: "Emite nota fiscal" });

    await user.click(within(group).getByRole("button", { name: "Não se aplica" }));

    expect((await within(group).findAllByText("Não se aplica"))[0]).toBeVisible();
    expect(api.calls).toContain("not-applicable:invoice_available:1");
    expect(within(group).queryByText("Não encontrado")).not.toBeInTheDocument();
  });

  it("explains incomplete submission and points to every missing field", async () => {
    render(<SupplierProfileReviewPage api={new FakeReviewApi()} token="secret" />);

    const submit = await screen.findByRole("button", { name: "Enviar revisão" });
    expect(submit).toBeDisabled();
    const blocker = screen.getByRole("region", { name: "Pendências da revisão" });
    expect(within(blocker).getByText(/2 campos obrigatórios/)).toBeVisible();
    expect(
      within(blocker).getByRole("button", { name: "Nome comercial" }),
    ).toBeVisible();
  });

  it("shows a safe expired-link state without supplier data", async () => {
    const api = new FakeReviewApi();
    api.loadError = new ReviewApiError("LINK_EXPIRED", "expired", 410);
    render(<SupplierProfileReviewPage api={api} token="secret" />);

    expect(
      await screen.findByRole("heading", { name: "Este link expirou" }),
    ).toBeVisible();
    expect(screen.queryByText("Sabor da Vila")).not.toBeInTheDocument();
  });

  it("does not persist the review token in browser storage", async () => {
    const localSpy = vi.spyOn(Storage.prototype, "setItem");
    render(
      <SupplierProfileReviewPage api={new FakeReviewApi()} token="never-store-me" />,
    );
    await screen.findByRole("heading", { name: /revise seu perfil/i });
    expect(localSpy).not.toHaveBeenCalled();
  });

  it("exports a route factory without owning the central router", () => {
    const route = createSupplierProfileReviewRoute(new FakeReviewApi());
    expect(route.path).toBe("/supplier-review/:token");
    expect(route.Component).toBeTypeOf("function");
  });
});
