# SPDX-License-Identifier: Apache-2.0
"""Planners turn one message into one *proposal*. A proposal is only data; nothing can execute it.

Proposal shape (all planners):
    {"source_id", "action", "amount" (decimal string or None), "currency",
     "payee": {"name", "iban", "email"}, "invoice_ref", "order_ref",
     "evidence_quote", "rationale"}

Actions: pay_invoice, refund, payout, send_invoice, send_reminder, flag_suspicious, no_action.
"""
from __future__ import annotations

ACTIONS = ("pay_invoice", "refund", "payout", "send_invoice", "send_reminder", "flag_suspicious", "no_action")
MONEY_OUT = {"pay_invoice", "refund", "payout"}


def message_text(msg: dict) -> str:
    """Subject + body + the text of readable PDF attachments: everything a planner and the checks may look at."""
    parts = [msg.get("subject", ""), msg.get("body", "")]
    for pdf in msg.get("pdfs") or []:
        if pdf.get("text"):
            parts.append(f"[Bijlage {pdf.get('name', 'PDF')}]\n{pdf['text']}")
    return "\n".join(parts)


def empty_proposal(source_id: str, action: str = "no_action", rationale: str = "") -> dict:
    return {"source_id": source_id, "action": action, "amount": None, "currency": "EUR",
            "payee": {"name": "", "iban": "", "email": ""}, "invoice_ref": "", "order_ref": "",
            "evidence_quote": "", "rationale": rationale}


def normalise_proposal(source_id: str, p: dict) -> dict:
    """Coerce any planner's output (including an LLM's JSON) into the proposal shape. Unknown -> no_action."""
    out = empty_proposal(source_id)
    if not isinstance(p, dict):
        out["rationale"] = "planner returned no usable proposal"
        return out
    action = str(p.get("action") or "no_action").strip().lower()
    out["action"] = action if action in ACTIONS else "no_action"
    amt = p.get("amount")
    out["amount"] = None if amt in (None, "", 0) else str(amt).strip()
    out["currency"] = str(p.get("currency") or "EUR").upper()[:3]
    payee = p.get("payee") if isinstance(p.get("payee"), dict) else {}
    for k in ("name", "iban", "email"):
        out["payee"][k] = str(payee.get(k) or p.get(f"payee_{k}") or "").strip()
    for k in ("invoice_ref", "order_ref", "evidence_quote", "rationale"):
        out[k] = str(p.get(k) or "").strip()
    return out


def make_planner(kind: str = "rules", **kw):
    if kind == "rules":
        from .rules import RulePlanner
        return RulePlanner()
    if kind == "claude":
        from .llm import ClaudePlanner
        return ClaudePlanner(**kw)
    if kind == "local":
        from .llm import OpenAICompatPlanner
        return OpenAICompatPlanner(**kw)
    raise ValueError(f"unknown planner {kind!r} (rules | claude | local)")
