# SPDX-License-Identifier: Apache-2.0
"""Regression tests: the four miss patterns of an earlier held-out set, and their false-positive guards.

1. "vanaf <time>" / "from <time>" before an IBAN is WHEN, not a source account ("vanaf deze maand: X").
2. "nieuwe rekening(:)" / "nieuw rek nr" / "new account number" / "changed their collection acct" announce new bank
   details. Dutch "rekening" is also "bill", so plain "nieuwe rekening" without nr/colon/IBAN does not count.
3. A "label: value" block (Account holder / IBAN / Reference) right after the paragraph with the amount.
4. Amounts without a currency sign ("1.840,00", "(1.150)") next to an IBAN.
Plus: "bedankt voor je betaling" / "thank you for your payment" is a receipt, not an ask.
"""
from __future__ import annotations

import unittest

from countersign_shadow import checks
from tests.test_structural_pay_ask import NEW, X, Base


class TestM1137Block(Base):
    def test_vanaf_time_is_not_a_source(self):
        for body in (f"nieuwe huur vanaf deze maand: {X}, zelfde bedrag als altijd.",
                     f"vanaf volgende maand graag op {X}, bedrag blijft 1.150.",
                     f"From next month: {X}, same amount as usual.",
                     f"Vanaf 1 december: {X}. Het bedrag blijft gelijk."):
            with self.subTest(body=body):
                self.assertEqual(self.sev(body), "block")
        self.assertFalse(checks._is_source(f"vanaf deze maand: {X}", len("vanaf deze maand: ")))
        self.assertTrue(checks._is_source(f"overgemaakt vanaf {X}", len("overgemaakt vanaf ")))

    def test_new_account_wording_blocks(self):
        for body in (f"Nieuwe rekening: {X}", f"nieuwe rek nr voor de huur: {X}", f"Ons nieuwe rekeningnr is {X}.",
                     f"Graag naar ons andere rekeningnummer {X}.", f"Please note our new account number: {X}",
                     f"they changed their collection acct, use {X}", f"Updated bank details: {X}",
                     f"Oude rekening (niet meer gebruiken): NL83 TEST 0230 9911 84\nNieuwe rekening: {X}"):
            with self.subTest(body=body):
                self.assertTrue(checks.BANK_CHANGE_RE.search(body))
                self.assertEqual(self.sev(body), "block")

    def test_label_block_after_ask_paragraph(self):
        for body in (f"The balance of EUR 3,400.00 should be credited to our escrow account:\n\n"
                     f"Account holder: Harbour Escrow\nIBAN: {X}\nReference: HE-1",
                     f"In de bijlage factuur 26-1004, totaal € 1.203,95.\n\nRekening: {X}\nTnv: Atelier Nieuw",
                     f"The deposit of 2.500,00 is due on arrival.\n\nBeneficiary name: Seaside\nIBAN: {X}\nRef: S-2"):
            with self.subTest(body=body):
                self.assertIn(NEW, checks.find_structural_asks(body))
                self.assertEqual(self.sev(body), "block")

    def test_bare_amounts_anchor(self):
        for body in (f"pls do the 480,00 to {X} before fri, thx", f"pls sort the VAT top-up - 1.840,00 to {X} ref Q3.",
                     f"Huur {X}, zelfde als altijd (1.150)."):
            with self.subTest(body=body):
                self.assertEqual(self.sev(body), "block")
        self.assertEqual([r for _a, _b, r in checks._bare_amounts("1.840,00 en 95,00 en 1,234.56 en (1.150)")],
                         ["1.840,00", "95,00", "1,234.56", "1.150"])
        self.assertEqual(checks._bare_amounts("1150, 01-11-2026, 01.11.2026, 12.34.56.78, v1.2.3, 26-1004"), [])

    def test_thanks_then_new_ask_still_blocks(self):
        self.assertEqual(self.sev(f"Thanks for your payment last month. This month please pay to {X}."), "block")


class TestM1137Legit(Base):
    def test_rekening_as_bill_and_portal_wording(self):
        for body in ("Ik stuur je een nieuwe rekening voor de gecorrigeerde uren.",
                     "De nieuwe rekening is verstuurd, de oude kunt u weggooien.",
                     "Belt u even, ik heb een nieuwe rekening bij een andere bank en wil dat bespreken.",
                     "We changed the account manager for your account; Sanne is your new contact.",
                     "I updated my account settings on the portal."):
            with self.subTest(body=body):
                self.assertFalse(checks.BANK_CHANGE_RE.search(body))
                self.assertNotEqual(self.sev(body), "block")

    def test_receipts_footers_and_reader_accounts_do_not_block(self):
        for body in (f"Bedankt voor je betaling van € 95,00.\n\nIBAN: {X}\nKvK: 12345678",
                     f"Thank you for your payment of EUR 95.00.\n\nAccount holder: Atelier Nieuw\nIBAN: {X}",
                     f"Versie 2.10 van de app staat klaar. Groet, Jan | KvK 12345678 | IBAN {X}",
                     f"Hoi, we hebben 1.150 bezoekers gehad dit weekend.\n\nGroet, Jan\nIBAN {X}",
                     f"Vanaf deze maand schrijven we de huur af van uw rekening {X}.",
                     f"Wij hebben 95,00 naar uw rekening {X} overgemaakt.",
                     f"De terugbetaling komt vanaf onze rekening {X}, bedrag 95,00."):
            with self.subTest(body=body):
                self.assertNotEqual(self.sev(body), "block")

    def test_known_supplier_new_wording_passes(self):
        k = " ".join(self.known[i:i + 4] for i in range(0, len(self.known), 4))
        for body in (f"Factuur 77 bedraagt 640,00.\n\nRekening: {k}\nTnv: Leverancier",
                     f"Zelfde rekening als altijd: {k}, bedrag 1.150 vanaf deze maand."):
            with self.subTest(body=body):
                self.assertEqual(self.sev(body), "ok")


if __name__ == "__main__":
    unittest.main()
