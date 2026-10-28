"""Agent IA (RF-16 / RF-19): answers grounded in local data + RAG.

Guarantees (ARQ-11):
* read-only by default — tools only *query* the merchant's own database;
* the two sensitive actions (``criar_nota``, ``retry_webhook``) never execute
  inside the chat: they return ``confirmation_required`` + an expirable token,
  and only ``POST /v1/agent/confirm`` — with the operator's token — performs
  the mutation (CA-17);
* deterministic local fallback (template/extraction) when no LLM env is set;
  with no supporting evidence the agent answers "sem dados suficientes" and
  never invents numbers (CA-16);
* PII is minimized: e-mails are masked when ``PII_REDACT_EMAIL=1``; secrets
  never enter the model context (they do not exist in the DB either);
* every interaction is appended to ``agent_logs`` with latency (RNF-14).
"""

from __future__ import annotations

import logging
import re
import threading
import time
from collections import defaultdict, deque
from datetime import timedelta
from typing import Any

from ..config import Settings
from ..errors import ApiError
from ..utils import iso_after, iso_now, new_id, canonical_json, utc_now
from . import events as events_hub
from . import rag as rag_service
from .automations import list_runs, list_rules
from .crm import customer_360, normalize_email
from .payments import get_payment
from .webhooks import public_payment

logger = logging.getLogger("app.agent")

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
PAYMENT_ID_RE = re.compile(r"pay_[0-9a-f]{8,40}")
DELIVERY_ID_RE = re.compile(r"whd_[0-9a-f]{8,40}")
CUSTOMER_ID_RE = re.compile(r"cus_[0-9a-f]{8,40}")

_RATE_LIMIT_LOCK = threading.Lock()
_RATE_WINDOWS: dict[str, deque[float]] = defaultdict(lambda: deque(maxlen=200))


# --------------------------------------------------------------------------- #
# Deterministic helpers
# --------------------------------------------------------------------------- #
def mask_email(email: str, enabled: bool) -> str:
    if not enabled:
        return email
    local, _, domain = email.partition("@")
    if not domain:
        return email
    visible = local[:1] if local else "*"
    return f"{visible}***@{domain}"


def redact(text: str, enabled: bool) -> str:
    if not enabled:
        return text
    return EMAIL_RE.sub(lambda match: mask_email(match.group(0), True), text)


def _scope(question: str) -> tuple[str, str, str]:
    """(start_iso, end_iso, human label) parsed from the question (default: hoje)."""
    from .analytics import _iso, DAY_FORMAT

    now = utc_now()
    if re.search(r"ontem", question, re.IGNORECASE):
        end = now.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(seconds=1)
        return _iso(end.replace(hour=0, minute=0, second=0, microsecond=0)), _iso(end), "ontem"
    if re.search(r"(7|sete)\s*dias|semana", question, re.IGNORECASE):
        return _iso(now - timedelta(days=7)), _iso(now), "últimos 7 dias"
    if re.search(r"30\s*dias|m[eê]s( passad[oa])?", question, re.IGNORECASE):
        return _iso(now - timedelta(days=30)), _iso(now), "últimos 30 dias"
    return f"{now.strftime(DAY_FORMAT)}T00:00:00.000000Z", _iso(now), "hoje"


def _method_filter(question: str) -> tuple[list[str] | None, str]:
    if re.search(r"\bpix\b", question, re.IGNORECASE):
        return ["pix"], "PIX"
    if re.search(r"cart[ãa]o|cartao|cr[ée]dito|debito|d[ée]bito|card", question, re.IGNORECASE):
        return ["credit_card", "debit_card"], "cartões"
    return None, "pagamentos"


def _status_filter(question: str) -> tuple[str | None, str]:
    if re.search(r"aprovad|suced|pagos|pago|approved", question, re.IGNORECASE):
        return "succeeded", "aprovados"
    if re.search(r"falha|falharam|recus|failed|recusados", question, re.IGNORECASE):
        return "failed", "recusados"
    if re.search(r"pendent", question, re.IGNORECASE):
        return "pending", "pendentes"
    return None, "no total de cobranças"


_money_re = re.compile(r"receita|fatur|volume|valor total|quanto entrou|revenue", re.IGNORECASE)
_rate_re = re.compile(r"taxa|convers[ãa]o|porcentagem|aprovação em %", re.IGNORECASE)


def is_metric_question(question: str) -> bool:
    return bool(
        re.search(
            r"quantos|quantas|receita|taxa|volume|total de|faturament|convers\w*|quanto entrou",
            question,
            re.IGNORECASE,
        )
    )


# --------------------------------------------------------------------------- #
# Read-only tools (structured DB access)
# --------------------------------------------------------------------------- #
def tool_metrics(db: Any, question: str) -> dict[str, Any] | None:
    """Compute a metric directly from the payments table (never guessed)."""
    if not is_metric_question(question):
        return None
    status, status_label = _status_filter(question)
    methods, method_label = _method_filter(question)
    start, end, scope_label = _scope(question)
    clauses = ["created_at >= ?", "created_at <= ?"]
    params: list[Any] = [start, end]
    if status:
        clauses.append("status = ?")
        params.append(status)
    if methods:
        clauses.append("method IN (%s)" % ",".join("?" for _ in methods))
        params.extend(methods)
    success_clauses = [
        "created_at >= ?",
        "created_at <= ?",
        "status = 'succeeded'",
    ]
    success_params: list[Any] = [start, end]
    if methods:
        success_clauses.append("method IN (%s)" % ",".join("?" for _ in methods))
        success_params.extend(methods)
    with db.connection() as conn:
        matched = conn.execute(
            f"SELECT COUNT(*) AS total, COALESCE(SUM(amount_cents), 0) AS volume_cents "
            f"FROM payments WHERE {' AND '.join(clauses)}",
            params,
        ).fetchone()
        succeeded = conn.execute(
            f"SELECT COUNT(*) AS total, COALESCE(SUM(amount_cents), 0) AS volume_cents "
            f"FROM payments WHERE {' AND '.join(success_clauses)}",
            success_params,
        ).fetchone()
    return {
        "tool": "tool_metrics",
        "window": {"from": start, "to": end, "label": scope_label},
        "method": method_label,
        "methods": methods,
        "status_filter": status,
        "status_label": status_label,
        "matched_count": int(matched["total"]),
        "matched_volume_cents": int(matched["volume_cents"]),
        "succeeded_count": int(succeeded["total"]),
        "succeeded_volume_cents": int(succeeded["volume_cents"]),
    }


def tool_payment(db: Any, question: str, *, merchant_id: str = "mch_demo") -> dict[str, Any] | None:
    match = PAYMENT_ID_RE.search(question)
    if not match:
        return None
    try:
        return {"tool": "tool_payment", "payment": public_payment(get_payment(db, merchant_id, match.group(0)))}
    except ApiError:
        return {"tool": "tool_payment", "payment": None, "payment_id": match.group(0)}


def tool_customer(db: Any, question: str) -> dict[str, Any] | None:
    email = EMAIL_RE.search(question)
    cid = CUSTOMER_ID_RE.search(question)
    if not email and not cid:
        return None
    try:
        view = customer_360(db, cid.group(0)) if cid else None
        if view is None and email:
            with db.connection() as conn:
                row = conn.execute(
                    "SELECT id FROM customers WHERE LOWER(email) = ?", (normalize_email(email.group(0)),)
                ).fetchone()
            view = customer_360(db, row["id"]) if row else None
        if view is None:
            return {"tool": "tool_customer", "customer": None}
        return {"tool": "tool_customer", "customer": view}
    except ApiError:
        return {"tool": "tool_customer", "customer": None}


def tool_automations(db: Any, question: str) -> dict[str, Any] | None:
    if not re.search(r"automa[a-z]*|regra", question, re.IGNORECASE):
        return None
    rules = list_rules(db)
    runs = list_runs(db, limit=5)
    return {"tool": "tool_automations", "rules": rules["data"], "recent_runs": runs["data"]}


# --------------------------------------------------------------------------- #
# Chat entry point
# --------------------------------------------------------------------------- #
def chat(db: Any, settings: Settings, question: str, *, merchant_id: str = "mch_demo") -> dict[str, Any]:
    started = time.perf_counter()
    _rate_limit(settings, question)
    question = question.strip()

    observations: list[dict[str, Any]] = []
    tools_used: list[str] = []

    for tool_fn in (tool_metrics, tool_payment, tool_customer, tool_automations):
        if tool_fn is tool_payment:
            outcome = tool_fn(db, question, merchant_id=merchant_id)
        else:
            outcome = tool_fn(db, question)
        tools_used.append(tool_fn.__name__)
        if outcome is not None:
            observations.append(outcome)

    metrics = next((obs for obs in observations if obs.get("tool") == "tool_metrics"), None)
    payment_obs = next((obs for obs in observations if obs.get("tool") == "tool_payment"), None)
    customer_obs = next((obs for obs in observations if obs.get("tool") == "tool_customer"), None)

    rag_chunks = rag_service.search(db, question, limit=4)
    sources: list[dict[str, Any]] = [
        {
            "citation": chunk["citation"],
            "title": chunk["title"],
            "snippet": chunk["snippet"],
            "source_type": chunk["source_type"],
            "source_ref": chunk["source_ref"],
        }
        for chunk in rag_chunks
    ]
    if metrics:
        sources.append(
            {
                "citation": "db:payments",
                "title": "Tabela payments (consulta estruturada)",
                "snippet": (
                    f"status={metrics['status_filter'] or 'qualquer'}, método={metrics['method']}, "
                    f"janela={metrics['window']['label']} -> {metrics['matched_count']} cobranças, "
                    f"{metrics['succeeded_count']} aprovadas"
                ),
                "source_type": "database",
                "source_ref": "payments",
            }
        )

    # Sensitive action detection (two-step confirmation) --------------------- #
    action = detect_action(db, settings, question, observations)
    if action is not None:
        reply = _reply(
            question=question,
            answer=(
                f"Para executar **{action['type']}** é preciso confirmar a ação. "
                "Nada foi alterado ainda."
            ),
            sources=sources,
            observations=observations,
            tools_used=tools_used,
            engine="local" if not settings.llm_configured else settings.llm_model or "llm",
            started=started,
            action=action,
        )
        _log(db, settings, reply, question, action_taken=action["type"] + ":pending")
        return reply

    # Deterministic answer --------------------------------------------------- #
    answer, evidence = _deterministic_answer(db, settings, question, metrics, payment_obs, customer_obs)
    if evidence is None and not rag_chunks and metrics is None and payment_obs is None:
        reply = _reply(
            question=question,
            answer=(
                "Sem dados suficientes para responder essa pergunta. "
                "Fontes consultadas: payments, clientes, eventos e automações — "
                "nenhuma evidência encontrada."
            ),
            sources=sources,
            observations=observations,
            tools_used=tools_used,
            engine="local",
            started=started,
        )
        _log(db, settings, reply, question)
        return reply

    if settings.llm_configured:
        llm_answer = _llm_answer(db, settings, question, observations, sources, answer)
        if llm_answer:
            answer = llm_answer
            engine = "llm"
        else:
            engine = "fallback"
    else:
        engine = "local"

    reply = _reply(
        question=question,
        answer=answer,
        sources=sources,
        observations=observations,
        tools_used=tools_used,
        engine=engine,
        started=started,
    )
    _log(db, settings, reply, question)
    return reply


def _deterministic_answer(
    db: Any,
    settings: Settings,
    question: str,
    metrics: dict[str, Any] | None,
    payment_obs: dict[str, Any] | None,
    customer_obs: dict[str, Any] | None,
) -> tuple[str, Any]:
    if metrics:
        label = metrics["window"]["label"]
        method_label = metrics["method"]
        if _rate_re.search(question) and metrics["status_filter"] is None:
            approved = metrics["succeeded_count"]
            attempts = metrics["matched_count"]
            return (
                f"A taxa de aprovação {label} de {method_label} foi de "
                f"{int(round(metrics['succeeded_count'] / attempts * 100)) if attempts else 0}% "
                f"({approved} de {attempts} cobranças).",
                metrics,
            )
        if _money_re.search(question):
            return (
                f"A receita {label} de {method_label} foi de "
                f"R$ {metrics['succeeded_volume_cents'] / 100:,.2f} "
                f"({metrics['succeeded_count']} cobranças aprovadas).",
                metrics,
            )
        if metrics["status_filter"]:
            return (
                f"Foi {metrics['matched_count']} cobrança(s) {metrics['status_label']} de {method_label} {label} "
                f"(volume de {metrics['matched_volume_cents']} centavos).",
                metrics,
            )
        return (
            f"{label} entraram {metrics['matched_count']} cobrança(s) de {method_label}; "
            f"{metrics['succeeded_count']} foram aprovadas "
            f"(R$ {metrics['succeeded_volume_cents'] / 100:,.2f} recebidos).",
            metrics,
        )
    if payment_obs:
        payment = payment_obs.get("payment")
        if payment is None:
            return (
                f"Não encontrei o pagamento '{payment_obs['payment_id']}' nos dados deste merchant.",
                None,
            )
        return (
            f"Pagamento {payment['id']}: {payment['method']} de "
            f"R$ {payment['amount'] / 100:,.2f} está {payment['status']}."
            + (f" Motivo: {payment['failure_reason']}." if payment.get("failure_reason") else "")
            + (f" Cliente: {mask_email(payment['customer']['email'], settings.pii_redact_email)}."
               if payment.get("customer") else ""),
            payment,
        )
    if customer_obs:
        customer = customer_obs.get("customer")
        if customer is None:
            return ("Não encontrei esse cliente nos dados deste merchant.", None)
        return (
            f"Cliente {mask_email(customer['email'], settings.pii_redact_email)} "
            f"({customer['lifecycle_status']}); {customer['payments_count']} pagamento(s), "
            f"{customer['succeeded_count']} aprovados, R$ {customer['total_succeeded_cents'] / 100:,.2f} recebidos.",
            customer,
        )
    return (
        "Consigo responder sobre pagamentos, clientes, automatizações e métricas — "
        "tente 'quantos PIX foram aprovados hoje?'.",
        None,
    )


# --------------------------------------------------------------------------- #
# Sensitive actions (two-step confirmation)
# --------------------------------------------------------------------------- #
def detect_action(
    db: Any, settings: Settings, question: str, observations: list[dict[str, Any]]
) -> dict[str, Any] | None:
    if re.search(r"retry|reprocess|reenvia", question, re.IGNORECASE):
        payment_match = PAYMENT_ID_RE.search(question)
        delivery_match = DELIVERY_ID_RE.search(question)
        if payment_match or delivery_match:
            return register_pending(
                db,
                settings,
                "retry_webhook",
                {
                    "payment_id": payment_match.group(0) if payment_match else None,
                    "delivery_id": delivery_match.group(0) if delivery_match else None,
                },
                description=(
                    f"Reenviar webhooks de {'delivery ' + delivery_match.group(0) if delivery_match else 'pagamento ' + payment_match.group(0)}"
                ),
            )
    if re.search(r"nota|anota", question, re.IGNORECASE) and re.search(
        r"adicion|cria|escrev|registrar?|\+", question, re.IGNORECASE
    ):
        email = EMAIL_RE.search(question)
        cid = CUSTOMER_ID_RE.search(question)
        payment_match = PAYMENT_ID_RE.search(question)
        customer_id = None
        email_value = None
        if cid:
            customer_id = cid.group(0)
        if email:
            email_value = normalize_email(email.group(0))
        elif payment_match:
            with db.connection() as conn:
                row = conn.execute(
                    "SELECT customer_email FROM payments WHERE id = ?", (payment_match.group(0),)
                ).fetchone()
            if row and row["customer_email"]:
                email_value = normalize_email(row["customer_email"])
        if customer_id or email_value:
            body = _note_body(question)
            return register_pending(
                db,
                settings,
                "criar_nota",
                {"customer_id": customer_id, "email": email_value, "body": body},
                description=f"Adicionar nota ao cliente {mask_email(email_value, settings.pii_redact_email) or customer_id}",
            )
    return None


def _note_body(question: str) -> str:
    quoted = re.search(r"[\"']([^\"']{3,500})[\"']", question)
    if quoted:
        return quoted.group(1).strip()
    after = re.search(r"nota[^:]*:\s*(.+)", question, re.IGNORECASE)
    if after:
        return after.group(1).strip()[:500]
    return question.strip()[:500]


def register_pending(
    db: Any, settings: Settings, action_type: str, payload: dict[str, Any], *, description: str
) -> dict[str, Any]:
    action_id = new_id("apa")
    token = new_id("act", 16)
    now = iso_now()
    expires_at = iso_after(settings.agent_confirm_ttl_seconds)
    with db.transaction() as conn:
        conn.execute(
            "INSERT INTO agent_pending_actions(id, token, action_type, payload, status, expires_at, created_at) "
            "VALUES (?, ?, ?, ?, 'pending', ?, ?)",
            (action_id, token, action_type, canonical_json(payload), expires_at, now),
        )
        events_hub.record_event(
            conn,
            "agent.action.pending",
            {"action_type": action_type, "action_id": action_id, "expires_at": expires_at},
        )
    return {
        "type": action_type,
        "action_id": action_id,
        "token": token,
        "description": description,
        "expires_at": expires_at,
        "confirmation_required": True,
        "payload": payload,
    }


def confirm(db: Any, settings: Settings, token: str) -> dict[str, Any]:
    started = time.perf_counter()
    with db.transaction() as conn:
        row = conn.execute(
            "SELECT * FROM agent_pending_actions WHERE token = ?", (token,)
        ).fetchone()
        if row is None:
            raise ApiError(404, "confirmation_not_found", "This confirmation token does not exist")
        if row["status"] == "expired" or (
            row["status"] == "pending" and row["expires_at"] <= iso_now()
        ):
            conn.execute(
                "UPDATE agent_pending_actions SET status = 'expired' WHERE id = ? AND status = 'pending'",
                (row["id"],),
            )
            raise ApiError(
                409, "confirmation_expired", "This confirmation token has expired. Ask again.",
                details={"action_id": row["id"]},
            )
        if row["status"] != "pending":
            raise ApiError(
                409, "confirmation_already_used", "This confirmation was already used or cancelled",
                details={"action_id": row["id"]},
            )
        cursor = conn.execute(
            "UPDATE agent_pending_actions SET status = 'executed', executed_at = ? "
            "WHERE id = ? AND status = 'pending'",
            (iso_now(), row["id"]),
        )
        if cursor.rowcount != 1:  # race with another confirm call
            raise ApiError(409, "confirmation_already_used", "This confirmation was already used")
        event_id = events_hub.record_event(
            conn,
            "agent.action.executed",
            {"action_type": row["action_type"], "action_id": row["id"]},
            fanout=settings.event_fanout,
        )
    result = _execute_action(db, settings, row["action_type"], row["payload"])
    latency_ms = int((time.perf_counter() - started) * 1000)
    _log(
        db,
        settings,
        {
            "latency_ms": latency_ms,
            "answer": canonical_json({"confirmed": True, **result}),
            "rag_sources": [],
        },
        "(confirmação de ação) " + row["action_type"],
        action_taken=f"{row['action_type']}:executed",
    )
    return {
        "object": "agent_confirmation",
        "action_type": row["action_type"],
        "status": "executed",
        "result": result,
        "latency_ms": latency_ms,
    }


def cancel(db: Any, token: str) -> dict[str, Any]:
    """Optional explicit refuse (never required by the contract, useful in UI)."""
    with db.transaction() as conn:
        row = conn.execute(
            "SELECT id FROM agent_pending_actions WHERE token = ? AND status = 'pending'", (token,)
        ).fetchone()
        if row is None:
            raise ApiError(404, "confirmation_not_found", "This confirmation token does not exist")
        conn.execute(
            "UPDATE agent_pending_actions SET status = 'cancelled' WHERE id = ? AND status = 'pending'",
            (row["id"],),
        )
    return {"cancelled": True}


def _execute_action(db: Any, settings: Settings, action_type: str, payload_json: str) -> dict[str, Any]:
    from .webhook_admin import requeue_delivery_row

    import json

    try:
        payload = json.loads(payload_json or "{}")
    except ValueError:
        payload = {}

    if action_type == "criar_nota":
        from .crm import add_note

        email = normalize_email(payload.get("email"))
        customer_id = payload.get("customer_id")
        if not customer_id and email:
            with db.connection() as conn:
                row = conn.execute(
                    "SELECT id FROM customers WHERE LOWER(email) = ?", (email,)
                ).fetchone()
            if row is None:
                raise ApiError(404, "customer_not_found", "Customer no longer exists")
            customer_id = row["id"]
        if not customer_id:
            raise ApiError(422, "invalid_action_payload", "The pending note has no customer attached")
        note = add_note(
            db, customer_id, str(payload.get("body") or "nota registrada via assistente"),
            fanout=settings.event_fanout,
        )
        return {"note": note}

    if action_type == "retry_webhook":
        payment_id = payload.get("payment_id")
        delivery_id = payload.get("delivery_id")
        clauses, params = [], []
        if delivery_id:
            clauses.append("d.id = ?")
            params.append(delivery_id)
        if payment_id:
            clauses.append("d.payment_id = ?")
            params.append(payment_id)
        if not clauses:
            raise ApiError(422, "invalid_action_payload", "No delivery to requeue")
        with db.connection() as conn:
            failed = conn.execute(
                "SELECT d.id FROM webhook_deliveries d WHERE d.status = 'failed' AND "
                + " AND ".join(clauses),
                params,
            ).fetchall()
        if not failed:
            raise ApiError(
                409, "no_failed_deliveries", "There are no failed webhooks to requeue for this target"
            )
        with db.transaction() as conn:
            requeued: list[str] = []
            for row in failed:
                requeue_delivery_row(conn, row["id"])
                requeued.append(row["id"])
            events_hub.record_event(
                conn,
                "webhook.requeued",
                {"deliveries": requeued, "payment_id": payment_id, "via": "agent"},
                fanout=settings.event_fanout,
            )
        return {"requeued_deliveries": requeued}

    raise ApiError(422, "invalid_action_type", f"Unknown action type: {action_type}")


# --------------------------------------------------------------------------- #
# Pluggable LLM (OpenAI-compatible) + rate limit + audit
# --------------------------------------------------------------------------- #
def _rate_limit(settings: Settings, question: str) -> None:
    now = time.monotonic()
    key = "merchant"
    with _RATE_LIMIT_LOCK:
        window = _RATE_WINDOWS[key]
        while window and now - window[0] > 60.0:
            window.popleft()
        if len(window) >= settings.agent_rate_limit_per_minute:
            raise ApiError(
                429, "rate_limited", "Too many questions per minute. Try again shortly."
            )
        window.append(now)


def _llm_answer(
    db: Any,
    settings: Settings,
    question: str,
    observations: list[dict[str, Any]],
    sources: list[dict[str, Any]],
    deterministic: str,
) -> str | None:
    """Call the configured OpenAI-compatible endpoint; None on any failure."""
    import json as json_lib

    import httpx

    context = {
        "pergunta": question,
        "observacoes": observations,
        "fontes": [
            {"citation": s["citation"], "snippet": s["snippet"]} for s in sources[:6]
        ],
        "resposta_calcglobal_local": deterministic,
    }
    redacted = redact(json_lib.dumps(context, ensure_ascii=False, default=str), settings.pii_redact_email)
    try:
        response = httpx.post(
            settings.llm_base_url.rstrip("/") + "/chat/completions",
            headers=(
                {"Authorization": f"Bearer {settings.llm_api_key}"}
                if settings.llm_api_key
                else {}
            ),
            json={
                "model": settings.llm_model,
                "messages": [
                    {
                        "role": "system",
                        "content": (
                            "Você é o assistente do FluxPay. Responda em português do Brasil, "
                            "curto e factual, usando SOMENTE o contexto em JSON fornecido. "
                            "Nunca invente números: se o contexto não traz a resposta, diga "
                            "'sem dados suficientes'. Cite as fontes entre colchetes."
                        ),
                    },
                    {"role": "user", "content": redacted},
                ],
                "temperature": 0.2,
                "max_tokens": 400,
            },
            timeout=settings.agent_llm_timeout_seconds,
        )
        response.raise_for_status()
        data = response.json()
        content = data["choices"][0]["message"]["content"]
        return redact(str(content).strip(), settings.pii_redact_email) or None
    except Exception as exc:  # noqa: BLE001 - LLM is optional (A-09)
        logger.warning("llm unavailable; falling back locally", extra={"error": str(exc)[:200]})
        return None


def _reply(
    *,
    question: str,
    answer: str,
    sources: list[dict[str, Any]],
    observations: list[dict[str, Any]],
    tools_used: list[str],
    engine: str,
    started: float,
    action: dict[str, Any] | None = None,
) -> dict[str, Any]:
    latency_ms = int((time.perf_counter() - started) * 1000)
    return {
        "object": "agent_reply",
        "question": question,
        "answer": answer,
        "sources": sources,
        "action": action,
        "confirmation_required": bool(action and action.get("confirmation_required")),
        "engine": engine,
        "tools_used": tools_used,
        "latency_ms": latency_ms,
    }


def _log(
    db: Any,
    settings: Settings,
    reply: dict[str, Any],
    question: str,
    *,
    action_taken: str | None = None,
) -> None:
    redact_enabled = settings.pii_redact_email
    try:
        with db.transaction() as conn:
            conn.execute(
                "INSERT INTO agent_logs(id, question, rag_sources, action_taken, answer, latency_ms, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    new_id("agl"),
                    redact(question, redact_enabled)[:1000],
                    redact(canonical_json(reply.get("sources") or []), redact_enabled)[:4000],
                    action_taken or ("pending_confirmation" if reply.get("confirmation_required") else None),
                    redact(str(reply.get("answer") or ""), redact_enabled)[:4000],
                    int(reply.get("latency_ms") or 0),
                    iso_now(),
                ),
            )
            events_hub.record_event(
                conn,
                "agent.chat",
                {
                    "engine": reply.get("engine", "local"),
                    "latency_ms": reply.get("latency_ms"),
                    "sources": len(reply.get("sources") or []),
                    "action": action_taken,
                },
            )
    except Exception:  # noqa: BLE001 - audit must not break the chat
        logger.exception("agent log failed")
