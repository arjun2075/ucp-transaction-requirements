import hashlib
from copy import deepcopy
from pathlib import Path
import tempfile
import threading
import unittest

from fiscal_ledger import (
    AuthenticationError,
    FiscalDocumentLedger,
    FiscalLedgerError,
    ReplayConflict,
    RevisionConflict,
)


def fixture_document(document_id="doc1", replaces=None):
    content = f'<FixtureFiscalDocument id="{document_id}" notice="NOT A TAX DOCUMENT"/>'.encode()
    document = {
        "id": document_id,
        "state": "issued",
        "document_type": "fixture-fiscal-document",
        "jurisdiction": "MX",
        "media_type": "application/xml",
        "content_hash": hashlib.sha256(content).hexdigest(),
        "retrieval_url": f"https://business.example/documents/{document_id}",
        "issued_at": "2026-10-24T18:00:00Z",
    }
    if replaces:
        document["replaces"] = replaces
    document["invoice_number"] = f"INV-{document_id}"
    return document, content


def event(event_id, event_type, revision, document_id="doc1", replaces=None):
    value = {
        "event_id": event_id,
        "order_id": "ord1",
        "checkout_id": "chk1",
        "expected_revision": revision,
        "type": event_type,
        "occurred_at": "2026-10-24T18:00:00Z",
    }
    if event_type == "cancel":
        value["document_id"] = document_id
        return value, None
    value["document"], content = fixture_document(document_id, replaces)
    return value, content


class FiscalDocumentLedgerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.database = Path(self.temp.name) / "ledger.sqlite3"
        self.ledger = FiscalDocumentLedger(
            self.database,
            order_id="ord1",
            checkout_id="chk1",
            business_id="business1",
            platform_id="platform1",
        )

    def tearDown(self):
        self.temp.cleanup()

    def apply(self, value, content):
        return self.ledger.apply(value, content=content, authenticated_business="business1")

    def issue(self):
        value, content = event("evt-issue", "issue", 0)
        return self.apply(value, content)

    def test_issue_persists_full_order_snapshot(self):
        snapshot = self.issue()
        extension = snapshot["com.example.fiscal_documents"]
        self.assertEqual(extension["revision"], 1)
        self.assertEqual(extension["documents"][0]["state"], "issued")
        self.assertEqual(extension["events"][0]["id"], "evt-issue")
        reopened = FiscalDocumentLedger(
            self.database, order_id="ord1", checkout_id="chk1",
            business_id="business1", platform_id="platform1",
        )
        self.assertEqual(reopened.snapshot(), snapshot)

    def test_persisted_order_parties_cannot_be_rebound(self):
        for field, value in (
            ("checkout_id", "other-checkout"),
            ("business_id", "other-business"),
            ("platform_id", "other-platform"),
        ):
            arguments = {
                "order_id": "ord1", "checkout_id": "chk1",
                "business_id": "business1", "platform_id": "platform1",
            }
            arguments[field] = value
            with self.assertRaises(FiscalLedgerError):
                FiscalDocumentLedger(self.database, **arguments)

    def test_lost_response_retry_returns_identical_committed_snapshot(self):
        value, content = event("evt-issue", "issue", 0)
        first = self.apply(value, content)
        second = self.apply(value, content)
        self.assertEqual(second, first)
        self.assertEqual(second["com.example.fiscal_documents"]["revision"], 1)

    def test_event_id_cannot_be_reused_with_modified_payload(self):
        value, content = event("evt-issue", "issue", 0)
        self.apply(value, content)
        changed = deepcopy(value)
        changed["occurred_at"] = "2026-10-24T18:00:01Z"
        with self.assertRaises(ReplayConflict):
            self.apply(changed, content)

    def test_content_hash_tampering_is_rejected_atomically(self):
        value, content = event("evt-issue", "issue", 0)
        with self.assertRaises(FiscalLedgerError):
            self.apply(value, content + b"tampered")
        self.assertEqual(self.ledger.snapshot()["com.example.fiscal_documents"]["revision"], 0)

    def test_wrong_business_cannot_mutate(self):
        value, content = event("evt-issue", "issue", 0)
        with self.assertRaises(AuthenticationError):
            self.ledger.apply(value, content=content, authenticated_business="rogue")

    def test_wrong_order_or_checkout_binding_is_rejected(self):
        value, content = event("evt-issue", "issue", 0)
        value["checkout_id"] = "other"
        with self.assertRaises(FiscalLedgerError):
            self.apply(value, content)

    def test_stale_revision_is_rejected(self):
        self.issue()
        value, content = event("evt-other", "issue", 0, "doc2")
        with self.assertRaises(RevisionConflict):
            self.apply(value, content)

    def test_concurrent_writers_cannot_both_commit_same_revision(self):
        barrier = threading.Barrier(2)
        results = []

        def writer(number):
            value, content = event(f"evt-{number}", "issue", 0, f"doc{number}")
            barrier.wait()
            try:
                self.apply(value, content)
                results.append("committed")
            except RevisionConflict:
                results.append("conflict")

        threads = [threading.Thread(target=writer, args=(number,)) for number in (1, 2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertCountEqual(results, ["committed", "conflict"])
        self.assertEqual(self.ledger.snapshot()["com.example.fiscal_documents"]["revision"], 1)

    def test_cancel_then_replace_preserves_history(self):
        self.issue()
        cancel, _ = event("evt-cancel", "cancel", 1)
        canceled = self.apply(cancel, None)
        self.assertEqual(canceled["com.example.fiscal_documents"]["documents"][0]["state"], "canceled")
        replacement, content = event("evt-replace", "replace", 2, "doc2", "doc1")
        replaced = self.apply(replacement, content)
        extension = replaced["com.example.fiscal_documents"]
        self.assertEqual(extension["revision"], 3)
        self.assertEqual(extension["documents"][1]["replaces"], "doc1")
        self.assertEqual([item["type"] for item in extension["events"]], ["issue", "cancel", "replace"])

    def test_replacement_requires_canceled_target(self):
        self.issue()
        replacement, content = event("evt-replace", "replace", 1, "doc2", "doc1")
        with self.assertRaises(FiscalLedgerError):
            self.apply(replacement, content)

    def test_credit_note_issued_without_canceling_original(self):
        first = self.issue()
        self.assertEqual(first["com.example.fiscal_documents"]["documents"][0]["state"], "issued")
        value, content = event("evt-credit", "issue", 1, "credit1")
        value["document"]["document_type"] = "credit_note"
        value["document"]["adjustment_id"] = "adj1"
        value["document"]["preceding_documents"] = [{"document_id": "doc1", "invoice_number": "INV-doc1"}]
        value["order_adjustments"] = [{
            "id": "adj1", "type": "credit", "status": "completed",
            "occurred_at": "2026-10-24T18:05:00Z",
        }]
        result = self.apply(value, content)
        docs = result["com.example.fiscal_documents"]["documents"]
        self.assertEqual([d["state"] for d in docs], ["issued", "issued"])
        self.assertEqual(docs[1]["adjustment_id"], "adj1")
        self.assertEqual(docs[1]["preceding_documents"][0]["invoice_number"], "INV-doc1")
        self.assertEqual(result["adjustments"][0]["id"], "adj1")

    def test_multiple_documents_can_point_to_one_adjustment(self):
        self.issue()
        adjustments = [{"id": "adj1", "type": "credit", "status": "completed",
                        "occurred_at": "2026-10-24T18:05:00Z"}]
        for revision, name in [(1, "credit1"), (2, "newinvoice")]:
            value, content = event("evt-" + name, "issue", revision, name)
            value["document"]["adjustment_id"] = "adj1"
            value["document"]["preceding_documents"] = [{"document_id": "doc1", "invoice_number": "INV-doc1"}]
            value["order_adjustments"] = adjustments
            self.apply(value, content)
        documents = self.ledger.snapshot()["com.example.fiscal_documents"]["documents"]
        self.assertEqual([d.get("adjustment_id") for d in documents], [None, "adj1", "adj1"])

    def test_adjustment_without_document_is_valid(self):
        adjustments = [{"id": "adj-no-doc", "type": "refund", "status": "pending",
                        "occurred_at": "2026-10-24T18:05:00Z"}]
        result = self.ledger.snapshot(order_adjustments=adjustments)
        self.assertEqual(result["com.example.fiscal_documents"]["revision"], 0)
        self.assertEqual(result["com.example.fiscal_documents"]["documents"], [])
        self.assertEqual(result["adjustments"], adjustments)

    def test_unknown_adjustment_or_bad_local_predecessor_rolled_back(self):
        self.issue()
        value, content = event("evt-bad", "issue", 1, "credit1")
        value["document"]["adjustment_id"] = "missing"
        with self.assertRaises(FiscalLedgerError):
            self.apply(value, content)
        del value["document"]["adjustment_id"]
        value["document"]["preceding_documents"] = [{"document_id": "doc1", "invoice_number": "wrong"}]
        with self.assertRaises(FiscalLedgerError):
            self.apply(value, content)
        self.assertEqual(self.ledger.snapshot()["com.example.fiscal_documents"]["revision"], 1)

    def test_external_preceding_invoice_reference_is_supported(self):
        value, content = event("evt-external", "issue", 0, "credit1")
        value["document"]["preceding_documents"] = [{
            "invoice_number": "EXT-31", "issue_date": "2026-10-22",
        }]
        result = self.apply(value, content)
        self.assertEqual(result["com.example.fiscal_documents"]["documents"][0]["preceding_documents"][0]["invoice_number"], "EXT-31")

    def test_authenticated_retrieval_checks_identity_and_integrity(self):
        _, content = event("evt-issue", "issue", 0)
        self.issue()
        self.assertEqual(
            self.ledger.retrieve("doc1", authenticated_platform="platform1"), content
        )
        with self.assertRaises(AuthenticationError):
            self.ledger.retrieve("doc1", authenticated_platform="rogue")

    def test_timestamp_requires_explicit_utc(self):
        value, content = event("evt-issue", "issue", 0)
        value["occurred_at"] = "2026-10-24T18:00:00"
        with self.assertRaises(FiscalLedgerError):
            self.apply(value, content)


if __name__ == "__main__":
    unittest.main(verbosity=2)
