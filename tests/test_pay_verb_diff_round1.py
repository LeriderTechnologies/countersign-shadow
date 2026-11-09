# SPDX-License-Identifier: Apache-2.0
"""planners.rules.P_PAY_CONTEXT diffed against checks._PAY_VERB.

Same gap as an earlier "betalen" fix: a message with an amount + a known IBAN, but no factuur/invoice/betaal/
pay/payment word -- only a pay verb that checks._PAY_VERB already reads (storten, voldoen, transfer, settle) -- did
not make P_PAY_CONTEXT match, so the planner drafted "no_action" (final else branch) and the full C1-C15 pipeline
never ran: the card silently said "none" instead of warning (same shape as a held-out test set's N1e "betalen" miss).

Each added verb: one probe that was none -> now warn/blocks, one legit in-limit use of the same verb that stays
"ok" (not newly blocked), and a check that the existing passive-participle shape ("kan worden gestort",
tests/test_heldout_regressions.py F11PassiveAskNameBlock, which must stay off this simple gate and on checks.py's own structural
C14 detection) is untouched: \\b...\\b on each new word so bare "stort" does not match inside "gestort".
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
    jump amount (3,450 vs. a largest earlier payment of 815), the same shape an earlier fix used for "betalen"."""

    PROBES = {
        "storten": f"Totaal € 3.450,00. Geen aanbetaling nodig, maar wel graag vandaag storten.\n\nIBAN {KNOWN}",
        "voldoen": f"Totaal € 3.450,00. Geen aanbetaling nodig, maar wel graag vandaag voldoen.\n\nIBAN {KNOWN}",
        "transfer": f"Total EUR 3,450.00. No deposit needed, but please transfer today.\n\nIBAN {KNOWN}",
        "settle": f"Total EUR 3,450.00. No deposit needed, but please settle today.\n\nIBAN {KNOWN}",
    }

    def test_none_of_the_probes_hold_an_older_pay_context_word(self):
        old_without_new = re.compile(r"(?i)factuur|invoice|herinnering|aanmaning|nota|te betalen|betaal|betalen|"
                                      r"overmaken|pay\b|payment")
        for verb, body in self.PROBES.items():
            with self.subTest(verb=verb):
                self.assertIsNone(old_without_new.search(body))

    def test_probes_were_silent_now_warn(self):
        for verb, body in self.PROBES.items():
            with self.subTest(verb=verb):
                action, verdict = run(body)
                self.assertEqual(action, "pay_invoice")
                self.assertEqual(verdict, "warn")

    def test_legit_in_limit_use_of_each_verb_stays_ok(self):
        legit = {
            "storten": f"Totaal € 780,00. Graag deze keer storten op {KNOWN} t.n.v. Drukkerij Bladwijzer, "
                       "o.v.v. 2026-0877.",
            "voldoen": f"Totaal € 780,00. Graag voldoen op {KNOWN} t.n.v. Drukkerij Bladwijzer, o.v.v. 2026-0877.",
            "transfer": f"Total EUR 780.00. Please transfer to {KNOWN}, ref 2026-0877.",
            "settle": f"Total EUR 780.00. Please settle via {KNOWN}, ref 2026-0877.",
        }
        for verb, body in legit.items():
            with self.subTest(verb=verb):
                action, verdict = run(body)
                self.assertEqual(action, "pay_invoice")
                self.assertEqual(verdict, "ok")


class GestortParticipleStaysOffThisGate(unittest.TestCase):
    """a held-out test mail (tests/test_heldout_regressions.py F11PassiveAskNameBlock): "Het bedrag kan .. worden gestort" is a
    passive-modal construction that checks.py reads via its own structural C14 detection, with the KNOWN IBAN
    correctly left unblocked. P_PAY_CONTEXT's bare "storten"/"stort" must stay word-boundaried so they do not match
    the "gestort" substring and pull this shape onto the simple pay_invoice gate (which would false-block it: this
    regressed in a first draft of this fix -- test_f11_shape_blocks / test_same_shape_to_the_known_iban_is_not_blocked
    in tests/test_heldout_regressions.py)."""

    BODY = ("De Belastingdienst heeft een naheffing opgelegd. Om een boete te voorkomen moet de naheffing van "
            "€ 2.684,00 uiterlijk vrijdag binnen zijn.\n\nWij nemen de afdracht zoals altijd voor u waar. Het "
            "bedrag kan op onze tussenrekening worden gestort, waarna wij het direct doorboeken naar de "
            f"Belastingdienst:\n\nTussenrekening Dekker Advies\n{KNOWN}\nKenmerk: OB-Q2-NH-0193\n\nMet vriendelijke "
            "groet,\nFrank Dekker")

    def test_gestort_does_not_match_p_pay_context(self):
        self.assertIsNone(P_PAY_CONTEXT.search("worden gestort"))
        self.assertIsNone(P_PAY_CONTEXT.search("Het bedrag is gestort."))

    def test_planner_action_and_verdict_unchanged(self):
        action, verdict = run(self.BODY)
        self.assertEqual(action, "no_action")
        self.assertEqual(verdict, "none")

    def test_bare_imperative_storten_forms_still_match(self):
        self.assertIsNotNone(P_PAY_CONTEXT.search("Stort het bedrag op de rekening."))
        self.assertIsNotNone(P_PAY_CONTEXT.search("U kunt dit storten op IBAN X."))


if __name__ == "__main__":
    unittest.main()
