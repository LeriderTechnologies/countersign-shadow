# SPDX-License-Identifier: Apache-2.0
"""Regression tests: the RulePlanner drafts no_action (with a warning in the rationale) instead of
pay_invoice when the message names an IBAN and an amount but asks nothing to be paid now (checks.no_pay_reason):
a receipt / payment confirmation, a remittance advice, a quote (invoice comes later), and a statement whose IBAN
sits only in the footer while the money comes back to you. Tuned on a spent held-out test set.
Guards: a plain invoice, an optional / donation ask, and a supplier's "new remittance account" stay pay drafts.
"""
from __future__ import annotations

import unittest
from decimal import Decimal

from countersign_shadow import checks, iban as ibanlib
from countersign_shadow.importers import transactions as tx
from countersign_shadow.planners.rules import RulePlanner

OWN = ibanlib.make_nl("OWNB", "0412776350")
KNOWN = ibanlib.make_nl("KNWN", "0204918372")
NEW = ibanlib.make_nl("NEWB", "0108293745")
GB_NEW = "GB54XMPL20000312345678"


def history():
    rows = [tx._tx("ing", d, Decimal("-250.00"), "EUR", "NORDIC PACKAGING LTD", KNOWN, description="Betaling",
                   own_iban=OWN) for d in ("2026-08-01", "2026-09-01", "2026-10-01")]
    rows.append(tx._tx("ing", "2026-10-02", Decimal("-1.00"), "EUR", "Bankkosten", "", own_iban=OWN))
    return rows


def plan(body, subject="Bericht", sender="info@nieuw.example", name=""):
    msg = {"id": "q", "subject": subject, "from": sender, "from_name": name, "date": "2026-11-01", "body": body}
    return msg, RulePlanner().plan_one(msg)


def card(msg, p, hist=None):
    res = checks.evaluate(p, msg, history() if hist is None else hist, checks.Policy())
    return checks.verdict(p["action"], res), res


SHAPES = {
    "receipt / payment received": (
        "Payment received - order 7731",
        f"Good news: we received EUR 1,250.00 from {NEW} (Hendriks Interieur) for order 7731 on 28 October. "
        "The order is now marked as paid and can be shipped."),
    "remittance advice": (
        "Remittance advice - invoice 2026-044",
        f"Please be advised that we have transferred EUR 2,150.00 to your account {OWN} in settlement of invoice "
        f"2026-044.\n\nPaid from: Harbourline Retail Ltd, {GB_NEW}\nValue date: 30 October 2026"),
    "quote": (
        "Offerte verbouwing kantoor",
        "Hierbij de offerte voor de verbouwing.\n\nTimmerwerk: € 2.150,00\nTotaal: € 3.480,00 excl. btw\n\n"
        "Deze offerte is 30 dagen geldig. Bij akkoord plannen we de werkzaamheden in; na oplevering ontvangt u de "
        f"factuur, te voldoen binnen 14 dagen op {NEW}."),
    "footer IBAN, money coming back": (
        "Afrekening servicekosten 2025",
        "Hierbij de afrekening servicekosten 2025:\n\nBetaald voorschot: € 1.200,00\nWerkelijke kosten: € 1.050,00\n"
        f"Tegoed: € 150,00\n\nHet tegoed wordt binnen 4 weken teruggestort op uw rekening {OWN}.\n\n"
        f"VVE Havenkwartier | IBAN {NEW} | KvK 40112233"),
}


class PlannerNoPay(unittest.TestCase):
    def test_four_shapes_plan_no_action_with_warning(self):
        for shape, (subject, body) in SHAPES.items():
            with self.subTest(shape=shape):
                msg, p = plan(body, subject)
                self.assertEqual(p["action"], "no_action")
                self.assertIsNone(p["amount"])
                self.assertIn("Geen betaalverzoek", p["rationale"])
                self.assertIn("controleren", p["rationale"])
                verdict, _res = card(msg, p)
                self.assertNotIn(verdict, ("blocked", "flagged"))

    def test_plain_invoice_still_pay_invoice(self):
        msg, p = plan(f"Factuur 2026-118: totaal € 450,00, graag binnen 14 dagen overmaken op {NEW}.", "Factuur")
        self.assertEqual(p["action"], "pay_invoice")
        self.assertEqual(p["payee"]["iban"], NEW)
        verdict, _ = card(msg, p)
        self.assertEqual(verdict, "blocked")  # never paid before: C5

    def test_invoice_with_footer_iban_only_still_pay_invoice(self):
        msg, p = plan("Factuur 2026-119\n\nWebhosting november: € 39,00\nTotaal te betalen: € 39,00\n\n"
                      f"Hostbedrijf B.V. | IBAN {NEW} | KvK 12345678", "Factuur")
        self.assertEqual(p["action"], "pay_invoice")

    def test_optional_and_donation_keep_pay_draft(self):
        for body in (f"Doneer vandaag: met € 25,00 betaal je een week vis. Overmaken op {NEW}. Giften zijn aftrekbaar (ANBI).",
                     f"De vrijwillige bijdrage van € 40,00 kunt u overmaken op {NEW}. Er is geen verplichting."):
            with self.subTest(body=body):
                _msg, p = plan(body)
                self.assertEqual(p["action"], "pay_invoice")

    def test_new_remittance_account_is_not_incoming(self):
        body = ("Kindly note our new remittance account below, which replaces all previous instructions.\n\n"
                f"IBAN: {GB_NEW}\n\nOutstanding: EUR 3,960.00 (invoices NP-4471, NP-4502)")
        self.assertIsNone(checks.no_pay_reason(body, GB_NEW, "pay_invoice"))
        msg, p = plan(body, "Statement", sender="ar@nordicpackaging.example", name="Nordic Packaging")
        self.assertEqual(p["action"], "pay_invoice")
        verdict, _ = card(msg, p)
        self.assertEqual(verdict, "blocked")
        # a remittance ADVICE is still incoming money
        self.assertIsNotNone(checks.no_pay_reason("Remittance advice: EUR 100.00 for invoice 7.", NEW))

    def test_no_action_draft_still_blocks_iban_changed(self):
        """a quote from a known supplier that names a new IBAN is planned no_action (the invoice
        comes later), and C14 still blocks the card with IBAN CHANGED. Before, this test's message was planned
        pay_invoice, so the no_action path was not exercised."""
        msg = {"id": "q", "subject": "Offerte verpakkingen", "from": "ar@nordicpackaging.example",
               "from_name": "Nordic Packaging", "date": "2026-11-01",
               "body": "Hierbij de offerte voor de verpakkingen.\n\nTotaal € 3.480,00 excl. btw\n\nDeze offerte is "
                       "30 dagen geldig. Na levering ontvangt u de factuur, te voldoen binnen 14 dagen op "
                       f"{GB_NEW}."}
        p = RulePlanner().plan_one(msg)
        self.assertEqual(p["action"], "no_action")
        verdict, res = card(msg, p)
        self.assertEqual(verdict, "blocked")
        self.assertTrue(any(not c.ok and c.severity == "block" and "IBAN CHANGED" in c.detail for c in res))

if __name__ == "__main__":
    unittest.main()
