# SPDX-License-Identifier: Apache-2.0
"""Regression tests: a conditional "no action needed" (from a spent held-out test set).

A conditional "no action needed" ("No further action is needed if this has already been arranged", "U hoeft niets te
doen als dit al geregeld is", "Al geregeld? Dan hoeft u niets te doen") is the reminder's ask, not a no-pay context: the
never-paid block stays a block. An unconditional "no action needed" still softens it to a warn.
Out of scope on purpose: a conditional "already paid" ("If you have already paid, please disregard") still softens --
changing it turns a held-out test mail (legit reminder, known IBAN, larger amount) into a false alarm.
"""
from __future__ import annotations

import unittest

from countersign_shadow import checks
from tests.test_no_pay_context import NEW, X, run

ASK = f"Het dubbel uitgekeerde bedrag van € 95,00 gaat retour binnen 5 werkdagen naar:\n\nIBAN: {X}\nKenmerk: RF-1\n\n"
ASK_EN = f"The amount of EUR 95.00 is to be returned within 5 working days to:\n\nIBAN: {X}\nReference: RF-1"


class TestConditionalNoAction(unittest.TestCase):
    def test_conditional_no_action_keeps_the_block(self):
        for body in (ASK_EN + "\n\nNo further action is needed if this has already been arranged.",
                     ASK_EN + "\n\nNo action is required unless you have not paid yet.",
                     ASK + " U hoeft niets te doen als dit al geregeld is.",
                     ASK + " Al geregeld? Dan hoeft u niets te doen.",
                     ASK + " Indien dit reeds geregeld is, hoeft u niets te doen."):
            with self.subTest(body=body):
                v, res = run(body)
                self.assertEqual(v, "blocked", res)
                self.assertIsNone(checks.no_pay_reason(body, NEW, "no_action"))

    def test_unconditional_no_action_still_softens(self):
        for body in (ASK_EN + "\n\nNo further action is needed.", ASK + " U hoeft niets te doen."):
            with self.subTest(body=body):
                self.assertEqual(checks.no_pay_reason(body, NEW, "no_action"), "the message says no action is needed")
                v, res = run(body)
                self.assertEqual(v, "warn", res)

    def test_a_condition_in_another_sentence_does_not_count(self):
        body = ASK + " U hoeft niets te doen. Als u vragen heeft, bel ons."
        self.assertEqual(checks.no_pay_reason(body, NEW, "no_action"), "the message says no action is needed")


if __name__ == "__main__":
    unittest.main()
