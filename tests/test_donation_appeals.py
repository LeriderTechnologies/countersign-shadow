# SPDX-License-Identifier: Apache-2.0
"""Regression tests. General shapes, each with fraud variants that must stay
blocked and legit variants that must stay as they were. Short unit-test strings only; no blind set was read.

- a plain donation appeal ("Doneer € 10,00 op X", "U kunt uw gift van € 50,00 overmaken op X") warns:
  a set amount up to DONATION_PLAIN_CAP with no pay-now pressure is an appeal; Dutch "gift" is a donation word.
- F2: "if you wish to keep supporting / protect our forests" is no threat; "keep our partnership" still is.
- F3: "make a donation of / stuur de donatie van EUR n" is an order; pay-now pressure beats "if you like".
- F4: the C3 "factuur volgt" softening needs no pay ask anywhere in the message.
- F5: "moet .. worden doorgeboekt" does not span a subordinate clause or an agent ("door ons").
- F6: "Nieuwe rekening van het kantoor / de firma: X" is bank-change wording.
- "Graag € n vandaag op X" is a pay ask (no "invoice comes later" planner skip).
- the decimal comma in "€ 4.100,00" does not cut a third-person payout notice.
"""
from __future__ import annotations

import unittest
from decimal import Decimal

from countersign_shadow import checks

try:
    from tests.test_heldout_regressions import KNOWN, NEW, OWN, failed, run
except ImportError:  # python3 -m unittest discover -s tests
    from test_heldout_regressions import KNOWN, NEW, OWN, failed, run

CEO = dict(sender="ceo.janssen@gmail.com", name="Peter Janssen")
CHARITY = dict(sender="info@stichtinggroen.example", name="Stichting Groen")
ADYEN = dict(sender="payouts@adyen.example", name="Adyen")


def B(t):
    return "Beste,\n\n" + t + "\n\nGroet,\nMark"


def verdict(body, **kw):
    return run(body=body, **kw)[1]


class PlainDonationAppeal(unittest.TestCase):
    def test_plain_appeals_warn(self):
        for body in (B(f"Please donate EUR 25.00 to {NEW}. Thank you for your support."),
                     B(f"Doneer € 10,00 op {NEW} en help ons de bomen te planten."),
                     B(f"Wilt u ons blijven steunen? Doneer dan € 15,00 op {NEW}. Alvast bedankt!"),
                     B(f"U kunt uw gift van € 50,00 overmaken op {NEW} t.n.v. Stichting Groen."),
                     B(f"Graag € 25,00 overmaken op {NEW} voor het goede doel."),
                     B(f"Doe mee: doneer € 20,00 op {NEW}. Elke euro telt.")):
            with self.subTest(body=body[8:70]):
                self.assertEqual(verdict(body, **CHARITY), "warn")

    def test_gift_appeal_softens_c5(self):  # pay_invoice draft, C5 never-paid becomes a warn
        p, v, res = run(body=B(f"U kunt uw gift van € 50,00 overmaken op {NEW} t.n.v. Stichting Groen."), **CHARITY)
        self.assertEqual((p["action"], v, failed(res).get("C5")), ("pay_invoice", "warn", "warn"))

    def test_big_or_pressed_donation_asks_still_block(self):
        for body in (B(f"Please donate EUR 4,750.00 to {NEW}. Thank you for your support."),
                     B(f"Doneer € 980,00 op {NEW} en help ons de bomen te planten."),
                     B(f"Doneer € 50,00 op {NEW}, het is dringend."),
                     B(f"Please donate EUR 200.00 to {NEW} before noon. Confidential."),
                     B(f"Graag € 240,00 overmaken op {NEW} voor het goede doel. Bevestig als het gedaan is."),
                     B(f"Doneer vandaag nog € 20,00 op {NEW}."),
                     B(f"Maak 12.400,- over naar {NEW} voor het goede doel.")):
            with self.subTest(body=body[8:70]):
                self.assertEqual(verdict(body, **CEO), "blocked")

    def test_english_gift_is_no_donation_word(self):  # a prize scam's "your gift" is not an appeal
        self.assertIsNone(checks._NP_DONATION.search("To receive your gift, pay the shipping fee."))
        self.assertIsNone(checks._NP_DONATION.search("Buy a gift card"))
        for t in ("uw gift van € 50", "een gift", "Giften zijn aftrekbaar", "de gift van de maand"):
            self.assertIsNotNone(checks._NP_DONATION.search(t), t)
        self.assertEqual(verdict(B(f"To receive your gift, pay the shipping fee of EUR 2.95 to {NEW}."), **CEO),
                         "blocked")

    def test_amount_value(self):
        self.assertEqual(checks._amount_value("Maak 12.400,- over", 5, 13), Decimal("12400.00"))
        self.assertEqual(checks._amount_value("€ 25,00", 2, 7), Decimal("25.00"))


class IfYouWishThreatNarrowed(unittest.TestCase):  # F2
    def test_regex(self):
        r = checks._IF_YOU_WISH_RE
        for t in ("If you wish to keep supporting our shelter", "If you wish to continue supporting our work",
                  "If you would like to protect our forests", "If you want to keep the library open",
                  "If you wish to stay a member", "If you would like to save a tree"):
            self.assertIsNotNone(r.search(t), t)  # a leave-it-to-you
        for t in ("if you wish to keep our partnership", "if you would like to continue working with us",
                  "If you want to maintain the contract", "if you want to stay our supplier",
                  "if you want to avoid problems with the audit", "If you wish to keep our business relationship"):
            self.assertIsNone(r.search(t), t)  # a threat

    def test_appeals_up_to_the_cap_warn(self):  # above the cap these block (test_donation_cap)
        for body in (B(f"If you wish to keep supporting our shelter, please donate EUR 50.00 to {NEW}. Thank you!"),
                     B(f"If you would like to protect our forests, donate EUR 30.00 to {NEW}. Every euro helps."),
                     B(f"If you want to keep the library open, please donate EUR 40.00 to {NEW}.")):
            with self.subTest(body=body[8:60]):
                self.assertEqual(verdict(body, **CHARITY), "warn")

    def test_threats_block(self):
        for body in (B(f"Please donate EUR 4,750.00 to {NEW} today if you wish to keep our partnership."),
                     B(f"If you want to stay our supplier, donate EUR 3,000.00 to {NEW} as a gesture."),
                     B(f"Kindly donate EUR 9,800.00 to {NEW} if you want to avoid problems with the audit.")):
            with self.subTest(body=body[8:60]):
                self.assertEqual(verdict(body, **CEO), "blocked")


class DonationWordedOrders(unittest.TestCase):  # F3
    def test_orders_block(self):
        for body in (B(f"Make a donation of EUR 4,750.00 to {NEW} today. Confirm when done."),
                     B(f"Stuur de donatie van € 4.750,00 vandaag naar {NEW}. Bevestig als het gedaan is."),
                     B(f"If you like the idea, please donate EUR 4,750.00 to {NEW} today. I need it done before 12."),
                     B(f"Geef een bijdrage van € 3.000,00 aan {NEW}, vandaag nog."),
                     B(f"Send the contribution of EUR 2,500.00 to {NEW}.")):
            with self.subTest(body=body[8:60]):
                self.assertEqual(verdict(body, **CEO), "blocked")

    def test_appeals_still_warn(self):  # the same at EUR 300-500 blocks (test_donation_cap)
        for body in (B(f"Make a donation of EUR 25.00 to {NEW} and help us plant trees."),
                     B(f"Make your donation of EUR 50.00 to {NEW}. Thank you for your support."),
                     B(f"Stuur uw donatie van € 30,00 naar {NEW}. Dank voor uw steun!"),
                     B(f"If you like, you can donate EUR 40.00 to {NEW}. Let us know if you have questions.")):
            with self.subTest(body=body[8:60]):
                self.assertEqual(verdict(body, **CHARITY), "warn")

    def test_changed_iban_donation_still_blocks(self):  # the known supplier on a new IBAN: never softened
        self.assertEqual(verdict(B(f"Doneer € 25,00 op {NEW} voor het goede doel.")), "blocked")


QUOTE = ("Hello,\n\nOur quote:\nPanels: EUR {a}\nTotal: EUR {a}\n\nThe invoice follows after completion; no advance "
         "payment is needed.\n\nKind regards,\nMaarten\nIBAN {ib}")


class NoPayNowNeedsNoAskAnywhere(unittest.TestCase):  # F4 (history: KNOWN paid 640-890)
    def test_ask_on_another_line_blocks(self):
        for body in (B(f"Graag vandaag € 3.400,00 overmaken.\nIBAN: {KNOWN}\nDe factuur volgt na levering."),
                     f"Hello,\n\nKindly settle EUR 3,300.00 today; the invoice will follow.\nAccount: {KNOWN}\n\nMaarten",
                     B(f"Geen aanbetaling nodig.\nWel graag € 3.100,00 overmaken voor vrijdag.\n\nIBAN {KNOWN}")):
            with self.subTest(body=body[8:60]):
                _p, v, res = run(body=body)
                self.assertEqual((v, failed(res).get("C3")), ("blocked", "block"))

    def test_quote_without_ask_still_warns(self):
        for body in (QUOTE.format(a="3,480.00", ib=KNOWN),
                     B(f"Offerte: totaal € 2.250,00. U hoeft niets vooraf te betalen, de factuur volgt na oplevering."
                       f"\n\nIBAN {KNOWN}"),
                     QUOTE.format(a="2,400.00", ib=KNOWN).replace("needed.", "needed. Please pay within 14 days of "
                                                                  "the invoice.")):
            with self.subTest(body=body[8:60]):
                _p, v, res = run(body=body)
                self.assertEqual((v, failed(res)), ("warn", {"C3": "warn"}))

    def test_message_asks(self):
        self.assertTrue(checks._message_asks(f"Graag € 3.400,00 overmaken.\nIBAN: {KNOWN}", KNOWN))
        self.assertFalse(checks._message_asks(f"U hoeft niets vooraf te betalen (€ 900,00).\nIBAN {KNOWN}", KNOWN))
        self.assertFalse(checks._message_asks(f"Wij betalen € 900,00 morgen.\nIBAN {KNOWN}", KNOWN))


class MustModalWindow(unittest.TestCase):  # F5
    def test_subordinate_clause_or_agent_is_a_notice(self):
        for body in (B(f"De pinomzet van € 4.100,00 moet eerst worden gecontroleerd voordat deze kan worden doorgeboekt "
                       f"naar {NEW}."),
                     B(f"Uw uitbetaling van € 4.100,00 moet morgen door ons worden doorgeboekt naar {NEW}, u hoeft niets "
                       "te doen."),
                     B(f"De omzet moet worden gecontroleerd zodat die kan worden overgezet naar {NEW}.")):
            with self.subTest(body=body[8:70]):
                self.assertNotEqual(verdict(body, **ADYEN), "blocked")

    def test_must_passive_asks_still_block(self):
        for body in (B(f"Het saldo van € 6.200,00 dient vandaag te worden doorgestort naar {NEW}."),
                     B(f"De pinomzet (€ 4.100,00) moet vandaag nog worden overgezet naar {NEW}."),
                     B(f"De omzet van deze week, € 4.100,00, moeten we per direct laten worden doorgeboekt naar {NEW}."),
                     B(f"De dagomzet van € 4.100,00 dient uiterlijk vandaag worden doorgestort op {NEW}."),
                     B(f"De omzet van € 4.100,00 moet door jou worden doorgeboekt naar {NEW}.")):
            with self.subTest(body=body[8:70]):
                self.assertEqual(verdict(body, **CEO), "blocked")


class BankChangeOwnerNouns(unittest.TestCase):  # F6
    def test_owner_nouns_block(self):
        for owner in ("van het kantoor", "van de firma", "van de onderneming", "voor onze zaak", "van de firma Jansen"):
            with self.subTest(owner=owner):
                self.assertEqual(verdict(B(f"Omzet deze week: € 4.105,00. Nieuwe rekening {owner}: {NEW}."), **CEO),
                                 "blocked")

    def test_bill_wording_is_no_bank_change(self):
        for t in ("Nieuwe rekening voor de lunch: € 45,00", "Ik stuur een nieuwe rekening voor het kantoor.",
                  "Nieuwe rekening van het kantoor: zie de bijlage."):
            self.assertIsNone(checks.BANK_CHANGE_RE.search(t), t)
        self.assertIsNotNone(checks.BANK_CHANGE_RE.search(f"Nieuwe rekening van het kantoor: {NEW}"))


class NoticeAmountComma(unittest.TestCase):
    def test_third_person_payout_notice_with_amount(self):
        for body in (B(f"Adyen gaat de pinomzet van € 4.100,00 overmaken naar {NEW}."),
                     B(f"Adyen zal de pinomzet van € 4.100,00 morgen betalen op {NEW}."),
                     B(f"Adyen will transfer the turnover of EUR 4,100.00 to {NEW} tomorrow.")):
            with self.subTest(body=body[8:70]):
                self.assertNotEqual(verdict(body, **ADYEN), "blocked")

    def test_asks_still_block(self):
        for body in (B(f"Adyen gaat de pinomzet van € 4.100,00 overmaken naar {NEW}, graag vandaag bevestigen."),
                     B(f"Je gaat de omzet van € 4.100,00 vandaag overmaken naar {NEW}."),
                     B(f"Wij zijn overgestapt, de omzet van € 4.100,00, graag overmaken naar {NEW}."),
                     B(f"Omzet € 4.100,00, overmaken naar {NEW}.")):
            with self.subTest(body=body[8:70]):
                self.assertEqual(verdict(body, **CEO), "blocked")


class RequestWordAmountOnIban(unittest.TestCase):  # (history: KNOWN paid 640-890, largest 890)
    def test_planner_reads_the_ask(self):
        # Decision: the ask is read (pay_invoice, never "none"); C3 then judges it like the same mail WITH a pay verb.
        for amount, want in (("800,00", ("ok", {})), ("2.400,00", ("blocked", {"C3": "block"}))):
            plain = B(f"De factuur volgt. Graag € {amount} vandaag op {KNOWN}.")
            verb = B(f"De factuur volgt. Graag € {amount} vandaag overmaken op {KNOWN}.")
            for body in (plain, verb):
                with self.subTest(body=body[8:70]):
                    p, v, res = run(body=body)
                    self.assertEqual((p["action"], v, failed(res)), ("pay_invoice",) + want)

    def test_new_iban_with_invoice_follows_blocks(self):
        for body in (B(f"De factuur volgt. Graag € 800,00 vandaag op {NEW}."),
                     B(f"De factuur volgt later. Graag € 800,00 op {NEW}."),
                     B(f"Al betaald? Graag € 800,00 op {NEW}.")):
            with self.subTest(body=body[8:70]):
                self.assertEqual(verdict(body, **CEO), "blocked")

    def test_betalen_is_pay_context(self):
        p, v, _res = run(body=B(f"Totaal € 3.450,00. Geen aanbetaling nodig, maar wel graag vandaag betalen.\n\n"
                                f"IBAN {KNOWN}"))
        self.assertEqual((p["action"], v), ("pay_invoice", "warn"))
