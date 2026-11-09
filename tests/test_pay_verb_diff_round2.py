# SPDX-License-Identifier: Apache-2.0
"""round 2 of planners.rules.P_PAY_CONTEXT diffed against checks._PAY_VERB.

Round 1 (tests/test_pay_verb_diff_round1.py) added storten/voldoen/transfer/settle. Still missing per its own
sidetrack: wire, remit, deposit (EN) and overboeken, overschrijven (NL) -- all four already read by
checks._PAY_VERB, so a message with an amount + a known IBAN but no factuur/invoice/betaal/pay/payment word,
only one of these verbs, stayed "no_action" (final else branch; full C1-C15 pipeline never ran) instead of
warning.

Each added verb: one probe that was none -> now warn/blocks, one legit in-limit use that stays "ok" (not newly
blocked), and -- same discipline as round 1's "gestort" check -- a check that noun/participle forms that share a
prefix with the new bare words do not collide: \\b...\\b on each new word so "deposit" does not match
"depositogarantie" and "overboeken"/"overschrijven" do not match the noun forms "overboeking(en)"/
"overschrijving(en)" (read separately by checks._PAY_NOUN_RE, not this gate).
"""
from __future__ import annotations

import re
import unittest
from decimal import Decimal

from countersign_shadow import checks, iban as ibanlib
from countersign_shadow.importers import transactions as tx
from countersign_shadow.planners.rules import P_PAY_CONTEXT, RulePlanner

OWN = ibanlib.make_nl("OWNB", "0412776350")
KNOWN = ibanlib.make_nl("KNWN", "0204918372")


def history():
    rows = [tx._tx("ing", f"2026-{m:02d}-01", -Decimal(a), "EUR", "Drukkerij Bladwijzer", KNOWN,
                    description="Betaling", own_iban=OWN)
            for m, a in zip(range(7, 11), ("640.00", "890.00", "720.50", "815.00"))]
    rows.append(tx._tx("ing", "2026-10-02", Decimal("-1.00"), "EUR", "Bankkosten", "", own_iban=OWN))
    return rows


def run(body, sender="orders@drukkerijbladwijzer.example", name="Drukkerij Bladwijzer"):
    msg = {"id": "q", "subject": "Bericht", "from": sender, "from_name": name, "date": "2026-11-01", "body": body}
    p = RulePlanner().plan_one(msg)
    res = checks.evaluate(p, msg, history(), checks.Policy())
    return p["action"], checks.verdict(p["action"], res)


class AddedVerbsWereSilent(unittest.TestCase):
    """Each probe has no factuur/invoice/betaal(en)/overmaken/pay/payment word -- only the new verb -- and a
    jump amount (3,450 vs. a largest earlier payment of 815), the same shape earlier fixes used."""

    PROBES = {
        "wire": f"Total EUR 3,450.00. No deposit needed, but please wire today.\n\nIBAN {KNOWN}",
        "remit": f"Total EUR 3,450.00. No deposit needed, but please remit today.\n\nIBAN {KNOWN}",
        "deposit": f"Total EUR 3,450.00. No deposit needed, but please deposit today.\n\nIBAN {KNOWN}",
        "overboeken": f"Totaal € 3.450,00. Geen aanbetaling nodig, maar wel graag vandaag overboeken.\n\nIBAN {KNOWN}",
        "overschrijven": f"Totaal € 3.450,00. Geen aanbetaling nodig, maar wel graag vandaag overschrijven.\n\n"
                          f"IBAN {KNOWN}",
    }

    def test_none_of_the_probes_hold_an_older_pay_context_word(self):
        old_without_new = re.compile(r"(?i)factuur|invoice|herinnering|aanmaning|nota|te betalen|betaal|betalen|"
                                      r"overmaken|\bstorten\b|\bstort\b|\bvoldoen\b|\bvoldoe\b|\btransfer\b|"
                                      r"\bsettle\b|pay\b|payment")
        for verb, body in self.PROBES.items():
            with self.subTest(verb=verb):
                self.assertIsNone(old_without_new.search(body))

    def test_probes_were_silent_on_base_tree(self):
        """Confirms the gap directly on P_PAY_CONTEXT before relying on the planner run below."""
        old_without_new = re.compile(r"(?i)factuur|invoice|herinnering|aanmaning|nota|te betalen|betaal|betalen|"
                                      r"overmaken|\bstorten\b|\bstort\b|\bvoldoen\b|\bvoldoe\b|\btransfer\b|"
                                      r"\bsettle\b|pay\b|payment")
        for verb, body in self.PROBES.items():
            with self.subTest(verb=verb):
                self.assertIsNone(old_without_new.search(body))
                self.assertIsNotNone(P_PAY_CONTEXT.search(body))  # the new word itself now matches

    def test_probes_were_silent_now_warn(self):
        for verb, body in self.PROBES.items():
            with self.subTest(verb=verb):
                action, verdict = run(body)
                self.assertEqual(action, "pay_invoice")
                self.assertEqual(verdict, "warn")

    def test_legit_in_limit_use_of_each_verb_stays_ok(self):
        legit = {
            "wire": f"Total EUR 780.00. Please wire to {KNOWN}, ref 2026-0877.",
            "remit": f"Total EUR 780.00. Please remit to {KNOWN}, ref 2026-0877.",
            "deposit": f"Total EUR 780.00. Please deposit to {KNOWN}, ref 2026-0877.",
            "overboeken": f"Totaal € 780,00. Graag deze keer overboeken naar {KNOWN} t.n.v. Drukkerij Bladwijzer, "
                          "o.v.v. 2026-0877.",
            "overschrijven": f"Totaal € 780,00. Graag deze keer overschrijven naar {KNOWN} t.n.v. Drukkerij "
                              "Bladwijzer, o.v.v. 2026-0877.",
        }
        for verb, body in legit.items():
            with self.subTest(verb=verb):
                action, verdict = run(body)
                self.assertEqual(action, "pay_invoice")
                self.assertEqual(verdict, "ok")


class NounAndParticipleFormsStayOffThisGate(unittest.TestCase):
    """Noun/participle forms that share a prefix with the new bare words must not pull unrelated messages onto
    this gate: "depositogarantie" (bank jargon, not a pay ask), "overboeking(en)"/"overschrijving(en)" (the
    NOUN, already read separately by checks._PAY_NOUN_RE, not this VERB gate), "wired"/"remittance" (inflected/
    noun forms of the EN verbs, not the bare imperative/infinitive)."""

    def test_unrelated_forms_do_not_match(self):
        for text in (
            "Onder het Nederlandse depositogarantiestelsel is uw tegoed verzekerd.",
            "Bijgaand de overboeking van vorige week.",
            "Zie de overschrijving die wij vandaag ontvingen.",
            "The funds were wired last Tuesday.",
            "Please confirm the remittance we sent.",
        ):
            with self.subTest(text=text):
                self.assertIsNone(P_PAY_CONTEXT.search(text))

    def test_bare_forms_still_match(self):
        for text in (
            "Wire the amount today.",
            "Please remit the balance.",
            "Please deposit the balance.",
            "Wil je dit overboeken naar de nieuwe rekening?",
            "Kun je dit overschrijven naar de nieuwe rekening?",
        ):
            with self.subTest(text=text):
                self.assertIsNotNone(P_PAY_CONTEXT.search(text))


if __name__ == "__main__":
    unittest.main()
