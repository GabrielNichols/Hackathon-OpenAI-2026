# Core contracts v0

Contract version: `0.1.0`

The canonical Python import surface is `app.contracts`. Feature modules must not
import ORM models through this package and must not copy these DTOs into their
own modules. Changes to a public name or serialized field require a contract
test and an explicit contract-version decision.

## Stable conventions

- Money is represented only as strict, non-negative integer cents.
- Mutable aggregates carry a strict, non-negative integer `version`.
- Internal datetimes are timezone-aware and normalized to UTC. JSON boundaries
  use ISO 8601.
- Public DTOs reject unknown fields.
- Event names and stable reason codes use `UPPER_SNAKE_CASE` strings. Event names
  intentionally remain extensible strings rather than a closed enum because
  feature modules publish their own events.
- Idempotency keys are required for commands that can create an external or
  duplicated business effect.
- DTOs contain no ORM, transport, LLM, or framework-specific objects.

## Canonical ID prefixes

| Entity | Prefix | Example |
|---|---|---|
| Supplier | `sup_` | `sup_alpha` |
| Procurement request | `pr_` | `pr_demo` |
| RFQ round | `rfq_` | `rfq_001` |
| Quote | `quo_` | `quo_alpha_v1` |
| Approval | `apr_` | `apr_001` |
| Award | `awd_` | `awd_001` |

Fixture names such as `supplier_alpha` and `quote_alpha_v1` are factory labels;
their entity IDs still use `sup_` and `quo_`.

## Cross-module ports

- `SupplierDirectoryPort` is implemented by supplier onboarding and consumed by
  sourcing.
- `RFQExecutionPort` is implemented by RFQ/messaging and consumed by the agent.
- `QuoteDecisionPort` is the frozen superset used by the agent: `compare`,
  `run_negotiation`, `request_approval`, `send_award`, and `get_award_status`.
- `PolicyPort` receives both `actor_tenant_id` and `resource_tenant_id`; tenant
  isolation must not be inferred from free-form arguments.
- `AuditPort` defines the event shape. Persistence must append audit events in
  the same unit-of-work transaction as aggregate changes; calling the port after
  committing an aggregate does not satisfy atomicity.

`run_negotiation_round` is a tool name. Its adapter calls the canonical
`QuoteDecisionPort.run_negotiation` method.

## Delivery semantics

`RFQDeliveryStatus` and `OutboxStatus` are separate enums even where values
overlap. A delivery is `DELIVERED` only with a gateway external ID and timestamp.
`DeliveryBatchDTO.all_confirmed` means every individual delivery is confirmed;
it does not define the procurement policy threshold for moving a request to
`RFQ_ACTIVE`.

An award remains `CREATED` until delivery is acknowledged. Delivery failures
belong to delivery/outbox state and are retryable; they are not a terminal award
status. `SENT`, `ACCEPTED`, and `DECLINED` therefore imply a persisted send
timestamp.

## Errors

Public failures use `ErrorEnvelopeDTO`:

```json
{
  "error": {
    "code": "INVALID_STATE_TRANSITION",
    "message": "Quote cannot move from REQUESTED to VALID",
    "details": {},
    "correlation_id": "cor_123"
  }
}
```

Domain and infrastructure exceptions may remain local classes, but adapters map
them to the stable `ErrorCode` values at the API boundary.
