# Supplier Intelligence handoff

## Integration boundaries

The shared core contract was not present when this feature was built. `application/core_compat.py`
therefore contains replaceable Protocols and fakes for clock, audit, signed review links, and the
supplier activation command. Rebase onto the core `contracts-v0` commit and replace these adapters
without changing supplier behavior.

Activation is fail-closed: review completeness is checked first, the final token nonce is consumed
only by submit, the command port is invoked, and the ACTIVE projection changes only after the port
returns an ACTIVE result. No ORM status assignment is used.

`SupplierDirectoryPort.get` has no tenant argument in the proposed shared contract. The supplied
adapter is therefore constructed with a tenant scope; never share one instance across tenants.

## Router wiring

Mount the factories/routers exported by:

- `app.modules.suppliers.api.ingestion_router.create_ingestion_router`
- `app.modules.suppliers.api.profile_router.create_supplier_profile_router`
- `app.modules.suppliers.api.review_router.router`

The review router expects `app.state.supplier_review_service`. Profile and ingestion routers receive
their services and tenant resolvers through factories.

## Verification

From `backend/`, run:

```bash
pytest tests/unit/suppliers tests/contract/suppliers tests/integration/suppliers
ruff check app/modules/suppliers tests
mypy app/modules/suppliers
python -m app.modules.suppliers.extraction.evaluation
```

## Remaining real-environment work

- Bind `StructuredExtractionClient` to the selected model SDK and authorized blob access.
- Run the extraction dataset on authorized real documents before publishing any accuracy number.
- Replace the local activation/audit/token compatibility layer with the frozen core contracts.
- Supply three authorized real suppliers and materials for the demo; bundled synthetic seeds are
  prohibited as demo evidence.
- Let the integration owner mount routers and connect the frontend; this branch does neither.
