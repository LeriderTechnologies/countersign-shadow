# SPDX-License-Identifier: Apache-2.0
"""Regression tests: the donation cap leaked. Short unit-test strings only; no blind set was read.

- F1: DONATION_PLAIN_CAP (EUR 250) holds for a gift-noun amount too: "uw gift van € 9.800,00 graag vandaag overmaken
  op X" (CEO) blocks again; "uw gift / donatie van € 50,00" from the charity still warns.
- F2: the cap comes before the leave-it-to-you exit in a donation-worded message: "If you wish, you can make the
  donation of EUR 9,800.00 to X" blocks; an optional-only "if you prefer .. you can pay" keeps its exit.
- F3: a per-person / per-item amount ("€ 250,00 per medewerker", "EUR 200.00 each") counts as above the cap; a
  per-period one ("€ 5,00 per maand") does not.
"""
from __future__ import annotations

import unittest

from countersign_shadow import checks

try:
    from tests.test_heldout_regressions import NEW, failed, run
except ImportError:  # python3 -m unittest discover -s tests
    from test_heldout_regressions import NEW, failed, run

CEO = dict(sender="ceo.janssen@gmail.com", name="Peter Janssen")
CHARITY = dict(sender="info@stichtinggroen.example", name="Stichting Groen")


def B(t):
    return "Beste,\n\n" + t + "\n\nGroet,\nPeter"


def verdict(body, **kw):
    return run(body=body, **kw)[1]


class GiftNounAmountCap(unittest.TestCase):  # F1
    def test_big_gift_noun_asks_block(self):  # a QA probe (5 Dutch variants) + the English hole
        for body in (B(f"Uw gift van € 9.800,00 graag vandaag overmaken op {NEW}."),
                     B(f"U kunt uw gift van € 9.800,00 overmaken op {NEW}."),
                     B(f"Uw gift van € 9.800,00 kunt u vandaag overmaken op {NEW}. De directie rekent op u."),
                     B(f"De gift van € 9.800,00 graag vandaag overmaken op {NEW}."),
                     B(f"Maak uw gift van € 9.800,00 vandaag over op {NEW}."),
                     B(f"Please transfer your donation of EUR 9,800.00 to {NEW} today."),
                     B(f"Make your donation of EUR 9,800.00 to {NEW} today."),
                     B(f"Send your donation of EUR 9,800.00 to {NEW} today.")):
            with self.subTest(body=body[8:70]):
                self.assertEqual(verdict(body, **CEO), "blocked")

    def test_c5_blocks_on_pay_invoice_draft(self):
        p, v, res = run(body=B(f"Uw gift van € 9.800,00 graag vandaag overmaken op {NEW}."), **CEO)
        self.assertEqual((p["action"], v, failed(res).get("C5")), ("pay_invoice", "blocked", "block"))

    def test_charity_sender_does_not_buy_a_big_gift(self):
        body = B(f"Uw gift van € 4.750,00 graag vandaag overmaken naar {NEW}. Crowdfunding voor ons goede doel.")
        self.assertEqual(verdict(body, **CHARITY), "blocked")

    def test_small_gift_appeals_still_warn(self):  # and the English "your donation" at EUR 50
        for body in (B(f"U kunt uw gift van € 50,00 overmaken op {NEW} t.n.v. Stichting Groen."),
                     B(f"Uw donatie van € 50,00 kunt u overmaken op {NEW} t.n.v. Stichting Groen."),
                     B(f"Please transfer your donation of EUR 50.00 to {NEW}. Thank you!"),
                     B(f"Make your donation of EUR 25.00 to {NEW}. Thank you for your support.")):
            with self.subTest(body=body[8:70]):
                self.assertEqual(verdict(body, **CHARITY), "warn")


class CapBeforeLeaveItToYou(unittest.TestCase):  # F2
    def test_big_leave_it_to_you_asks_block(self):
        for body, kw in ((B(f"If you wish, you can make the donation of EUR 9,800.00 to {NEW} today. Thanks for "
                            "helping out."), CEO),
                         (B(f"Wie wil, kan € 4.750,00 doneren op {NEW} voor het goede doel."), CEO),
                         # The earlier leave-it-to-you appeals above the cap: same shape, so they block now too
                         (B(f"If you wish to keep supporting our shelter, please donate EUR 500.00 to {NEW}."), CHARITY),
                         (B(f"If you like, you can donate EUR 400.00 to {NEW}. Let us know if you have questions."),
                          CHARITY)):
            with self.subTest(body=body[8:70]):
                self.assertEqual(verdict(body, **kw), "blocked")

    def test_small_leave_it_to_you_asks_warn(self):
        for body in (B(f"If you wish, you can make the donation of EUR 98.00 to {NEW} today. Thanks for helping out."),
                     B(f"Wie wil, kan € 25,00 overmaken op {NEW} voor het goede doel.")):
            with self.subTest(body=body[8:70]):
                self.assertEqual(verdict(body, **CHARITY), "warn")

    def test_optional_only_offer_keeps_its_exit(self):  # no donation word: as before (test_no_pay_context)
        body = B(f"If you prefer to pay yearly you can pay EUR 1,009.20 to {NEW}. This is optional.")
        self.assertEqual(verdict(body, **CHARITY), "warn")


class PerUnitAmounts(unittest.TestCase):  # F3
    def test_per_unit_asks_block(self):
        for body in (B(f"Doneer € 250,00 per medewerker (12 medewerkers) op {NEW}."),
                     B(f"Please donate EUR 200.00 each to {NEW} today for our charity partner."),
                     B(f"Doneer € 100,00 per persoon op {NEW} voor het goede doel.")):
            with self.subTest(body=body[8:70]):
                self.assertEqual(verdict(body, **CEO), "blocked")

    def test_per_period_appeals_warn(self):
        for body in (B(f"Word donateur: doneer € 5,00 per maand op {NEW}."),
                     B(f"Please donate EUR 10.00 a month to {NEW}. Thank you for your support.")):
            with self.subTest(body=body[8:70]):
                self.assertEqual(verdict(body, **CHARITY), "warn")

    def test_per_unit_helper(self):
        for t, want in (("€ 250,00 per medewerker", True), ("EUR 200.00 each", True), ("€ 10,00 p.p.", True),
                        ("EUR 20.00 for every employee", True), ("EUR 10.00 a person", True),
                        ("€ 5,00 per maand", False), ("EUR 10.00 a month", False), ("EUR 20.00 every year", False),
                        ("€ 25,00 op X", False)):
            end = t.index(".00") + 3 if ".00" in t else t.index(",00") + 3
            self.assertEqual(checks._per_unit(t, end), want, t)

    def test_cap_edge(self):  # the cap itself is still an appeal
        self.assertEqual(verdict(B(f"Doneer € 250,00 op {NEW}."), **CHARITY), "warn")
        self.assertEqual(verdict(B(f"Doneer € 250,01 op {NEW}."), **CHARITY), "blocked")


if __name__ == "__main__":
    unittest.main()
