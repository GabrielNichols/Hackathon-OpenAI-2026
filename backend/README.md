# Core backend

This package publishes the deterministic domain, contract, policy, audit,
idempotency, outbox, signed-link, and PostgreSQL persistence primitives used by
the supplier, procurement-agent, and quote-portal branches.

## Local gates

From `backend/`, using Python 3.12:

```bash
python -m pip install -e '.[dev]'
python -m ruff format --check app tests migrations
python -m ruff check app tests migrations
python -m mypy app
python -m pytest -s tests/unit tests/contract -q
TESTCONTAINERS_RYUK_DISABLED=true python -m pytest -s -m postgres tests/integration/core -q
```

The integration suite starts PostgreSQL 16 through Testcontainers. CI can
instead set `CORE_TEST_DATABASE_URL` to a dedicated PostgreSQL database.

## Integration boundaries

- Import cross-module DTOs and ports only from `app.contracts`.
- Import reusable fakes from `app.platform.fakes`.
- Import deterministic factories and `assert_core_port` from `app.testing`.
- Persist aggregate snapshots, audit events, idempotency records, and outbox
  messages inside one `SqlAlchemyUnitOfWork` transaction.
- Authorize through `PolicyPort` before invoking aggregate operations; domain
  invariants remain the final guard against invalid facts.
- Never treat an outbox delivery failure as a completed business transition.
- Never reuse a signed-link nonce or an idempotency key with a different payload.

The frozen public surface is documented in `../shared/contracts/core-v0.md`.
