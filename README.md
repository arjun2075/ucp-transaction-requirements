# UCP transaction requirements — extension integration v0.3

Prepared 24 October 2026 (Pacific). Local, unpublished reference implementation.

## Result

Two experimental extensions can compose with the existing draft Checkout and Order response schemas, with no edits to upstream core schemas. They remain separate concerns:

- `com.example.screening`: Checkout verification Actions and transaction-bound screening evidence.
- `com.example.fiscal_documents`: optional Checkout preconditions plus a proposed post-order fiscal-document ledger. The ledger does not reuse screening evidence or claim that a document is legally valid.

**36 tests pass.** Responses and capability declarations are validated using JSON Schema Draft 2020-12 against an included, unchanged UCP schema snapshot. Thirteen fiscal-ledger tests exercise durable SQLite transactions, optimistic revisions, concurrent writers, replay conflict detection, lost-response retry, content tampering, authenticated mutation and retrieval, immutable party/order binding, and issue/cancel/replace history. The earlier 23 integration tests continue to cover the combined extension example.

This establishes feasibility in a local implementation. It does not establish an official extension, a protocol defect, legal compliance, maintainer approval, live provider integration or production UCP conformance.

## Reproduce

Use Python 3.12 or compatible Python:

```sh
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python -m unittest -v test_integration test_fiscal_ledger
.venv/bin/python generate_examples.py
```

`validation-v0.3.txt` records the current checks; `validation.txt` preserves the v0.2 baseline run. `examples/` contains validated Checkout/Order responses, illustrative signed provider results, the corresponding public key, and clearly marked non-CFDI XML fixtures. No private signing key is distributed. Regenerating examples creates a fresh fixture key.

## Source baseline and verification boundaries

Upstream UCP revision: `ba9795a6a1c55dc98ec50032cc124a77fba16750` (current `main` inspected on 24 October 2026).

- [Extension-declared Action contract](https://github.com/Universal-Commerce-Protocol/ucp/blob/ba9795a6a1c55dc98ec50032cc124a77fba16750/docs/documentation/schema-authoring.md#extension-declared-action-types)
- [Common Actions and Signals](https://github.com/Universal-Commerce-Protocol/ucp/blob/ba9795a6a1c55dc98ec50032cc124a77fba16750/docs/specification/overview/index.md)
- [Checkout eligibility and Action lifecycle](https://github.com/Universal-Commerce-Protocol/ucp/blob/ba9795a6a1c55dc98ec50032cc124a77fba16750/docs/specification/shopping/checkout/index.md#eligibility-verification-at-completion)
- [Negotiation](https://github.com/Universal-Commerce-Protocol/ucp/blob/ba9795a6a1c55dc98ec50032cc124a77fba16750/docs/documentation/core-concepts.md)
- [Order full-snapshot and authentication contract](https://github.com/Universal-Commerce-Protocol/ucp/blob/ba9795a6a1c55dc98ec50032cc124a77fba16750/docs/specification/shopping/order/index.md#operations)

The fixture date `2026-10-23` makes metadata conform to the schema's date grammar; it does not claim a certified UCP release exists with that date. Schema `$id` and `spec` URLs on `example.com` are illustrative and resolve locally in the test registry, not hosted public extensions.

The official Rust `ucp-schema` executable is unavailable in this environment. Validation uses the unchanged response schemas and ordinary JSON Schema `allOf`/`$ref` composition. UCP direction annotations are not interpreted; no create/update/complete request-shape or official resolver validation is claimed. Schema validation cannot prove behavioral invariants; the service tests check the explicitly listed invariants.

## Extension contract implemented by the adapters

### Screening and an optional fiscal precondition before completion

The business selects exact-version matches and prunes extensions without an active parent. This fixture uses one candidate version per capability and does not implement full profile discovery or protocol-version selection.

Local policy requires both workflows. If either cannot be negotiated, the business keeps completion blocked and returns a recoverable outcome; missing support never waives that local requirement. This is implementation policy, not a fictional UCP `required_extensions` field.

The business emits an Action with a fresh task ID and `config` identifying the checkout, policy, provider and a SHA-256 digest of the covered facts. The covered facts are buyer, seller, payer, destination, goods, currency and total. Any supported fact change conservatively invalidates accepted results and creates fresh tasks. Production scope rules must be domain-specific; blanket invalidation here is a fixture choice.

The platform-side adapter calls the configured verifier. The verifier signs a canonical JSON payload, and the platform relays it to the business's extension-specific result adapter. The business checks signature, trusted issuer, matching task/checkout/policy/scope, expiry and a satisfied outcome. Pending or denied results cannot complete the workflow. Provider IDs map to preconfigured keys; no untrusted URL or JWKS key is automatically accepted.

For the fixture's optional fiscal precondition, the signed result also identifies a document and its content hash. Initial delivery must match that accepted result. This is an integrity binding, not authority certification. The signature encoding is an extension-owned canonical-JSON/Ed25519 fixture format, not JWS, AP2 or an SAT digital seal.

Only the business's subsequent Checkout response establishes its current state. A signed provider result alone does not make Checkout ready. A rejected Complete Checkout remains incomplete with a recoverable error selecting the exact outstanding Action. Processing the Action does not repeat completion; the platform submits a later operation.

The pre-completion adapter is in-process. It is not an HTTP server or full REST binding. The business/provider identities and applicable policies are fixtures, not an automated determination of legal obligations.

### Post-order fiscal-document lifecycle

Order updates already carry full entities. The proposed fiscal extension contributes a namespaced `com.example.fiscal_documents` object with document IDs, state, type, jurisdiction label, media type, content hash, retrieval URL, lifecycle events and an extension-specific revision. `fiscal_ledger.py` persists these records in SQLite and returns a schema-valid full Order snapshot after each committed event.

The sequence is:

1. An issued fixture document is delivered through a full Order snapshot.
2. Accepted document cancellation updates its status in a later snapshot.
3. A replacement document has a new ID and references the canceled original.

Document cancellation does not cancel the purchase or mutate fulfillment status. Older document records and lifecycle events remain visible. The fixture requires accepted cancellation before replacement; that is a chosen reference rule, not a universal rule of Mexican or other law. The state name is deliberately `issued`, not `certified`, because the fixture does not integrate with a tax authority.

The durable adapter serializes writers with `BEGIN IMMEDIATE`, requires the caller's expected revision, and commits document state, lifecycle event and replay response in one transaction. An exact event retry returns the original response; the same event ID with changed input is rejected. Content bytes must match the declared digest. Retrieval requires an independently supplied authenticated Platform identity and rechecks stored content integrity. These tests exercise adapter behavior, not an HTTP authentication mechanism: production REST or webhook code must authenticate the Business/Platform using a supported UCP mechanism before passing an identity to this adapter.

The earlier receiving adapter still demonstrates full-snapshot handling: it tolerates exact duplicates, ignores stale fiscal revisions and rejects conflicting snapshots at the same revision. Those are proposed extension semantics, not core Order revision fields. See [docs/fiscal-lifecycle.md](docs/fiscal-lifecycle.md) for the state machine, invariants, failure behavior and remaining security boundaries.

Order does **not** adopt common Actions in the inspected contract. Consequently this package does not add Order Actions or claim that Checkout Actions block later shipment. A shipment-control integration would need an explicitly defined capability/service contract and enforcement by the system controlling shipment.

## Tests and evidence

The tests cover upstream capability declaration shapes, exact-version mismatch, orphan and multi-parent behavior, schema-invalid configurations and totals, pending Actions, successful signed verification, tampering, untrusted issuer, wrong task, destination/payer change, expiry at completion, denial/pending results, authoritative state, document evidence binding, cancellation/replacement, duplicate/stale deliveries, conflicting revisions, wrong order owner, durable restart, atomic rollback, authenticated retrieval, event replay conflict, lost-response retry and a two-writer race.

`upstream-schema-sha256.json` records hashes of every included upstream schema; all bytes were compared against the checked-out baseline. No base schema is weakened or patched. Extension fields and states are proposed local contracts, not newly discovered official fields.

## Opportunity assessment

**Confirmed:** the draft extension machinery can represent the tested Checkout workflows, and ordinary schema composition can carry a document ledger in Order snapshots. Existing mechanisms avoid the need to invent a core compliance executor or a new core invoice transport for this example. On the evidence inspected, a Core enhancement proposal would be premature; the smallest justified contribution is this vendor-namespaced reference implementation and documentation.

**Not established:** cross-provider agreement on evidence semantics, legal applicability, complete invoice lifecycle coverage across jurisdictions, independent enforcement at payment/fulfillment services, or a need for a new core primitive.

The useful standards question is narrower: should UCP provide a reference integration pattern for required workflow negotiation and transaction-bound evidence, and is there existing work defining portable fiscal-document lifecycle semantics? Our main-repository searches found adjacent payer-identity/payment-terms work, not a dedicated CFDI/screening lifecycle contract; that bounded search does not rule out third-party extensions.

## Maintainer-review note — draft, unsent

I tested a small local example composing existing Checkout Actions with two experimental extensions for screening and an optional fiscal precondition. The responses validate against the pinned draft UCP schemas; signed fixture results bind to transaction facts and expire, and a locally required workflow remains blocked if its extension cannot be negotiated.

For post-order documents, a namespaced Order extension carries delivery, cancellation and replacement records through existing full Order snapshots. I did not add Order Actions, new core fields or a new core endpoint.

Would a reference integration note using this scope be useful? Is there existing fiscal-document work we should align with, particularly around document identifiers, lifecycle updates and mandatory-workflow fallback? I am not claiming a protocol bypass or proposing a general legal-rule engine.

No public comment or PR has been posted. This investigation relates to #926; #925's FX-guarantee question remains separate.

## Reviewer alignment — immutable correction path

The original v0.3 cancellation fixture remains explicitly non-normative.
The new correction scenario issues a **separate** credit-note document
without canceling or mutating the original, with optional
`preceding_documents` and outbound `adjustment_id` metadata. The
Business-owned Order adjustment is not required to have a fiscal document;
more than one fiscal document can correlate to the same adjustment.
No reverse reference or new UCP Core fields are needed. See
[docs/fiscal-lifecycle.md](docs/fiscal-lifecycle.md).

The earlier 36-test validation log predates this addition. The new tests
are provided as reproducible source, not claimed as executed until verified.
