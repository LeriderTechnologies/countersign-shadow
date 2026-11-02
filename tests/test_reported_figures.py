# SPDX-License-Identifier: Apache-2.0
"""Regression tests.
the planner's amount picker never takes a labelled business figure (omzet / turnover / revenue / voorbelasting /
input VAT) as the pay amount (a held-out test mail "omzet € 48.600").
a reported total next to an IBAN ("doel van 12.500,00 gehaald", "we raised EUR 12,500.00") is no pay amount for
the planner and no C14 structural anchor (a QA probe crowdfunding false block).
a mail announcing a FUTURE invoice is a no-pay reason ("invoice comes later").
"""
from __future__ import annotations

import importlib.util
import unittest
from decimal import Decimal
from pathlib import Path

from countersign_shadow import checks, iban as ibanlib
from countersign_shadow.importers import transactions as tx
from countersign_shadow.money import find_amounts, is_reported_figure
from countersign_shadow.planners.rules import RulePlanner, _main_amount

OWN = ibanlib.make_nl("OWNB", "0412776350")
KNOWN = ibanlib.make_nl("KNWN", "0204918372")
NEW = ibanlib.make_nl("NEWB", "0381726354")


def history():
    rows = [tx._tx("ing", d, Decimal("-250.00"), "EUR", "DE RUITER BOEKHOUDING", KNOWN, description="Betaling",
                   own_iban=OWN) for d in ("2026-08-01", "2026-09-01", "2026-10-01")]
    rows.append(tx._tx("ing", "2026-10-02", Decimal("-1.00"), "EUR", "Bankkosten", "", own_iban=OWN))
    return rows


def run(body, sender="info@deruiterboekhouding.example", name="De Ruiter Boekhouding"):
    msg = {"id": "q", "subject": "Bericht", "from": sender, "from_name": name, "date": "2026-11-01", "body": body}
    p = RulePlanner().plan_one(msg)
    res = checks.evaluate(p, msg, history(), checks.Policy())
    return p, checks.verdict(p["action"], res), res


def flags(text):
    return [is_reported_figure(text, p, p + len(raw)) for _d, p, raw in find_amounts(text)]


class ReportedFigures(unittest.TestCase):
    def test_labelled_business_figures(self):
        self.assertEqual(flags("(omzet € 48.600, voorbelasting € 6.120)"), [True, True])
        self.assertEqual(flags("Turnover Q3: EUR 120,000.00, input VAT EUR 4,300.00"), [True, True])
        self.assertEqual(flags("Total revenue was EUR 9,000.00."), [True])
        self.assertEqual(flags("Netto-omzet over het kwartaal: € 31.200,00"), [True])

    def test_charges_are_not_reported_figures(self):
        for t in ("Te betalen btw € 2.140", "Omzetbelasting € 2.140 te betalen", "Revenue share fee: € 120,00",
                  "Totaal: € 312,40", "Amount due: EUR 1,250.00", "Omzet te betalen: € 5,00"):
            with self.subTest(t=t):
                self.assertEqual(flags(t), [False])

    def test_reported_totals(self):
        self.assertEqual(flags("Ons doel van € 12.500,00 is gehaald!"), [True])
        self.assertEqual(flags("Er is € 3.400 opgehaald voor de club."), [True])
        self.assertEqual(flags("We raised EUR 12,500.00 - goal reached!"), [True])
        self.assertEqual(flags("Bedrag: € 95,00. Het doel is bijna bereikt."), [False])  # other sentence


class AmountPicker(unittest.TestCase):
    def test_nl_omzet_is_never_the_pay_amount(self):
        t = "Voorlopige cijfers: omzet € 48.600, voorbelasting € 6.120. Te betalen voor de begeleiding: € 360,00."
        self.assertEqual(_main_amount(t)[0], Decimal("360.00"))
        self.assertIsNone(_main_amount("De cijfers (omzet € 48.600, voorbelasting € 6.120) staan klaar."))

    def test_en_turnover_is_never_the_pay_amount(self):
        t = "Your Q3 turnover EUR 120,000.00 is booked. Amount due for our services: EUR 450.00."
        self.assertEqual(_main_amount(t)[0], Decimal("450.00"))
        self.assertIsNone(_main_amount("Revenue: 48.600,00\nInput VAT: 6.120,00"))

    def test_m1137_l03_shape_is_silent(self):
        p, verdict, _res = run(
            "Beste klant,\n\nDe Belastingdienst verwacht de BTW-aangifte uiterlijk 31 januari 2027. Wij hebben de "
            "voorlopige cijfers (omzet € 48.600, voorbelasting € 6.120) klaarstaan voor controle.\n\nOns kantoor "
            "factureert de begeleiding zoals gebruikelijk aan het einde van het kwartaal, rekeningnummer "
            f"{KNOWN} zoals u van ons kent.\n\nMet vriendelijke groet,\nDe Ruiter")
        self.assertEqual(p["action"], "no_action")
        self.assertNotIn(verdict, ("blocked", "flagged"))

    def test_en_turnover_report_is_not_a_blocked_pay(self):
        p, verdict, _res = run(f"Hi,\n\nYour Q3 figures: turnover EUR 120,000.00, input VAT EUR 4,300.00. Please "
                               f"review the draft before Friday. Our account {KNOWN} as usual.\n\nRegards")
        self.assertNotEqual(p["amount"], "120000.00")
        self.assertNotIn(verdict, ("blocked", "flagged"))

    def test_real_invoice_still_pays(self):
        p, verdict, _res = run(f"Factuur 2026-118\n\nTe betalen: € 240,00 op {KNOWN} binnen 14 dagen.")
        self.assertEqual((p["action"], p["amount"]), ("pay_invoice", "240.00"))
        self.assertEqual(verdict, "ok")


class ReportedTotalNextToIban(unittest.TestCase):
    def test_nl_crowdfunding_goal_not_blocked(self):
        b = f"Beste buren,\n\nOns doel van 12.500,00 is gehaald! Rekening {NEW} blijft nog open tot 30 november.\n\nComité"
        self.assertEqual(checks.find_structural_asks(b), {})
        _p, verdict, _res = run(b, sender="info@buurtfonds.example", name="Buurtfonds")
        self.assertNotIn(verdict, ("blocked", "flagged"))

    def test_en_crowdfunding_goal_not_blocked(self):
        b = f"Dear backers,\n\nWe raised EUR 12,500.00 - goal reached! Account {NEW} stays open until 30 November.\n\nTeam"
        self.assertEqual(checks.find_structural_asks(b), {})
        _p, verdict, _res = run(b, sender="info@buurtfonds.example", name="Buurtfonds")
        self.assertNotIn(verdict, ("blocked", "flagged"))

    def test_pay_ask_after_goal_still_blocks(self):
        _p, verdict, _res = run(f"Goal reached! Please pay the remaining EUR 2,400.00 to our new account {NEW} today.",
                                sender="info@buurtfonds.example", name="Buurtfonds")
        self.assertEqual(verdict, "blocked")
        b = f"Doel gehaald! Rekening {NEW}, bedrag € 75,00, graag voor vrijdag."
        self.assertIn(NEW, checks.find_structural_asks(b))


class InvoiceComesLater(unittest.TestCase):
    def test_reason_nl_en(self):
        for t in (f"Kosten begeleiding € 360,00. De factuur volgt aan het einde van het kwartaal; rekening {NEW}.",
                  f"Ons kantoor factureert dit zoals gebruikelijk aan het einde van het kwartaal (€ 360,00), {NEW}.",
                  f"Estimated cost EUR 360.00. The invoice will follow; our account is {NEW}.",
                  f"Cost EUR 360.00. You will be invoiced at the end of the quarter, account {NEW}."):
            with self.subTest(t=t):
                self.assertEqual(checks.no_pay_reason(t, NEW), "the message announces an invoice that comes later")

    def test_planner_drafts_no_action_with_warning(self):
        body = f"Kosten begeleiding € 360,00. De factuur volgt aan het einde van het kwartaal; rekening {NEW}."
        p, verdict, _res = run(body, sender="info@nieuwkantoor.example", name="Nieuw Kantoor")
        self.assertEqual(p["action"], "no_action")
        self.assertIn("factuur aan die later komt", p["rationale"])
        self.assertNotIn(verdict, ("blocked", "flagged"))
        # the same mail from a supplier you paid on another IBAN: still IBAN CHANGED -> blocked
        _p, verdict, _res = run(body)
        self.assertEqual(verdict, "blocked")

    def test_not_a_reason_with_pay_ask_redirect_or_deposit(self):
        for t in (f"The invoice will follow. Please pay EUR 500.00 to {NEW} today.",
                  f"De factuur volgt later. Voortaan betalen op {NEW}: € 360,00.",
                  f"De factuur volgt later; de aanbetaling van € 360,00 staat open op {NEW}."):
            with self.subTest(t=t):
                self.assertNotEqual(checks.no_pay_reason(t, NEW), "the message announces an invoice that comes later")




if __name__ == "__main__":
    unittest.main()
