# SPDX-License-Identifier: Apache-2.0
"""Regression tests.
BANK_CHANGE_RE reads "our new remittance account" and "replaces all previous instructions" as a bank change
(a held-out test mail was caught only through its changed IBAN). A remittance ADVICE is not a bank change.
_is_source no longer reads "het einde van het kwartaal, rekeningnummer X zoals u van ons kent" (a held-out test mail) as
money coming FROM X; plain "van"/"from" needs an account word right after it or a payer word before it.
"""
from __future__ import annotations

import unittest
from decimal import Decimal

from countersign_shadow import checks, iban as ibanlib
from countersign_shadow.importers import transactions as tx
from countersign_shadow.planners.rules import RulePlanner

OWN = ibanlib.make_nl("OWNB", "0412776350")
OTHER = ibanlib.make_nl("KNWN", "0204918372")
GB_NEW = "GB54XMPL20000312345678"


def history():
    rows = [tx._tx("ing", d, Decimal("-250.00"), "EUR", "DRUKKERIJ JANSEN B.V.", OTHER, description="Betaling",
                   own_iban=OWN) for d in ("2026-08-01", "2026-09-01", "2026-10-01")]
    rows.append(tx._tx("ing", "2026-10-02", Decimal("-1.00"), "EUR", "Bankkosten", "", own_iban=OWN))
    return rows


def run(body, subject="Statement", sender="ar@harbourline.example", name="Harbourline"):
    msg = {"id": "q", "subject": subject, "from": sender, "from_name": name, "date": "2026-11-01", "body": body}
    p = RulePlanner().plan_one(msg)
    res = checks.evaluate(p, msg, history(), checks.Policy())
    return p, checks.verdict(p["action"], res), res


def failed(res, prefix):
    return [c for c in res if not c.ok and c.rule.startswith(prefix)]


class BankChangeWording(unittest.TestCase):
    def test_regex_reads_remittance_account_and_replaces_instructions(self):
        for s in ("Kindly note our new remittance account below.",
                  "Please use our updated remittance details.",
                  "This letter replaces all previous instructions.",
                  "This notice supersedes any prior payment instructions.",
                  "Deze brief vervangt alle eerdere betaalinstructies."):
            with self.subTest(s=s):
                self.assertIsNotNone(checks.BANK_CHANGE_RE.search(s))

    def test_regex_ignores_remittance_advice(self):
        for s in ("Remittance advice - invoice 2026-044", "Please find our remittance advice attached.",
                  "The new remittance advice format starts next month.", "This replaces the previous version of the PDF."):
            with self.subTest(s=s):
                self.assertIsNone(checks.BANK_CHANGE_RE.search(s))

    def test_never_paid_iban_with_remittance_wording_blocks_with_w2(self):
        # with an amount: pay draft, W2 + never-paid C5 block
        p, verdict, res = run("Dear customer,\n\nKindly note our new remittance account below, which replaces all "
                              f"previous instructions.\n\nIBAN: {GB_NEW}\n\nOutstanding: EUR 3,960.00 (invoices "
                              "NP-4471, NP-4502)")
        self.assertTrue(failed(res, "W2"))
        self.assertEqual(verdict, "blocked")
        # without an amount: whatever the label, the C14 bank-change block
        p, verdict, res = run("Dear customer,\n\nKindly note our new remittance account below, which replaces all "
                              f"previous instructions.\n\nIBAN: {GB_NEW}\n\nRegards,\nAccounts Receivable")
        self.assertTrue(failed(res, "W2"))
        self.assertEqual(verdict, "blocked")
        self.assertTrue(any("announces new bank details" in c.detail and c.severity == "block"
                            for c in failed(res, "C14")))

    def test_legit_remittance_advice_does_not_trigger(self):
        p, verdict, res = run("Remittance advice - invoice 2026-044\n\nPlease be advised that we have transferred "
                              f"EUR 2,150.00 to your account {OWN} in settlement of invoice 2026-044.\n\n"
                              f"Paid from: Harbourline Retail Ltd, {GB_NEW}\nValue date: 30 October 2026",
                              subject="Remittance advice - invoice 2026-044")
        self.assertFalse(failed(res, "W2"))
        self.assertNotIn(verdict, ("blocked", "flagged"))
        self.assertEqual(p["action"], "no_action")


class SourceNeedsPayerWord(unittest.TestCase):
    L03 = ("Ons kantoor factureert de begeleiding zoals gebruikelijk aan het einde van het kwartaal, rekeningnummer "
           f"{OTHER} zoals u van ons kent.")

    def _src(self, text, ib):
        return checks._is_source(text, text.index(ib))

    def test_van_as_of_is_not_source(self):
        self.assertFalse(self._src(self.L03, OTHER))
        self.assertNotEqual(checks.no_pay_reason(self.L03, OTHER),
                            "the IBAN is where money comes FROM (incoming money)")

    def test_real_sources_still_read(self):
        for t in (f"Paid from: Harbourline Retail Ltd, {GB_NEW}",
                  f"Good news: we received EUR 1,250.00 from {GB_NEW} for order 7731.",
                  f"Het bedrag is afgeschreven van rekening {GB_NEW}.",
                  f"De betaling komt van uw rekening {GB_NEW}.",
                  f"Afzender: {GB_NEW}",
                  f"Wij hebben € 80,00 ontvangen vanaf {GB_NEW}."):
            with self.subTest(t=t):
                self.assertTrue(self._src(t, GB_NEW))


if __name__ == "__main__":
    unittest.main()
