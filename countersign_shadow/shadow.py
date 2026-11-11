# SPDX-License-Identifier: Apache-2.0
"""Shadow mode: messages -> planner proposals -> code checks -> decision cards. That is the whole pipeline.

There is deliberately no execute / approve / send function anywhere in this package. A card is a draft that
the pilot user judges (right / wrong / dangerous); the judgement is the only thing that gets written.
"""
from __future__ import annotations

import hashlib
from datetime import datetime, timezone

from .checks import Policy, evaluate, own_account_draft, verdict
from .store import Workspace

VERDICT_NL = {
    "ok": "Countersign zou dit doorlaten (jij keurt goed)",
    "warn": "Countersign zou dit doorlaten, met een waarschuwing",
    "blocked": "Countersign zou dit tegenhouden",
    "flagged": "Countersign markeert dit als verdacht",
    "none": "Geen betaling nodig",
}
ACTION_NL = {
    "pay_invoice": "Factuur betalen", "refund": "Terugbetalen", "payout": "Betalen via PayPal",
    "send_invoice": "Factuur sturen", "send_reminder": "Herinnering sturen",
    "flag_suspicious": "Verdacht bericht", "no_action": "Geen betaling nodig",
}


def card_id(planner_name: str, source_id: str) -> str:
    return "c-" + hashlib.sha256(f"{planner_name}|{source_id}".encode()).hexdigest()[:8]


def draft_cards(ws: Workspace, planner, policy: Policy | None = None, messages: list[dict] | None = None) -> list[dict]:
    policy = policy or Policy.load(ws.root / "policy.json")
    msgs = messages if messages is not None else ws.messages
    history = ws.transactions
    cards: list[dict] = []
    proposals = planner.plan(msgs)
    by_id = {m["id"]: m for m in msgs}
    order = {m["id"]: (m.get("date") or "", i) for i, m in enumerate(msgs)}
    for prop in proposals:
        msg = by_id.get(prop["source_id"])
        if msg is None:
            continue
        prop = own_account_draft(prop, msg, history)  # a "payment" to your own account is no payment
        before = [m for m in msgs if order[m["id"]] < order[msg["id"]]]  # earlier date, or same date + earlier
        checks = evaluate(prop, msg, history, policy, earlier=cards, earlier_messages=before)
        v = verdict(prop["action"], checks)
        cards.append({
            **prop, "id": card_id(planner.name, prop["source_id"]), "planner": planner.name,
            "subject": msg.get("subject", ""), "from": msg.get("from", ""), "date": msg.get("date", ""),
            "checks": [c.as_dict() for c in checks], "verdict": v, "verdict_nl": VERDICT_NL[v],
            "action_nl": ACTION_NL.get(prop["action"], prop["action"]),
            "reasons": [c.uitleg or c.detail for c in sorted(checks, key=lambda c: c.severity != "block")
                        if not c.ok],
            "notes": [c.goed for c in checks if c.ok and c.goed],
            "created": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        })
    return cards


def run(ws: Workspace, planner, keep_other_planners: bool = True) -> list[dict]:
    """Draft cards and store them (replacing this planner's earlier cards; feedback is kept separately)."""
    new = draft_cards(ws, planner)
    old = ws.load("cards", []) if keep_other_planners else []
    kept = [c for c in old if c.get("planner") != planner.name]
    ws.save("cards", kept + new)
    return new
