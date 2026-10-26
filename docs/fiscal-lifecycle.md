# Fiscal-document lifecycle reference contract

This document specifies the behavior implemented by `fiscal_ledger.py` and the
vendor-namespaced `com.example.fiscal_documents` schema. It is experimental. It
does not select applicable law, validate a tax identifier, contact a tax
authority, certify a document, or establish legal compliance.

## Separation of concerns

Pre-transaction screening and post-order fiscal documents are distinct.
Screening can gate Checkout through a separately negotiated Action extension.
Fiscal-document delivery is state attached to the resulting Order. A screening
result is neither a fiscal document nor evidence that a document was accepted by
an authority. This package does not use one as a substitute for the other.

UCP Order does not adopt common Actions in the inspected contract. The reference
therefore adds no Order Action and no Core field. It composes a namespaced object
into full Order snapshots through the existing extension model.

## State and events

Each document has a stable ID and one of two local states:

- `issued`: the Business made the referenced bytes available.
- `canceled`: a later accepted event canceled the referenced document.

`issue` creates a new ID. `cancel` changes an issued document to canceled.
`replace` creates a new ID that names a canceled predecessor in `replaces`.
Records and events are append-preserving; replacement does not delete history.
None of these state names asserts tax-authority certification.

Each mutation carries a globally unique `event_id`, `order_id`, `checkout_id`,
`expected_revision`, event type, and UTC occurrence time. Issue and replacement
also carry document metadata and the exact content bytes. Cancellation carries
the target document ID and no content.

## Required invariants

The adapter enforces all of the following in one SQLite transaction:

1. The transport-authenticated Business equals the configured Order owner.
2. The event is bound to the configured Order and originating Checkout.
3. `expected_revision` equals the currently committed fiscal revision.
4. Document IDs are unique within the Order.
5. Supplied bytes match the declared SHA-256 content hash.
6. Cancellation targets an issued document.
7. Replacement targets an existing canceled document.
8. Document mutation, lifecycle event, revision increment and replay response
   either commit together or roll back together.

An exact retry of a committed `event_id` returns the original committed Order
snapshot. Reusing that ID with any changed event metadata or content is rejected.
This makes a lost response recoverable without repeating the mutation. Two
writers presenting the same expected revision cannot both commit.

## Retrieval

The retrieval URL is an opaque reference; its presence is not authorization.
The reference method accepts an identity only after a transport adapter has
authenticated it and returns bytes only when it equals the configured Platform.
It recomputes the digest before returning stored content.

A production binding must implement real authentication, authorization, key and
credential rotation, URL confidentiality, rate limiting, audit retention and
privacy controls. The in-process identity argument in this repository is a
testable trust boundary, not proof that any HTTP mechanism has been deployed.

## Compatibility and graduation

Unaware implementations ignore the vendor extension and continue processing the
ordinary Order. Implementations that negotiate the extension validate and apply
its full-snapshot contract. No `dev.ucp.*` identifier is allocated by this work.

Before any Core proposal, independent implementations should demonstrate a
shared need for the same identifiers, lifecycle states, ordering/replay rules,
access model and jurisdiction metadata across more than one fiscal regime. The
contract would also need maintainer agreement on whether a standard extension,
implementation guide or existing Order pattern is the appropriate home.

## Test matrix

`test_fiscal_ledger.py` covers persistence across restart, exact lost-response
retry, conflicting replay, content tampering with rollback, wrong Business,
immutable Business/Platform/Order/Checkout binding, stale revisions, a two-writer
race, cancel/replace history, invalid replacement order, authenticated retrieval
and strict UTC time.
`test_integration.py` continues to cover schema composition and complete Order
snapshot consumption against the pinned upstream schema set.

## Correction-document reference path (reviewer-aligned increment)

The preferred portable correction path issues **new** documents, without
altering the original invoice's identity or bytes. An original invoice may
have no Order adjustment. A new credit-note document has its own ID and may
include `preceding_documents` with a prior invoice number and optional date;
when referencing a local prior document, `document_id` must resolve and its
invoice number must match. A document may carry an **outbound** `adjustment_id`
to an existing Core Order adjustment; the Core adjustment does not refer back
to the document. Multiple documents can reference one adjustment, while some
adjustments require no document.

The Business supplies its current authoritative `order_adjustments` when
issuing documents in the fixture. This adapter validates correlation only;
the reference does not certify tax effect or prove an adjustment is final.
Core adjustments may change independently, including without a fiscal
revision increment. UCP explicitly permits some adjustments to update in
place; a reference is not immutable authorization evidence.

The earlier `cancel` and `replace` event operations remain **legacy,
non-normative local workflow simulation**; their state changes must not be
mapped to Italian FatturaPA cancellation, EN 16931 correction semantics, or
any jurisdiction's legal effects. They are not used by the new credit-note
correlation tests. Removing those legacy operations is a separate migration
decision; the production interpretation is not changed by this experiment.

Reference cases: EN 16931/Peppol preceding-invoice groups and Italy's
separate credit-note / new-invoice workflow, suggested independently on
[UCP #927](https://github.com/Universal-Commerce-Protocol/ucp/discussions/927).
No tax-authority validation or Core EP is claimed.
