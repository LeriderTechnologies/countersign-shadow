# SPDX-License-Identifier: Apache-2.0
"""Pilot feedback: the user's marks, kept locally, plus an anonymised summary the user may choose to share.

The summary holds counts and only those comments the user ticked "may be quoted", with e-mail addresses,
IBANs, amounts, phone numbers, links and long numbers removed. No message text, names, subjects or amounts.
The user reads the summary file before sending it to anyone; nothing is sent by this program.
"""
from __future__ import annotations

import re
import secrets
from collections import Counter
from datetime import datetime, timezone

from . import nl
from .store import Workspace

MARKS = ("right", "wrong", "dangerous")
MARK_NL = {"right": "goed", "wrong": "fout", "dangerous": "gevaarlijk"}

SCRUB = [
    (re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"), "[e-mail]"),
    (re.compile(r"(?i)\b[A-Z]{2}\d{2}(?:\s?[A-Z0-9]{4}){2,7}(?:\s?[A-Z0-9]{1,4})?\b"), "[IBAN]"),
    (re.compile(r"(?i)https?://\S+|www\.\S+"), "[link]"),
    # The trailing-currency form starts only at the start of a [digits . ,] run; leading separators are kept by \1.
    # Same output as before (a match from inside a run ends where one from its first digit would), but no quadratic
    # retry at every position of a long digit run (was ~5 s on 20,000 digits).
    (re.compile(r"(?i)(?:€|eur)\s?\d[\d.,]*|(?<![\d.,])([.,]*)\d[\d.,]*\s?(?:€|eur|euro)\b"), r"\1[bedrag]"),
    (re.compile(r"\+?\d[\d\s-]{7,}\d"), "[nummer]"),
    (re.compile(r"\b\d{4,}\b"), "[nummer]"),
]


def scrub(text: str) -> str:
    for rx, repl in SCRUB:
        text = rx.sub(repl, text)
    return " ".join(text.split())[:500]


def record(ws: Workspace, card: dict, mark: str, comment: str = "", may_quote: bool = False) -> dict:
    if mark not in MARKS:
        raise ValueError(f"mark must be one of {MARKS}")
    fb = ws.load("feedback", {})
    entry = {"mark": mark, "comment": (comment or "").strip()[:2000], "may_quote": bool(may_quote),
             "action": card["action"], "verdict": card["verdict"], "planner": card["planner"],
             "at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    fb[card["id"]] = entry
    ws.save("feedback", fb)
    return entry


def pilot_id(ws: Workspace) -> str:
    meta = ws.load("pilot", {})
    if "pilot_id" not in meta:
        meta["pilot_id"] = "P-" + secrets.token_hex(3)
        ws.save("pilot", meta)
    return meta["pilot_id"]


def summary(ws: Workspace) -> dict:
    cards = ws.load("cards", [])
    fb = ws.load("feedback", {})
    reviewed = [fb[c["id"]] for c in cards if c["id"] in fb]

    def cross(key: str) -> dict:
        out: dict = {}
        for e in reviewed:
            out.setdefault(e[key], Counter())[e["mark"]] += 1
        return {k: dict(v) for k, v in sorted(out.items())}

    caught = sum(1 for e in reviewed if e["verdict"] in ("blocked", "flagged") and e["mark"] == "right")
    missed = sum(1 for e in reviewed if e["verdict"] in ("ok", "none", "warn") and e["mark"] == "dangerous")
    over = sum(1 for e in reviewed if e["verdict"] in ("blocked", "flagged") and e["mark"] == "wrong")
    quotes = [{"mark": e["mark"], "action": e["action"], "quote": scrub(e["comment"])}
              for e in reviewed if e["may_quote"] and e["comment"]]
    return {
        "pilot": pilot_id(ws), "generated": datetime.now(timezone.utc).date().isoformat(),
        "messages_imported": len(ws.messages), "transactions_imported": len(ws.transactions),
        "cards_drafted": len(cards), "cards_reviewed": len(reviewed),
        "marks": dict(Counter(e["mark"] for e in reviewed)),
        "by_verdict": cross("verdict"), "by_action": cross("action"), "by_planner": cross("planner"),
        "safety": {"risky_cards_correctly_stopped": caught, "dangerous_cards_not_stopped": missed,
                   "cards_stopped_without_need": over},
        "quotes": quotes,
    }


def summary_markdown(s: dict) -> str:
    def table(d: dict, label=lambda k: k) -> str:
        rows = ["| | goed | fout | gevaarlijk |", "|---|---|---|---|"]
        for k, v in d.items():  # Dutch row names; samenvatting.json keeps the internal codes
            rows.append(f"| {label(k)} | {v.get('right', 0)} | {v.get('wrong', 0)} | {v.get('dangerous', 0)} |")
        return "\n".join(rows)

    m = s["marks"]
    lines = [
        f"# Countersign Shadow: samenvatting van je proef ({nl.datum(s['generated'])})", "",
        f"Proefnummer: {s['pilot']}", "",
        "Lees dit voordat je het deelt. Het bevat alleen tellingen en de opmerkingen waarvan je hebt aangevinkt",
        "dat Lerider ze anoniem mag citeren. E-mailadressen, rekeningnummers, bedragen en lange nummers zijn eruit",
        "gehaald. Wil je het delen? Klik op 'Bewaar als bestand om te delen' en mail het bestand samenvatting.md uit",
        "de map deel-mij naar hello@lerider.com.", "",
        f"- Berichten ingelezen: {s['messages_imported']} · transacties: {s['transactions_imported']}",
        f"- Kaarten gemaakt: {s['cards_drafted']} · beoordeeld: {s['cards_reviewed']}",
        f"- Goed: {m.get('right', 0)} · Fout: {m.get('wrong', 0)} · Gevaarlijk: {m.get('dangerous', 0)}",
        f"- Risico terecht tegengehouden: {s['safety']['risky_cards_correctly_stopped']} · "
        f"gevaarlijk maar niet tegengehouden: {s['safety']['dangerous_cards_not_stopped']} · "
        f"onnodig tegengehouden: {s['safety']['cards_stopped_without_need']}", "",
        "## Per oordeel van Countersign", "", table(s["by_verdict"], lambda k: nl.VERDICT_KORT.get(k, k)), "",
        "## Per soort actie", "", table(s["by_action"], lambda k: nl.ACTION_KORT.get(k, k)), "",
    ]
    if len(s["by_planner"]) > 1:  # one method (the usual offline rules) needs no table of its own
        lines += ["## Per beoordelingsmethode", "", table(s["by_planner"], nl.planner), ""]
    lines += ["## Citaten (met toestemming)", ""]
    lines += [f'- ({MARK_NL[q["mark"]]}, {nl.ACTION_KORT.get(q["action"], q["action"])}) "{q["quote"]}"' for q in s["quotes"]] or ["- (geen)"]
    return "\n".join(lines) + "\n"
