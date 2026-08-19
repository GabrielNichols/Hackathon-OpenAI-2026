# Supplier Intelligence bounded context

This module implements the supplier-owned half of Canal Agente without central router wiring or
direct writes to the core state machine.

## Public feature surfaces

- `api/ingestion_router.py`: factory for material upload and original-document retrieval.
- `api/profile_router.py`: tenant-scoped supplier create/get routes.
- `api/review_router.py`: signed profile review, confirm, correct, not-applicable, and submit routes.
- `search/`: the `SupplierDirectoryPort`-compatible tenant-scoped directory adapter.
- `extraction/`: strict DTOs, deterministic normalization, spreadsheet parsing, fake provider, and
  vendor-neutral structured LLM adapter.

The central application must import and mount these routers. This feature intentionally does not
edit a shared application router.

## Extraction safety

`LLMSupplierExtractionProvider` accepts an injected `StructuredExtractionClient`. The client must
return `StructuredSupplierExtractionDTO`; free text is never persisted as a field. Provider output
cannot use `confirmed` or `corrected`. Critical values without evidence become `not_found`, and low
confidence values become `needs_review`.

`parse_supplier_spreadsheet` reads adjacent label/value cells from XLSX files, applies only explicit
aliases, and records `source_sheet` plus `source_cell_range`. Contradictory cells are preserved as a
`needs_review` decision instead of being guessed.

## Synthetic evaluation command

From `backend/`:

```bash
python -m app.modules.suppliers.extraction.evaluation
```

An alternate dataset and output file can be supplied with `--dataset` and `--output`. The bundled
dataset is marked `synthetic_test_only` and `authorized_for_real_demo: false`. Its metrics validate
the evaluator and safety regressions; they are not product accuracy claims.

## Seed warning

`seeds/fixtures/*.synthetic.json` contains exactly three automated-test fixtures. Every record uses
synthetic IDs, `example.test` contacts, and an explicit `NOT AUTHORIZED FOR A REAL DEMO` disclaimer.
Do not show these fixtures as real suppliers. A real demo requires authorized supplier materials,
real review submissions, and separately retained evaluation evidence.

