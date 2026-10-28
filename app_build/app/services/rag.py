"""Local RAG pipeline (RF-17): incremental indexer + BM25 lexical search.

* Sources: payments, customers, recent event_stream days, automation rules
  (with run summaries) and the project docs (``specs/*.md``, project README).
* Incremental by ``content_hash``: an unchanged document is skipped (A-14);
  ``POST /v1/rag/reindex`` forces a full refresh.
* Index is a compact inverted term list per chunk (``rag_chunks.terms``) scored
  with BM25 at query time — no external embeddings, no extra dependencies.
"""

from __future__ import annotations

import logging
import math
import re
import sqlite3
import threading
import unicodedata
from datetime import timedelta
from hashlib import sha256
from pathlib import Path
from typing import Any

from ..config import Settings
from ..utils import canonical_json, iso_now, new_id, utc_now

logger = logging.getLogger("app.rag")

TOKEN_RE = re.compile(r"[a-z0-9_@.\-]{2,}")
CHUNK_SIZE = 700
CHUNK_OVERLAP = 120

STOPWORDS = {
    "a", "as", "o", "os", "de", "da", "do", "das", "dos", "em", "no", "na", "nos", "nas",
    "um", "uma", "e", "ou", "que", "para", "por", "com", "sem", "até", "de", "é", "foi",
    "the", "of", "for", "and", "or", "is", "was", "to", "in", "on", "at", "by", "with",
    "que", "se", "ao", "à", "ser", "sua", "seu", "pelo",
}

_INDEX_LOCK = threading.Lock()


# --------------------------------------------------------------------------- #
# Text utilities
# --------------------------------------------------------------------------- #
def _strip_accents(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value)
    return "".join(char for char in decomposed if not unicodedata.combining(char))


def tokenize(text: str) -> list[str]:
    tokens = TOKEN_RE.findall(_strip_accents(text.lower()))
    return [
        token
        for token in tokens
        if (token not in STOPWORDS) and (not token.isdigit() or len(token) >= 4)
    ]


def canonical_terms(tokens: list[str]) -> str:
    """Space-padded inverted term list stored in ``rag_chunks.terms``."""
    unique = list(dict.fromkeys(tokens))[:120]
    return " " + " ".join(unique) + " "


def content_hash(content: str) -> str:
    normalized = canonical_json(content.strip())
    return sha256(normalized.encode("utf-8")).hexdigest()


# --------------------------------------------------------------------------- #
# Document assembly (source extractors)
# --------------------------------------------------------------------------- #
def payment_document(conn: sqlite3.Connection, payment_id: str) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM payments WHERE id = ?", (payment_id,)).fetchone()
    if row is None:
        return None
    payment = dict(row)
    description = payment.get("description") or ""
    lines = [
        f"Pagamento {payment['id']}",
        f"status: {payment['status']}",
        f"metodo: {payment['method']}",
        f"valor: {payment['amount_cents']} centavos (R$ {payment['amount_cents'] / 100:.2f})",
        f"currency: {payment['currency']}",
        f"criado_em: {payment['created_at']}",
        f"atualizado_em: {payment['updated_at']}",
    ]
    if description:
        lines.append(f"descricao: {description}")
    if payment.get("customer_email"):
        lines.append(f"cliente: {payment['customer_email']}")
    if payment.get("failure_reason"):
        lines.append(f"motivo_falha: {payment['failure_reason']}")
    if payment.get("authorization_code"):
        lines.append(f"autorizacao: {payment['authorization_code']}")
    if payment["method"] == "pix":
        lines.append(f"pix_txid: {payment.get('pix_txid')}")
    if payment.get("card_last4"):
        lines.append(f"cartao: {payment.get('card_brand')} ****{payment.get('card_last4')}")
    return {
        "source_type": "payment",
        "source_ref": payment["id"],
        "title": f"Pagamento {payment['id']} ({payment['method']}, {payment['status']})",
        "content": "\n".join(lines),
    }


def customer_document(conn: sqlite3.Connection, source_ref: str) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT * FROM customers WHERE LOWER(email) = ? OR id = ?", (source_ref.lower(), source_ref)
    ).fetchone()
    if row is None:
        return None
    customer = dict(row)
    payments = conn.execute(
        "SELECT method, status, amount_cents, created_at FROM payments "
        "WHERE LOWER(customer_email) = ? ORDER BY created_at DESC LIMIT 20",
        (customer["email"].lower(),),
    ).fetchall()
    lines = [
        f"Cliente {customer['id']}",
        f"email: {customer['email']}",
        f"nome: {customer['name'] or '(sem nome)'}",
        f"status_lifecycle: {customer['lifecycle_status']}",
        f"primeiro_contato: {customer['first_seen_at']}",
    ]
    for payment in payments[:10]:
        lines.append(
            f"pagamento: {payment['method']} {payment['status']} {payment['amount_cents']} "
            f"centavos em {payment['created_at']}"
        )
    return {
        "source_type": "customer",
        "source_ref": customer["email"],
        "title": f"Cliente {customer['email']}",
        "content": "\n".join(lines),
    }


def _project_doc_files(settings: Settings) -> list[tuple[Path, str]]:
    """(path, relative reference) for the project documentation sources."""
    from ..config import BASE_DIR

    found: list[tuple[Path, str]] = []
    readme = BASE_DIR / "README.md"
    if readme.is_file():
        found.append((readme, "app_build/README.md"))
    specs_dir = settings.rag_specs_dir
    if specs_dir.is_dir():
        for child in sorted(specs_dir.glob("*.md")):
            found.append((child, f"specs/{child.name}"))
    return found


def project_document(path: Path, reference: str) -> dict[str, Any] | None:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    return {
        "source_type": "project_doc",
        "source_ref": reference,
        "title": f"Documento: {reference}",
        "content": text,
    }


def event_feed_document(conn: sqlite3.Connection, day: str) -> dict[str, Any] | None:
    """One rolling document per day of event_stream (refreshed by the sweep)."""
    rows = conn.execute(
        "SELECT type, payload, created_at FROM event_stream "
        "WHERE created_at LIKE ? ORDER BY created_at LIMIT 400",
        (f"{day}%",),
    ).fetchall()
    if not rows:
        return None
    lines = [f"Fluxo de eventos de {day}"]
    for row in rows:
        payload = row["payload"]
        lines.append(f"{row['created_at']} {row['type']} {payload[:160]}")
    return {
        "source_type": "event_feed",
        "source_ref": day,
        "title": f"Eventos {day}",
        "content": "\n".join(lines),
    }


def automation_document(conn: sqlite3.Connection, rule_id: str) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM automation_rules WHERE id = ?", (rule_id,)).fetchone()
    if row is None:
        return None
    rule = dict(row)
    runs = conn.execute(
        "SELECT status, COUNT(*) FROM automation_runs WHERE rule_id = ? GROUP BY status",
        (rule_id,),
    ).fetchall()
    run_summary = ", ".join(f"{r[0]}: {r[1]}" for r in runs) or "sem execuções"
    return {
        "source_type": "automation",
        "source_ref": rule_id,
        "title": f"Automação {rule['name']}",
        "content": (
            f"Regra de automação {rule['id']}\n"
            f"nome: {rule['name']}\n"
            f"evento: {rule['event_type']}\n"
            f"condições: {rule['conditions']}\n"
            f"ação: {rule['action_type']} {rule['action_config']}\n"
            f"habilitada: {bool(rule['enabled'])}\n"
            f"execuções: {run_summary}"
        ),
    }


# --------------------------------------------------------------------------- #
# Chunking + upsert
# --------------------------------------------------------------------------- #
def chunk_text(title: str, content: str) -> list[tuple[int, str]]:
    """Split into paragraph-aware chunks of ~CHUNK_SIZE chars (heading carried)."""
    body = content.strip()
    chunks: list[str] = []
    paragraphs = [paragraph.strip() for paragraph in re.split(r"\n\s*\n|\n(?=#)", body) if paragraph.strip()]
    current = ""
    for paragraph in paragraphs:
        if len(current) + len(paragraph) + 1 <= CHUNK_SIZE:
            current = f"{current}\n{paragraph}".strip()
        else:
            if current:
                chunks.append(current)
            while len(paragraph) > CHUNK_SIZE:
                chunks.append(paragraph[:CHUNK_SIZE])
                paragraph = paragraph[CHUNK_SIZE - CHUNK_OVERLAP :]
            current = paragraph
    if current:
        chunks.append(current)
    if not chunks:
        chunks = [body]
    header = f"{title}\n" if title else ""
    return [(index, f"{header}{chunk}") for index, chunk in enumerate(chunks)]


def _upsert_document(conn: sqlite3.Connection, document: dict[str, Any], *, force: bool = False) -> str:
    """Insert or refresh a document. Returns 'created' | 'updated' | 'unchanged'."""
    digest = content_hash(document["content"])
    existing = conn.execute(
        "SELECT id, content_hash FROM rag_documents WHERE source_type = ? AND source_ref = ?",
        (document["source_type"], document["source_ref"]),
    ).fetchone()
    if existing is not None and existing["content_hash"] == digest and not force:
        return "unchanged"
    now = iso_now()
    chunks = chunk_text(document["title"], document["content"])
    if existing is None:
        doc_id = new_id("rdc")
        conn.execute(
            "INSERT INTO rag_documents(id, source_type, source_ref, title, content, content_hash, indexed_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                doc_id,
                document["source_type"],
                document["source_ref"],
                document["title"],
                document["content"],
                digest,
                now,
            ),
        )
    else:
        doc_id = existing["id"]
        conn.execute(
            "DELETE FROM rag_chunks WHERE document_id = ?", (doc_id,)
        )
        conn.execute(
            "UPDATE rag_documents SET title = ?, content = ?, content_hash = ?, indexed_at = ? WHERE id = ?",
            (document["title"], document["content"], digest, now, doc_id),
        )
    for ordinal, text in chunks:
        conn.execute(
            "INSERT INTO rag_chunks(id, document_id, ordinal, text, terms, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (new_id("rc"), doc_id, ordinal, text, canonical_terms(tokenize(text)), now),
        )
    return "updated" if existing is not None else "created"


def index_source(db: Any, settings: Settings, source_type: str, source_ref: str) -> str:
    """Queue-consumer entry point for a single source document."""
    with db.transaction() as conn:
        if source_type == "payment":
            document = payment_document(conn, source_ref)
        elif source_type == "customer":
            document = customer_document(conn, source_ref)
        elif source_type == "automation":
            document = automation_document(conn, source_ref)
        else:
            document = None
        if document is None:
            return "missing"
        return _upsert_document(conn, document)


def reindex_all(db: Any, settings: Settings, *, force: bool = False) -> dict[str, int]:
    """Full sweep: payments, customers, automations, event feeds and docs."""
    from . import events as events_service

    stats = {"indexed": 0, "unchanged": 0, "payment": 0, "customer": 0, "automation": 0, "event_feed": 0, "project_doc": 0}
    with _INDEX_LOCK:
        with db.transaction() as conn:
            if force:
                conn.execute("DELETE FROM rag_chunks WHERE document_id IN (SELECT id FROM rag_documents)")
                conn.execute("DELETE FROM rag_documents")
            for row in conn.execute(
                "SELECT id FROM payments ORDER BY created_at DESC LIMIT ?",
                (settings.rag_index_payments_limit,),
            ).fetchall():
                _index_one(stats, _upsert_document, conn, payment_document(conn, row["id"]), "payment")
            for row in conn.execute(
                "SELECT id, email FROM customers ORDER BY created_at LIMIT ?",
                (settings.rag_index_customers_limit,),
            ).fetchall():
                _index_one(stats, _upsert_document, conn, customer_document(conn, row["email"]), "customer")
            for row in conn.execute("SELECT id FROM automation_rules").fetchall():
                _index_one(stats, _upsert_document, conn, automation_document(conn, row["id"]), "automation")
            for offset in range(settings.rag_index_feed_days):
                day = (utc_now() - timedelta(days=offset)).strftime("%Y-%m-%d")
                _index_one(stats, _upsert_document, conn, event_feed_document(conn, day), "event_feed")
            for path, reference in _project_doc_files(settings):
                _index_one(stats, _upsert_document, conn, project_document(path, reference), "project_doc")
    logger.info("rag reindex sweep", extra=stats)
    return stats


def _index_one(
    stats: dict[str, int],
    upsert: Any,
    conn: sqlite3.Connection,
    document: dict[str, Any] | None,
    bucket: str,
) -> None:
    if document is None:
        return
    outcome = upsert(conn, document)
    if outcome == "unchanged":
        stats["unchanged"] += 1
    else:
        stats["indexed"] += 1
        stats[bucket] += 1


def enqueue_source(conn: sqlite3.Connection, source_type: str, source_ref: str) -> None:
    """Enqueue a focused index job (caller owns the transaction)."""
    from . import queue as queue_service

    queue_service.enqueue(
        conn,
        queue_service.RAG_INDEX,
        {"source_type": source_type, "source_ref": source_ref},
        dedupe_key=f"rag.index:{source_type}:{source_ref}:cust",
        max_attempts=5,
    )


# --------------------------------------------------------------------------- #
# BM25 search
# --------------------------------------------------------------------------- #
def search(
    db: Any,
    query: str,
    *,
    limit: int = 5,
    source_type: str | None = None,
) -> list[dict[str, Any]]:
    tokens = tokenize(query)[:12]
    if not tokens:
        return []
    likes = [(" %" + token + " %") for token in tokens]
    clauses = " OR ".join("terms LIKE ?" for _ in likes)
    params: list[Any] = [*likes]
    where = f"WHERE ({clauses})"
    if source_type:
        where += " AND d.source_type = ?"
        params.append(source_type)
    with db.connection() as conn:
        stats = conn.execute(
            "SELECT COUNT(*) FROM rag_documents"
        ).fetchone()[0]
        if not stats:
            return []
        rows = conn.execute(
            "SELECT c.id AS chunk_id, c.text, c.terms, c.ordinal, "
            "d.id AS document_id, d.source_type, d.source_ref, d.title "
            "FROM rag_chunks c JOIN rag_documents d ON d.id = c.document_id "
            f"{where} LIMIT 400",
            params,
        ).fetchall()
    if not rows:
        return []

    total_chunks = float(len(rows))
    document_freq: dict[str, int] = {}
    chunk_terms = []
    for row in rows:
        terms = row["terms"].split()
        chunk_terms.append(terms)
        for token in set(tokens):
            if token in terms:
                document_freq[token] = document_freq.get(token, 0) + 1

    k1, b = 1.5, 0.75
    avg_len = (sum(len(t) for t in chunk_terms) / len(chunk_terms)) or 1.0
    scored = []
    query_set = set(tokens)
    for row, terms in zip(rows, chunk_terms):
        doc_len = max(len(terms), 1)
        score = 0.0
        for token in query_set:
            df = document_freq.get(token, 0)
            if not df:
                continue
            idf = math.log((total_chunks - df + 0.5) / (df + 0.5) + 1.0)
            tf = terms.count(token)
            score += idf * tf * (k1 + 1) / (tf + k1 * (1 - b + b * doc_len / avg_len))
        if score > 0:
            scored.append((score, row))
    scored.sort(key=lambda item: -item[0])
    results = []
    for score, row in scored[:limit]:
        snippet = _snippet(row["text"], query_set)
        results.append(
            {
                "chunk_id": row["chunk_id"],
                "document_id": row["document_id"],
                "source_type": row["source_type"],
                "source_ref": row["source_ref"],
                "title": row["title"],
                "ordinal": row["ordinal"],
                "score": round(score, 4),
                "snippet": snippet,
                "quote": snippet,
                "citation": f"[doc:{row['source_ref']}#chunk{row['ordinal']}]",
            }
        )
    return results


def _snippet(text: str, tokens: set[str], radius: int = 140) -> str:
    lowered = text.lower()
    position = -1
    for token in sorted(tokens, key=len, reverse=True):
        position = lowered.find(token)
        if position >= 0:
            break
    if position < 0:
        return text[: radius * 2].strip()
    start = max(0, position - radius)
    end = min(len(text), position + radius)
    prefix = "…" if start > 0 else ""
    suffix = "…" if end < len(text) else ""
    return prefix + text[start:end].strip() + suffix
