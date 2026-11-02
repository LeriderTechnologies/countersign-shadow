# SPDX-License-Identifier: Apache-2.0
"""C12 (warn): a known IBAN, but the name on this message differs from the name(s) you paid on that IBAN before.
Run: python3 -m unittest discover -s tests -v"""
from __future__ import annotations

import unittest

from countersign_shadow import checks, iban as ibanlib

IBAN = ibanlib.make_nl("RABO", "0312456789")
OTHER = ibanlib.make_nl("INGB", "0007777777")


def decide(payee_name: str, paid_names: list[str], policy: checks.Policy | None = None,
           payee_iban: str = IBAN) -> tuple[str, checks.Check | None, list[checks.Check]]:
    quote = f"Betaal 120,00 naar {payee_iban} t.n.v. {payee_name or 'ons'}."
    message = {"subject": "Factuur F-77", "body": quote, "from": "info@leverancier.example",
               "from_name": "", "date": "2026-10-24", "pdfs": []}
    proposal = {"action": "pay_invoice", "amount": "120.00", "currency": "EUR",
                "payee": {"name": payee_name, "iban": payee_iban, "email": ""}, "invoice_ref": "F-77",
                "order_ref": "", "evidence_quote": quote, "rationale": ""}
    history = [{"amount": "-99.00", "currency": "EUR", "date": f"2026-0{i + 1}-01", "iban": IBAN, "counterparty": n,
                "email": "", "description": "", "reference": "", "status": ""} for i, n in enumerate(paid_names)]
    result = checks.evaluate(proposal, message, history, policy or checks.Policy())
    c12 = next((c for c in result if c.rule.startswith("C12")), None)
    return checks.verdict("pay_invoice", result), c12, result


class TestNameCheck(unittest.TestCase):
    def test_exact_match_is_ok(self):
        v, c12, _ = decide("Jansen Bouw BV", ["Jansen Bouw BV"])
        self.assertTrue(c12.ok)
        self.assertEqual(v, "ok")

    def test_near_matches_do_not_warn(self):
        for payee, paid in [("Jansen Bouw BV", "Jansen Bouw B.V."), ("Café Blauw V.O.F.", "CAFE BLAUW"),
                            ("Kartonfabriek Noord BV", "KARTONFABRIEK NOO"),  # bank-truncated counterparty
                            ("Glazuurhandel De Vries", "Glazuurhandel de Vries B.V."),
                            ("Jansen Bouw", "Jansen Bouw Rotterdam"), ("Bakkerij Smit", "Bakkerij Smitt")]:
            with self.subTest(payee=payee, paid=paid):
                v, c12, _ = decide(payee, [paid])
                self.assertTrue(c12.ok, c12.detail)
                self.assertEqual(v, "ok")

    def test_mismatch_warns_in_dutch_naming_both(self):
        v, c12, result = decide("Kartonfabriek Noord BV", ["Someone Else BV"])
        self.assertFalse(c12.ok)
        self.assertEqual(c12.severity, "warn")
        self.assertEqual(v, "warn")  # a warning, never a block on its own
        self.assertIn("Someone Else BV", c12.uitleg)
        self.assertIn("Kartonfabriek Noord BV", c12.uitleg)
        self.assertIn(IBAN, c12.uitleg)
        self.assertTrue(next(c for c in result if c.rule.startswith("C5")).ok)

    def test_same_first_word_is_still_a_mismatch(self):
        _, c12, _ = decide("Jansen Advocaten", ["Jansen Bouw BV"])
        self.assertFalse(c12.ok)

    def test_any_earlier_name_on_that_iban_counts(self):
        _, c12, _ = decide("Stroom & Co", ["Energiedirect", "Stroom en Co B.V."])
        self.assertTrue(c12.ok)

    def test_vetted_name_in_policy_counts(self):
        policy = checks.Policy(known_ibans={IBAN: "Huurbaas Vastgoed BV"})
        _, c12, _ = decide("Huurbaas Vastgoed", [], policy)
        self.assertTrue(c12.ok)
        _, c12, _ = decide("Iemand Anders", [], policy)
        self.assertFalse(c12.ok)
        self.assertIn("Huurbaas Vastgoed BV", c12.uitleg)

    def test_missing_name_does_not_warn(self):
        for payee in ("", "  ", "x", "B.V."):
            with self.subTest(payee=payee):
                v, c12, _ = decide(payee, ["Jansen Bouw BV"])
                self.assertIsNone(c12)
                self.assertEqual(v, "ok")

    def test_history_without_names_does_not_warn(self):
        _, c12, _ = decide("Jansen Bouw BV", [""])
        self.assertIsNone(c12)

    def test_new_iban_has_no_c12_and_iban_changed_is_unchanged(self):
        # reverse case: known supplier name, new IBAN -> still C5 "IBAN CHANGED" (block), no C12
        v, c12, result = decide("Jansen Bouw BV", ["Jansen Bouw BV"], payee_iban=OTHER)
        self.assertIsNone(c12)
        c5 = next(c for c in result if c.rule.startswith("C5"))
        self.assertFalse(c5.ok)
        self.assertIn("IBAN CHANGED", c5.detail)
        self.assertEqual(v, "blocked")


if __name__ == "__main__":
    unittest.main()
