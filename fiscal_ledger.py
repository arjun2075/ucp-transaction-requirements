"""Durable reference adapter for a vendor-namespaced fiscal document ledger.

This is an interoperability experiment, not a tax authority integration and not
an assertion that a document is legally valid.  Transport adapters must supply
the independently authenticated Business or Platform identity.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime
import hashlib
import json
from pathlib import Path
import sqlite3
from typing import Any

from schema_support import validate


F = "com.example.fiscal_documents"


class FiscalLedgerError(ValueError):
    """Base error for rejected lifecycle operations."""


class AuthenticationError(FiscalLedgerError):
    pass


class RevisionConflict(FiscalLedgerError):
    pass


class ReplayConflict(FiscalLedgerError):
    pass


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def _parse_timestamp(value: str) -> None:
    """Reject timestamps that are not explicit RFC 3339 UTC instants."""
    if not value.endswith("Z"):
        raise FiscalLedgerError("occurred_at must be an explicit UTC instant")
    datetime.fromisoformat(value[:-1] + "+00:00")


class FiscalDocumentLedger:
    """SQLite-backed event adapter with atomic revision and replay handling."""

    def __init__(
        self,
        database: str | Path,
        *,
        order_id: str,
        checkout_id: str,
        business_id: str,
        platform_id: str,
    ) -> None:
        self.database = str(database)
        self.order_id = order_id
        self.checkout_id = checkout_id
        self.business_id = business_id
        self.platform_id = platform_id
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database, timeout=5, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS ledger (
                    order_id TEXT PRIMARY KEY,
                    checkout_id TEXT NOT NULL,
                    business_id TEXT NOT NULL,
                    platform_id TEXT NOT NULL,
                    revision INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS documents (
                    order_id TEXT NOT NULL,
                    document_id TEXT NOT NULL,
                    state TEXT NOT NULL CHECK (state IN ('issued', 'canceled')),
                    document_type TEXT NOT NULL,
                    jurisdiction TEXT NOT NULL,
                    media_type TEXT NOT NULL,
                    content_hash TEXT NOT NULL,
                    retrieval_url TEXT NOT NULL,
                    issued_at TEXT NOT NULL,
                    replaces TEXT,
                    content BLOB NOT NULL,
                    PRIMARY KEY (order_id, document_id),
                    FOREIGN KEY (order_id) REFERENCES ledger(order_id)
                );
                CREATE TABLE IF NOT EXISTS events (
                    event_id TEXT PRIMARY KEY,
                    order_id TEXT NOT NULL,
                    revision INTEGER NOT NULL,
                    event_type TEXT NOT NULL,
                    occurred_at TEXT NOT NULL,
                    document_id TEXT NOT NULL,
                    request_hash TEXT NOT NULL,
                    response_json TEXT NOT NULL,
                    FOREIGN KEY (order_id) REFERENCES ledger(order_id)
                );
                """
            )
            connection.execute(
                "INSERT OR IGNORE INTO ledger VALUES (?, ?, ?, ?, 0)",
                (self.order_id, self.checkout_id, self.business_id, self.platform_id),
            )
            binding = connection.execute(
                "SELECT checkout_id, business_id, platform_id FROM ledger WHERE order_id = ?",
                (self.order_id,),
            ).fetchone()
            expected = (self.checkout_id, self.business_id, self.platform_id)
            actual = (binding["checkout_id"], binding["business_id"], binding["platform_id"])
            if actual != expected:
                raise FiscalLedgerError("existing order binding cannot be changed")

    def apply(
        self,
        event: dict[str, Any],
        *,
        content: bytes | None,
        authenticated_business: str,
    ) -> dict[str, Any]:
        """Apply one authenticated event and return the committed full snapshot.

        The same event ID and byte-identical request returns the original
        response. Reusing an event ID for a different request is rejected.
        """
        if authenticated_business != self.business_id:
            raise AuthenticationError("wrong Business identity")
        self._validate_event(event, content)
        request_hash = hashlib.sha256(_canonical(event) + b"\0" + (content or b"")).hexdigest()

        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            replay = connection.execute(
                "SELECT request_hash, response_json FROM events WHERE event_id = ?",
                (event["event_id"],),
            ).fetchone()
            if replay:
                if replay["request_hash"] != request_hash:
                    raise ReplayConflict("event_id was already used for a different request")
                connection.commit()
                return json.loads(replay["response_json"])

            ledger = connection.execute(
                "SELECT * FROM ledger WHERE order_id = ?", (self.order_id,)
            ).fetchone()
            if event["order_id"] != ledger["order_id"] or event["checkout_id"] != ledger["checkout_id"]:
                raise FiscalLedgerError("event is not bound to this order and checkout")
            if event["expected_revision"] != ledger["revision"]:
                raise RevisionConflict(
                    f"expected revision {event['expected_revision']}, current revision is {ledger['revision']}"
                )

            if event["type"] == "issue":
                self._issue(connection, event["document"], content)
                document_id = event["document"]["id"]
            elif event["type"] == "cancel":
                document_id = event["document_id"]
                changed = connection.execute(
                    "UPDATE documents SET state = 'canceled' "
                    "WHERE order_id = ? AND document_id = ? AND state = 'issued'",
                    (self.order_id, document_id),
                ).rowcount
                if changed != 1:
                    raise FiscalLedgerError("only an issued document can be canceled")
            else:
                replaced = connection.execute(
                    "SELECT state FROM documents WHERE order_id = ? AND document_id = ?",
                    (self.order_id, event["document"]["replaces"]),
                ).fetchone()
                if not replaced or replaced["state"] != "canceled":
                    raise FiscalLedgerError("replacement target must exist and be canceled")
                self._issue(connection, event["document"], content)
                document_id = event["document"]["id"]

            revision = ledger["revision"] + 1
            connection.execute(
                "UPDATE ledger SET revision = ? WHERE order_id = ?", (revision, self.order_id)
            )
            snapshot = self._snapshot(connection)
            validate(snapshot, "order-composed.json")
            response_json = json.dumps(snapshot, sort_keys=True, separators=(",", ":"))
            connection.execute(
                "INSERT INTO events VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    event["event_id"], self.order_id, revision, event["type"],
                    event["occurred_at"], document_id, request_hash, response_json,
                ),
            )
            # Include the just-committed event in both the response and replay record.
            snapshot = self._snapshot(connection)
            validate(snapshot, "order-composed.json")
            response_json = json.dumps(snapshot, sort_keys=True, separators=(",", ":"))
            connection.execute(
                "UPDATE events SET response_json = ? WHERE event_id = ?",
                (response_json, event["event_id"]),
            )
            connection.commit()
            return deepcopy(snapshot)

    def _validate_event(self, event: dict[str, Any], content: bytes | None) -> None:
        required = {"event_id", "order_id", "checkout_id", "expected_revision", "type", "occurred_at"}
        if not required.issubset(event):
            raise FiscalLedgerError("event is missing required fields")
        if event["type"] not in {"issue", "cancel", "replace"}:
            raise FiscalLedgerError("unsupported event type")
        _parse_timestamp(event["occurred_at"])
        if event["type"] == "cancel":
            if "document_id" not in event or content is not None:
                raise FiscalLedgerError("cancel requires document_id and no content")
        else:
            if "document" not in event or content is None:
                raise FiscalLedgerError("issue and replace require document metadata and content")
            document = event["document"]
            required_document = {
                "id", "state", "document_type", "jurisdiction", "media_type",
                "content_hash", "retrieval_url", "issued_at",
            }
            if not required_document.issubset(document) or document["state"] != "issued":
                raise FiscalLedgerError("invalid issued document metadata")
            _parse_timestamp(document["issued_at"])
            if hashlib.sha256(content).hexdigest() != document["content_hash"]:
                raise FiscalLedgerError("content does not match content_hash")
            if event["type"] == "replace" and not document.get("replaces"):
                raise FiscalLedgerError("replacement requires replaces")
            if event["type"] == "issue" and document.get("replaces"):
                raise FiscalLedgerError("issue cannot carry replaces")

    def _issue(self, connection: sqlite3.Connection, document: dict[str, Any], content: bytes) -> None:
        try:
            connection.execute(
                "INSERT INTO documents VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    self.order_id, document["id"], "issued", document["document_type"],
                    document["jurisdiction"], document["media_type"], document["content_hash"],
                    document["retrieval_url"], document["issued_at"], document.get("replaces"), content,
                ),
            )
        except sqlite3.IntegrityError as error:
            raise FiscalLedgerError("duplicate document ID") from error

    def _snapshot(self, connection: sqlite3.Connection) -> dict[str, Any]:
        ledger = connection.execute(
            "SELECT revision FROM ledger WHERE order_id = ?", (self.order_id,)
        ).fetchone()
        documents = [
            {key: row[key] for key in (
                "document_id", "state", "document_type", "jurisdiction", "media_type",
                "content_hash", "retrieval_url", "issued_at", "replaces",
            ) if row[key] is not None}
            for row in connection.execute(
                "SELECT document_id, state, document_type, jurisdiction, media_type, "
                "content_hash, retrieval_url, issued_at, replaces FROM documents "
                "WHERE order_id = ? ORDER BY rowid", (self.order_id,),
            )
        ]
        for document in documents:
            document["id"] = document.pop("document_id")
        events = [dict(row) for row in connection.execute(
            "SELECT event_id AS id, event_type AS type, occurred_at, document_id "
            "FROM events WHERE order_id = ? ORDER BY revision", (self.order_id,),
        )]
        return {
            "ucp": {"version": "2026-10-23", "status": "success", "capabilities": {
                "dev.ucp.shopping.order": [{"version": "2026-10-23"}],
                F: [{"version": "2026-10-23"}],
            }},
            "id": self.order_id,
            "checkout_id": self.checkout_id,
            "permalink_url": f"https://business.example/orders/{self.order_id}",
            "line_items": [{
                "id": "li1", "item": {"id": "sku1", "title": "Fixture goods", "price": 1000},
                "quantity": {"original": 1, "total": 1, "fulfilled": 0},
                "status": "processing",
                "totals": [{"type": "subtotal", "amount": 1000}, {"type": "total", "amount": 1000}],
            }],
            "fulfillment": {"expectations": [], "events": []},
            "currency": "MXN",
            "totals": [{"type": "subtotal", "amount": 1000}, {"type": "total", "amount": 1000}],
            F: {"revision": ledger["revision"], "documents": documents, "events": events},
        }

    def snapshot(self) -> dict[str, Any]:
        with self._connect() as connection:
            snapshot = self._snapshot(connection)
        validate(snapshot, "order-composed.json")
        return snapshot

    def retrieve(
        self,
        document_id: str,
        *,
        authenticated_platform: str,
    ) -> bytes:
        """Return content only after independent Platform authentication."""
        if authenticated_platform != self.platform_id:
            raise AuthenticationError("wrong Platform identity")
        with self._connect() as connection:
            row = connection.execute(
                "SELECT content, content_hash FROM documents WHERE order_id = ? AND document_id = ?",
                (self.order_id, document_id),
            ).fetchone()
        if not row:
            raise FiscalLedgerError("unknown document")
        content = bytes(row["content"])
        if hashlib.sha256(content).hexdigest() != row["content_hash"]:
            raise FiscalLedgerError("stored content failed integrity check")
        return content
