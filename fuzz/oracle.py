# SPDX-License-Identifier: Apache-2.0
"""Round-trip oracle: generated VALID file's ground truth vs. what the importer actually parsed back."""
from __future__ import annotations

# Formats whose importer never carries an IBAN at all (by design -- not a bug to flag).
NO_IBAN_FORMATS = {"paypal"}


def compare(fmt: str, parsed: list[dict], truth: list[dict]) -> list[str]:
    problems = []
    if len(parsed) != len(truth):
        return [f"expected {len(truth)} transactions, got {len(parsed)}"]
    for i, (p, t) in enumerate(zip(parsed, truth)):
        if p.get("date") != t["date"]:
            problems.append(f"row {i}: date {p.get('date')!r} != expected {t['date']!r}")
        if p.get("amount") != t["amount"]:
            problems.append(f"row {i}: amount {p.get('amount')!r} != expected {t['amount']!r}")
        if fmt not in NO_IBAN_FORMATS and p.get("iban") != t["iban"]:
            problems.append(f"row {i}: iban {p.get('iban')!r} != expected {t['iban']!r}")
        if t.get("email") and p.get("email") != t["email"].lower():
            problems.append(f"row {i}: email {p.get('email')!r} != expected {t['email'].lower()!r}")
        if not t.get("email") and p.get("counterparty") != t["counterparty"]:
            problems.append(f"row {i}: counterparty {p.get('counterparty')!r} != expected {t['counterparty']!r}")
    return problems
