# SPDX-License-Identifier: Apache-2.0
"""Regression tests: a crafted mail cannot stall Countersign Shadow with long runs.

Before: money.find_amounts took ~14 s on a 20,000-digit run (the trailing-currency amount was retried at every
position of the run); a long clause full of pay verbs ("pay pay pay ..") took ~18 s in checks.find_pay_asks (every
verb re-scanned the text from 0); the rules planner's e-mail regex took ~4.7 s on 100,000 letters; the feedback
scrub's amount pattern ~5 s on 20,000 digits. Each must now finish well under 0.5 s, and real amounts parse as
before.
"""
from __future__ import annotations

import random
import time
import unittest
from decimal import Decimal

from countersign_shadow import checks, feedback, money
from countersign_shadow.planners import rules
from tests.test_structural_pay_ask import NEW, X, Base

LIMIT = 0.5  # seconds


class _Timed(unittest.TestCase):
    def assertFast(self, fn, *args, limit=LIMIT):
        t = time.perf_counter()
        out = fn(*args)
        dt = time.perf_counter() - t
        self.assertLess(dt, limit, f"{fn.__name__} took {dt:.2f} s")
        return out


class TestFindAmountsLongRuns(_Timed):
    def test_20k_digit_run(self):
        self.assertEqual(self.assertFast(money.find_amounts, "1" * 20000), [])

    def test_other_long_runs(self):
        for text in ("1" * 20000 + " EUR", "EUR " + "1.234" * 4000, "123." * 5000, "1,234" * 4000, "1." * 10000,
                     ".1" * 10000, "1..," * 5000, ("1" * 20000 + ".") * 3, "1 " * 10000 + "EUR",
                     # trailing-€ runs must stay fast under the digit-run guard too
                     "1" * 20000 + " €", "1" * 20000 + "€", "€" * 10000 + "1" * 10000, "1€" * 10000):
            with self.subTest(text=text[:12]):
                self.assertFast(money.find_amounts, text)

    def test_fuzz_digit_separator_strings(self):
        rnd = random.Random(254)
        alphabet = "0123456789" * 4 + ".,  -€\n" + "EUReuro"
        t0 = time.perf_counter()
        for _ in range(300):
            s = "".join(rnd.choice(alphabet) for _ in range(rnd.randint(1, 3000)))
            for d, pos, raw in self.assertFast(money.find_amounts, s, limit=0.2):
                self.assertGreater(d, 0)
                self.assertEqual(s[pos:pos + len(raw)], raw)
        self.assertLess(time.perf_counter() - t0, 5.0)

    def test_real_amounts_unchanged(self):
        cases = {
            "Totaal € 1.284,00 of 38.00 EUR en 450,- euro, niet 2026": ["1284.00", "38.00", "450.00"],
            "EUR 38,00 en €38 en € 1.234,56": ["38.00", "38.00", "1234.56"],
            "1,234.56 EUR, 95,00 euro, 12.345.678,90 EUR": ["1234.56", "95.00", "12345678.90"],
            "factuur 26-1004: 1.203,95 EUR (excl. 21% btw)": ["1203.95"],
            "ca. 50 EUR, ca.50 EUR, 38.005 EUR": ["50.00", "50.00", "38005.00"],
            "Bedrag:95 eur\nTotaal:1.150,- euro": ["95.00", "1150.00"],
            # trailing € at end of line/sentence/string, and with a NBSP before it
            "Totaal: 7,50 €": ["7.50"],
            "Totaal: 7,50 €.": ["7.50"],
            "250€.": ["250.00"],
            "Totaal: 250\xa0€": ["250.00"],
        }
        for text, want in cases.items():
            with self.subTest(text=text):
                self.assertEqual([str(d) for d, *_ in money.find_amounts(text)], want)

    def test_fragment_of_a_malformed_run_is_not_an_amount(self):
        # used to yield 345678 ("345.678 EUR", from the middle of the run); "12.34.56 EUR" used to yield 34.56
        self.assertEqual(money.find_amounts("12345.678 EUR"), [])
        self.assertEqual(money.find_amounts("12.34.56 EUR"), [])
        self.assertEqual(money.find_amounts("1.234.567 EUR"), [(Decimal("1234567.00"), 0, "1.234.567 EUR")])


class TestPayVerbClauses(_Timed):
    def test_long_clause_of_pay_verbs(self):
        for body in ("pay " * 5000, f"Graag betalen: {'pay ' * 5000} op {X}, € 95,00.", f"{X} " + "betaal " * 3000):
            with self.subTest(body=body[:15]):
                self.assertFast(checks.find_pay_asks, body)
                self.assertFast(checks.find_structural_asks, body)

    def test_clause_and_first_person_unchanged(self):
        self.assertEqual(checks._clause("Ja. Wij betalen morgen; dank", 9, 15), (3, 22))
        self.assertEqual(checks._clause("geen einde", 3, 3), (0, 10))
        for body, want in ((f"Wij betalen morgen op {X}.", {}), (f"Betaal ik morgen op {X}.", {}),
                           (f"Graag betalen op {X}.", {NEW: "betalen"}),
                           (f"Ik weet het niet. Graag betalen op {X}.", {NEW: "betalen"}),
                           (f"Betaal met iDEAL via de link, of pay to {X}.", {NEW: "Betaal"})):
            with self.subTest(body=body):
                self.assertEqual(checks.find_pay_asks(body), want)
        self.assertEqual(checks.find_pay_asks("Betaal met iDEAL of contant aan de balie."), {})


class TestOtherLongRuns(_Timed):
    def test_rules_email_regex(self):
        self.assertFast(rules.EMAIL_RE.findall, "a" * 100000)
        self.assertFast(rules.EMAIL_RE.findall, "a" * 100000 + "@")
        self.assertEqual(rules._other_email("Mail jan.de-vries+f@studio.example.nl of info@x.example", "info@x.example"),
                         "jan.de-vries+f@studio.example.nl")

    def test_feedback_scrub_amounts(self):
        self.assertFast(feedback.scrub, "1" * 20000)
        self.assertFast(feedback.scrub, "1." * 10000)
        # same output as before the fix, leading separator kept
        self.assertEqual(feedback.scrub("kost .30€ of 1.234,56 eur, EUR 5"), "kost .30€ of [bedrag], [bedrag]")
        self.assertEqual(feedback.scrub("kost .30 eur, ,5€x"), "kost .[bedrag], ,[bedrag]x")


class TestEvaluateCraftedBodies(Base, _Timed):
    def test_crafted_20k_bodies(self):
        for pad in ("1" * 20000, "1." * 10000, "pay " * 5000, "a" * 20000, " " * 20000, "\n" * 20000,
                    "from " * 4000, "EUR 1 " * 3000):
            body = f"Graag betalen: {pad} op {X}, € 95,00."
            with self.subTest(pad=pad[:8]):
                self.assertFast(self.sev, body, limit=1.0)
