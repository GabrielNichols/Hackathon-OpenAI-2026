import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import {
  SupplierOnboardingPage,
  createSupplierOnboardingRoute,
  type CreateSupplierInput,
  type MaterialRecord,
  type SupplierOnboardingApi,
} from "../index";

class FakeOnboardingApi implements SupplierOnboardingApi {
  readonly calls: string[] = [];
  record: MaterialRecord = {
    documentId: "doc_1",
    originalFilename: "cardapio.pdf",
    mediaType: "application/pdf",
    status: "EXTRACTION_QUEUED",
  };

  async createSupplier(input: CreateSupplierInput): Promise<{ supplierId: string }> {
    this.calls.push(`create:${input.tradeName}`);
    return { supplierId: "sup_1" };
  }

  async uploadFile(_supplierId: string, file: File): Promise<MaterialRecord> {
    this.calls.push(`file:${file.name}`);
    return structuredClone(this.record);
  }

  async uploadText(_supplierId: string, text: string): Promise<MaterialRecord> {
    this.calls.push(`text:${text}`);
    return {
      ...this.record,
      originalFilename: "whatsapp.txt",
      mediaType: "text/plain",
    };
  }

  async getMaterial(): Promise<MaterialRecord> {
    return structuredClone(this.record);
  }

  async retryExtraction(): Promise<MaterialRecord> {
    this.calls.push("retry");
    this.record = { ...this.record, status: "EXTRACTION_QUEUED" };
    return structuredClone(this.record);
  }
}

async function registerSupplier(user: ReturnType<typeof userEvent.setup>): Promise<void> {
  await user.type(screen.getByLabelText("Razão social"), "Sabor da Vila Ltda");
  await user.type(screen.getByLabelText("Nome comercial"), "Sabor da Vila");
  await user.type(screen.getByLabelText("Nome do contato"), "Ana");
  await user.type(screen.getByLabelText("E-mail"), "ana@example.com");
  await user.type(screen.getByLabelText("Telefone"), "+55 11 99999-0000");
  await user.click(screen.getByRole("button", { name: "Continuar" }));
}

describe("SupplierOnboardingPage", () => {
  it("creates the minimum supplier and uploads a supported file", async () => {
    const api = new FakeOnboardingApi();
    const user = userEvent.setup();
    render(<SupplierOnboardingPage api={api} />);
    await registerSupplier(user);

    const file = new File(["%PDF fixture"], "cardapio.pdf", {
      type: "application/pdf",
    });
    await user.upload(screen.getByLabelText("Escolher material"), file);
    await user.click(screen.getByRole("button", { name: "Enviar material" }));

    expect(await screen.findByText("Extração na fila")).toBeVisible();
    expect(screen.getByText("cardapio.pdf")).toBeVisible();
    expect(api.calls).toEqual(["create:Sabor da Vila", "file:cardapio.pdf"]);
  });

  it("accepts pasted WhatsApp text without requiring a file", async () => {
    const api = new FakeOnboardingApi();
    const user = userEvent.setup();
    render(<SupplierOnboardingPage api={api} initialSupplierId="sup_existing" />);

    await user.click(screen.getByRole("button", { name: "Colar texto" }));
    await user.type(
      screen.getByLabelText("Texto do material"),
      "Coffee break para empresas a partir de 30 pessoas",
    );
    await user.click(screen.getByRole("button", { name: "Enviar material" }));

    expect(await screen.findByText("whatsapp.txt")).toBeVisible();
    expect(api.calls[0]).toMatch(/^text:Coffee break/);
  });

  it("shows an honest extraction failure while keeping the original available", async () => {
    const api = new FakeOnboardingApi();
    api.record = {
      ...api.record,
      status: "EXTRACTION_FAILED",
      failureMessage: "Não foi possível interpretar este layout.",
    };
    const user = userEvent.setup();
    render(<SupplierOnboardingPage api={api} initialSupplierId="sup_existing" />);
    const file = new File(["%PDF fixture"], "layout-incomum.pdf", {
      type: "application/pdf",
    });

    await user.upload(screen.getByLabelText("Escolher material"), file);
    await user.click(screen.getByRole("button", { name: "Enviar material" }));

    const failure = await screen.findByRole("alert");
    expect(within(failure).getByText("Extração não concluída")).toBeVisible();
    expect(within(failure).getByText(/original continua armazenado/i)).toBeVisible();
    await user.click(
      within(failure).getByRole("button", {
        name: "Tentar extração novamente",
      }),
    );
    expect(await screen.findByText("Extração na fila")).toBeVisible();
    expect(api.calls).toContain("retry");
  });

  it("exports its own route definition", () => {
    const route = createSupplierOnboardingRoute(new FakeOnboardingApi());
    expect(route.path).toBe("/suppliers/onboarding");
    expect(route.Component).toBeTypeOf("function");
  });
});
