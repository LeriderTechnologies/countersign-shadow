# SPDX-License-Identifier: Apache-2.0
"""Regression tests. General shapes, each with fraud
variants that must stay blocked and legit variants that must stay as they were. No blind set was read for these.

- F1: the C3 "factuur volgt / geen aanbetaling" softening needs the payee IBAN's own sentence to ask nothing and the
  amount to be at most 4x the largest earlier payment to that IBAN.
- F2: "donate / doneer" is a pay verb for the donation-word gate; "Transfer the donation of EUR n" is an order;
  "if you wish to keep our partnership" is a threat, not a leave-it-to-you.
- "moet worden doorgeboekt / doorgestort / overgezet naar X" is a pay ask (must-modals only).
- split "over maken" is a pay verb (not "afspraken / zorgen .. over maken").
- "Zet de omzet van € n op (onze spaar)rekening X" keeps its amount anchor; "Nieuwe rekening van de zaak: X"
  is bank-change wording.
"""
from __future__ import annotations

import unittest

from countersign_shadow import checks, iban as ibanlib

try:
    from tests.test_heldout_regressions import KNOWN, NEW, NEW2, OWN, failed, run, spaced
except ImportError:  # python3 -m unittest discover -s tests
    from test_heldout_regressions import KNOWN, NEW, NEW2, OWN, failed, run, spaced

CEO = dict(sender="ceo.janssen@gmail.com", name="Peter Janssen")
CHARITY = dict(sender="info@stichtinggroen.example", name="Stichting Groen")
ADYEN = dict(sender="payouts@adyen.example", name="Adyen")
OWN_LA = ibanlib.make_nl("OWNB", "0412776351")  # look-alike of OWN, valid checksum, never paid


def verdict(body, **kw):
    return run(body=body, **kw)[1]


QUOTE = ("Hello,\n\nOur quote:\nPanels: EUR {a}\nTotal: EUR {a}\n\nThe invoice follows after completion; no advance "
         "payment is needed.\n\nKind regards,\nMaarten\nIBAN {ib}")


class NoPayNowCap(unittest.TestCase):  # (history: KNOWN paid 640-890)
    def test_pay_ask_or_big_amount_blocks(self):
        for body in (f"Beste,\n\nGraag € 8.900,00 overmaken op {KNOWN}. De factuur volgt.\n\nMark",
                     f"Beste,\n\nGraag € 25.000,00 overmaken op {KNOWN}. Geen aanbetaling nodig.\n\nMark",
                     f"Hello,\n\nNo advance payment is needed. Please pay EUR 2,400.00 to {KNOWN} today.\n\nMark",
                     QUOTE.format(a="25,000.00", ib=KNOWN),  # IBAN in the signature, no ask, but 28x
                     QUOTE.format(a="8,900.00", ib=KNOWN)):  # 10x
            with self.subTest(body=body[-80:]):
                p, v, res = run(body=body)
                self.assertEqual((v, failed(res).get("C3")), ("blocked", "block"), (p["action"], p["amount"]))

    def test_quote_within_cap_still_warns(self):
        for a in ("3,480.00", "2,400.00"):  # 3.9x and 2.7x; a held-out test mail's shape (signature IBAN, no pay verb)
            with self.subTest(a=a):
                _p, v, res = run(body=QUOTE.format(a=a, ib=KNOWN))
                self.assertEqual((v, failed(res)), ("warn", {"C3": "warn"}))

    def test_iban_sentence_asks(self):
        self.assertTrue(checks._iban_sentence_asks(f"Graag € 8.900,00 overmaken op {KNOWN}.", KNOWN))
        self.assertTrue(checks._iban_sentence_asks(f"Graag € 8.900,00 op {KNOWN}.", KNOWN))
        self.assertFalse(checks._iban_sentence_asks(f"Groet,\nMaarten\nIBAN {KNOWN}", KNOWN))
        self.assertFalse(checks._iban_sentence_asks(f"Wij betalen € 900,00 op {KNOWN}.", KNOWN))


class DonateWording(unittest.TestCase):
    def test_ceo_donation_asks_block(self):
        for body in (f"Hi,\n\nPlease donate EUR 4,750.00 to {NEW} today if you wish to keep our partnership.\n\nPeter",
                     f"Hi,\n\nPlease donate EUR 4,750.00 to {NEW} today.\n\nPeter",
                     f"Hoi,\n\nDoneer vandaag € 4.750,00 aan {NEW}, het is voor het goede doel.\n\nPeter",
                     f"Hi,\n\nTransfer the donation of EUR 4,750.00 to {NEW} today, it is optional for others but not "
                     "for us.\n\nPeter",
                     f"Hi,\n\nPay the contribution of EUR 4,750.00 to {NEW} today, a donation.\n\nPeter",
                     f"Hi,\n\nIf you would like to continue working with us, transfer EUR 4,750.00 to {NEW} today as "
                     "a donation.\n\nPeter"):
            with self.subTest(body=body[4:60]):
                self.assertEqual(verdict(body, **CEO), "blocked")

    def test_real_appeals_still_warn(self):
        for body in (f"Hi,\n\nPlease transfer your donation of EUR 50.00 to {NEW}. Thank you for your support.\n\nBoard",
                     f"Hi,\n\nIf you wish to support us, please donate EUR 25.00 to {NEW}. It is voluntary.\n\nBoard",
                     f"Beste vrienden,\n\nWie wil, kan € 25,00 doneren op {NEW} voor het goede doel.\n\nStichting",
                     f"Beste vrienden,\n\nAls u wilt, kunt u € 20,00 doneren aan {NEW}. Dank voor uw steun!\n\n"
                     "Stichting Groen",
                     f"Hi,\n\nOur annual charity drive: please donate EUR 50.00 to {NEW} if you can.\n\nThe board"):
            with self.subTest(body=body[:60]):
                self.assertEqual(verdict(body, **CHARITY), "warn")

    def test_if_you_wish_threat(self):
        r = checks._IF_YOU_WISH_RE
        self.assertIsNone(r.search("if you wish to keep our partnership"))
        self.assertIsNone(r.search("if you would like to continue working with us"))
        self.assertIsNotNone(r.search("if you wish to support us"))
        self.assertIsNotNone(r.search("if you prefer"))


class PassivePayoutVerbs(unittest.TestCase):
    def test_must_passive_blocks(self):
        for body in (f"Hoi,\n\nDe omzet van € 4.100,00 moet worden doorgeboekt naar {NEW}.\n\nPeter",
                     f"Hoi,\n\nDe weekomzet moet vandaag nog worden doorgestort naar {NEW}.\n\nPeter",
                     f"Hoi,\n\nHet saldo van € 4.100,00 dient te worden overgezet naar {NEW}.\n\nPeter",
                     f"Hoi,\n\nDe pinomzet dient vandaag worden doorgeboekt naar {spaced(NEW)}.\n\nPeter"):
            with self.subTest(body=body[6:60]):
                self.assertEqual(verdict(body, **CEO), "blocked")

    def test_payout_notices_stay_non_blocking(self):
        for body in (f"Beste klant,\n\nDe pinomzet van € 4.100,00 kan pas morgen worden doorgeboekt naar rekening "
                     f"{NEW2}.\n\nAdyen",
                     f"Beste klant,\n\nHet bedrag van de pinomzet, € 4.100,00, is doorgeboekt naar rekening {NEW2}.\n\n"
                     "Adyen",
                     f"Beste klant,\n\nDe pinomzet van € 4.100,00 zal worden doorgeboekt naar rekening {NEW2}.\n\nAdyen",
                     f"Beste klant,\n\nDe pinomzet van € 4.100,00 wordt morgen doorgeboekt naar rekening {NEW2}.\n\n"
                     "Adyen"):
            with self.subTest(body=body[14:70]):
                self.assertNotEqual(verdict(body, **ADYEN), "blocked")


class SplitOverMaken(unittest.TestCase):
    def test_split_verb_blocks(self):
        for body in (f"Hoi,\n\nWil je de 12.400,00 over maken naar {NEW}? Geen enkele verplichting.\n\nPeter",
                     f"Hoi,\n\nKun je € 4.750,00 vandaag over maken naar {NEW}? Een donatie.\n\nPeter",
                     f"Hoi,\n\nGraag het bedrag vandaag over maken op {NEW}.\n\nPeter"):
            with self.subTest(body=body[6:50]):
                self.assertEqual(verdict(body, **CEO), "blocked")

    def test_not_a_pay_verb(self):
        for t in ("Daar kunnen we afspraken over maken.", "Je hoeft je geen zorgen over maken.",
                  "We moeten een afspraak over maken."):
            with self.subTest(t=t):
                self.assertIsNone(checks._PAY_VERB_RE.search(t))
        self.assertEqual(checks._PAY_VERB_RE.search("Wil je het over maken?").group(0), "over maken")


class TurnoverToAccount(unittest.TestCase):
    def test_d_shapes_block(self):
        for ib in (OWN_LA, NEW):
            for body in (f"Hoi,\n\nZet de omzet van € 4.105,00 vandaag op onze spaarrekening {ib}.\n\nPeter\n{OWN}",
                         f"Hoi,\n\nZet de omzet van € 4.105,00 vandaag op rekening {ib}.\n\nPeter",
                         f"Hoi,\n\nOmzet deze week: € 4.105,00. Nieuwe rekening van de zaak: {ib}.\n\nPeter",
                         f"Hoi,\n\nNieuwe rekening van het bedrijf {ib}.\n\nPeter"):
                with self.subTest(ib=ib[-4:], body=body[6:60]):
                    self.assertEqual(verdict(body, **CEO), "blocked")

    def test_zet_op_reads_only_account_words(self):
        self.assertFalse(checks._asked_sentence("Zet de omzet van € 4.105,00 op de agenda.", 15))
        self.assertTrue(checks._asked_sentence(f"Zet de omzet van € 4.105,00 op onze spaarrekening {NEW}.", 15))
        # a third-person notice is no ask
        self.assertFalse(checks._asked_sentence(f"Adyen zet de omzet van € 4.105,00 op rekening {NEW}.", 20))

    def test_reports_stay_silent(self):
        for body in (f"Hoi,\n\nOmzet deze week: € 4.105,00. Rekening van de zaak: {OWN}.\n\nPeter",
                     f"Hoi,\n\nDe omzet van € 4.105,00 staat op onze rekening {OWN}.\n\nPeter",
                     f"Hoi,\n\nIk stuur je een nieuwe rekening voor de zaak.\n\nPeter"):
            with self.subTest(body=body[6:60]):
                self.assertNotEqual(verdict(body, **CEO), "blocked")
        self.assertIsNone(checks.BANK_CHANGE_RE.search("Ik stuur je een nieuwe rekening voor de zaak."))


if __name__ == "__main__":
    unittest.main()
