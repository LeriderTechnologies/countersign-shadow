# SPDX-License-Identifier: Apache-2.0
"""(1) the structural message checks (C14, C13 on message IBANs) also run on no_action /
flag_suspicious cards; (2) the rule planner reads amounts written without a currency sign next to a payment word."""
from __future__ import annotations

import contextlib
import io
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

from countersign_shadow import checks, iban as ibanlib, samples
from countersign_shadow.__main__ import demo_workspace
from countersign_shadow.money import find_bare_amounts
from countersign_shadow.planners.rules import RulePlanner, _main_amount
from countersign_shadow.shadow import draft_cards

NEW = ibanlib.make_nl("BUNQ", "2011223344")
KARTON = samples.KARTON


def sp(i):
    return " ".join(i[k:k + 4] for k in range(0, len(i), 4))


class Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        with contextlib.redirect_stdout(io.StringIO()):
            cls.ws = demo_workspace(Path(cls._tmp.name) / "ws")

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def card(self, body, frm="info@nieuw.example", name="Atelier Nieuw", subject="Bericht"):
        msg = {"id": "m-257", "from": frm, "from_name": name, "subject": subject, "date": "2026-11-01",
               "body": body, "pdfs": []}
        return draft_cards(self.ws, RulePlanner(), checks.Policy(), messages=[msg])[0]

    @staticmethod
    def failed(card, severity=None):
        return {c["rule"].split()[0] for c in card["checks"] if not c["ok"] and severity in (None, c["severity"])}


class TestNoActionStructuralChecks(Base):
    def test_pay_ask_without_amount_is_blocked_on_no_action_card(self):
        c = self.card(f"Kunt u het bedrag storten op {sp(NEW)}?")
        self.assertEqual(c["action"], "no_action")
        self.assertEqual(c["verdict"], "blocked", c["reasons"])
        self.assertIn("C14", self.failed(c, "block"))

    def test_iban_changed_without_amount_is_blocked(self):
        c = self.card(f"Wij zijn overgestapt naar een andere bank. Ons rekeningnummer is vanaf heden {sp(NEW)}.\n"
                      "Wilt u dit aanpassen in uw administratie?", frm="debiteuren@kartonfabriek-noord.example",
                      name="Kartonfabriek Noord BV")
        self.assertEqual(c["action"], "no_action")
        self.assertEqual(c["verdict"], "blocked")
        c14 = next(x for x in c["checks"] if x["rule"].startswith("C14"))
        self.assertIn("IBAN CHANGED", c14["detail"])

    def test_fake_tax_office_without_amount_fails_c13(self):
        c = self.card(f"Maak het verschuldigde bedrag over op {sp(NEW)} t.n.v. Belastingdienst.",
                      frm="aanslag@belastingdienst-online.example", name="Belastingdienst")
        self.assertEqual(c["action"], "no_action")
        self.assertEqual(c["verdict"], "blocked")
        self.assertIn("C13", self.failed(c, "block"))

    def test_c13_skips_own_account_on_no_action(self):
        own = samples.OWN
        # The tax office announces a refund to YOUR account: claims Belastingdienst, but the IBAN is your own.
        c = self.card(f"Uw teruggaaf omzetbelasting wordt binnen 5 werkdagen gestort op {sp(own)}.",
                      frm="noreply@belastingdienst.example", name="Belastingdienst")
        self.assertNotIn("C13", self.failed(c))

    def test_customer_source_iban_stays_silent(self):
        # Never-paid IBAN that nobody asks you to pay: no block and no C14 warning on a no_action card.
        c = self.card(f"Bedankt, ik heb de factuur betaald vanaf {sp(NEW)}.", frm="a@klant.example", name="Klant")
        self.assertEqual(c["action"], "no_action")
        self.assertEqual(c["verdict"], "none")
        self.assertNotIn("C14", self.failed(c))

    def test_signature_iban_stays_silent(self):
        c = self.card(f"Tot donderdag!\n\nMet vriendelijke groet,\nPiet\nKvK 12345678 | IBAN {sp(NEW)}",
                      frm="piet@bouw.example", name="Piet")
        self.assertEqual(c["verdict"], "none")

    def test_bank_change_without_iban_only_warns_on_no_action(self):
        c = self.card("Let op: wij hebben een nieuw rekeningnummer. U ontvangt het per post.")
        self.assertEqual(c["action"], "no_action")
        self.assertEqual(c["verdict"], "warn")
        self.assertIn("W2", self.failed(c))
        self.assertNotIn("C14", self.failed(c))

    def test_known_iban_on_no_action_card_is_fine(self):
        c = self.card(f"Ons rekeningnummer blijft {sp(KARTON)}.", frm="debiteuren@kartonfabriek-noord.example",
                      name="Kartonfabriek Noord BV")
        self.assertNotEqual(c["verdict"], "blocked")

    def test_flag_suspicious_card_still_flagged_and_carries_c14(self):
        c = self.card(f"Uw account is geblokkeerd. Klik op de link en betaal de 5,00 kosten op {sp(NEW)}.")
        self.assertEqual(c["action"], "flag_suspicious")
        self.assertEqual(c["verdict"], "flagged")
        self.assertIn("C14", self.failed(c, "block"))


class TestBareAmounts(unittest.TestCase):
    def test_money_regex(self):
        got = [r for _a, _b, r in find_bare_amounts("1.840,00 en 1,250.00 en 143,20 en 95,- maar niet 1150, "
                                                   "12.11.2026, 2026/0412, GH-7802, NL56BUNQ2011223344")]
        self.assertEqual(got, ["1.840,00", "1,250.00", "143,20"])

    def test_planner_reads_amount_after_payment_word(self):
        for text, want in (("Totaal: 312,40\nIBAN x", "312.40"), ("Amount due: 1,250.00", "1250.00"),
                           ("Het openstaande bedrag van factuur GH-7802 is 143,20. Graag", "143.20"),
                           ("Please pay (amount 640.00) to us", "640.00"), ("te betalen 450\nRekening", "450.00"),
                           ("Bedrag: 1.840,00\nIBAN", "1840.00"), ("Uw saldo per 31.10 is 1.234,56", "1234.56"),
                           ("Totaal te betalen: 121,00 (incl. btw)", "121.00")):
            with self.subTest(text=text):
                self.assertEqual(_main_amount(text)[0], Decimal(want))

    def test_planner_ignores_numbers_that_are_no_amounts(self):
        for text in ("te betalen binnen 14 dagen", "Totaal 3 items", "levering op 12.11.2026 tussen 09.30 en 11.00",
                     "Factuurnummer 2026.104", "Kenmerk 2026/0412", "Bel 06 12 34 56 78 of 020-123 45 67",
                     "Payment due 15.11.", f"Rekening {sp(NEW)}", "Prijs 19.95 per stuk"):
            with self.subTest(text=text):
                self.assertIsNone(_main_amount(text))

    def test_currency_sign_still_wins(self):
        self.assertEqual(_main_amount("Totaal: 312,40 en verzendkosten € 5,00")[0], Decimal("5.00"))


class TestPlannerEndToEnd(Base):
    def test_sign_less_invoice_from_known_supplier_is_ok(self):
        c = self.card(f"Hierbij factuur F-2026-0460, totaal 312,40, te betalen binnen 14 dagen op {sp(KARTON)}.",
                      frm="debiteuren@kartonfabriek-noord.example", name="Kartonfabriek Noord BV")
        self.assertEqual(c["action"], "pay_invoice")
        self.assertEqual(c["amount"], "312.40")
        self.assertEqual(c["verdict"], "ok", c["reasons"])

    def test_english_thousands_amount_passes_v2(self):
        c = self.card(f"Invoice 8812. Amount due: 1,250.00. Kindly transfer to {sp(KARTON)}.",
                      frm="debiteuren@kartonfabriek-noord.example", name="Kartonfabriek Noord BV")
        self.assertEqual(c["action"], "pay_invoice")
        self.assertNotIn("V2", self.failed(c))

    def test_sign_less_invoice_to_swapped_iban_is_blocked_by_c5(self):
        c = self.card(f"Factuur F-2026-0450\nTotaal: 312,40\nTe betalen op {sp(NEW)} t.n.v. Kartonfabriek Noord BV.",
                      frm="debiteuren@kartonfabriek-noord.example", name="Kartonfabriek Noord BV")
        self.assertEqual(c["action"], "pay_invoice")
        self.assertEqual(c["verdict"], "blocked")
        self.assertIn("IBAN CHANGED", next(x["detail"] for x in c["checks"] if x["rule"].startswith("C5")))


if __name__ == "__main__":
    unittest.main()
