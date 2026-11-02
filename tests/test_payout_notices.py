# SPDX-License-Identifier: Apache-2.0
"""Regression tests. General shapes, each with fraud variants that must stay blocked.
Shapes from a spent held-out test set are rewritten here (it is a tuned set from this version on).

- a third-person payout / bank notice ("Adyen will send the daily turnover .. to X", "is doorgeboekt naar X",
  "the sweep .. has run") is no pay ask; imperatives and request words still are.
- the pay_invoice amount is the one in the payee IBAN's paragraph, not a figure from a summary list; a known
  supplier's "the invoice follows / no advance payment is needed" makes C3 warn, not block.
- the '.' in "12.400,00" no longer cuts "maak .. over" in find_pay_asks.
- "optional" / "donation" words no longer soften an outright ask to pay a set amount to a never-paid IBAN.
- a pay_invoice draft to your own account without transfer wording becomes a no_action card.
"""
from __future__ import annotations

import tempfile
import unittest

from countersign_shadow import checks, shadow
from countersign_shadow.planners.rules import RulePlanner
from countersign_shadow.store import Workspace

try:
    from tests.test_heldout_regressions import KNOWN, NEW, NEW2, OWN, failed, history, run, spaced
except ImportError:  # python3 -m unittest discover -s tests
    from test_heldout_regressions import KNOWN, NEW, NEW2, OWN, failed, history, run, spaced

ADYEN = dict(sender="payouts@adyen.example", name="Adyen")
CEO = dict(sender="ceo.janssen@gmail.com", name="Peter Janssen")
SMIT = dict(sender="boekhouder.smit@gmail.com", name="Smit")
CHARITY = dict(sender="info@stichtinggroen.example", name="Stichting Groen")


def verdict(body, **kw):
    return run(body=body, **kw)[1]


class ThirdPartyNotice(unittest.TestCase):
    def test_payout_notices_are_no_ask(self):
        for body in (f"Beste klant,\n\nHet bedrag van de pinomzet, € 4.100,00, is doorgeboekt naar rekening {NEW2}.\n\nAdyen",
                     f"Hello,\n\nAdyen will send the daily turnover of EUR 4,100.00 to {NEW2} tomorrow.\n\nAdyen",
                     f"Hello,\n\nThe automatic sweep of your daily revenue (EUR 4,100.00) to savings account {NEW2} has "
                     "run.\n\nYour bank",
                     f"Hello,\n\nAdyen will pay out the daily turnover of EUR 4,100.00 to {NEW2} tomorrow.\n\nAdyen"):
            with self.subTest(body=body[14:60]):
                self.assertNotEqual(verdict(body, **ADYEN), "blocked")

    def test_notice_verb_is_no_pay_cue(self):
        self.assertEqual(checks.find_pay_asks(f"Adyen will send the daily turnover of EUR 4,100.00 to {NEW2}."), {})
        self.assertIn(NEW, checks.find_pay_asks(f"Please send the daily turnover of EUR 4,100.00 to {NEW}."))

    def test_asks_still_block(self):
        for body in (f"Hi,\n\nPlease send the daily turnover of EUR 4,100.00 to {NEW} today.\n\nSmit",
                     f"Hi,\n\nSend the daily turnover of EUR 4,100.00 to {NEW} today.\n\nSmit",
                     f"Hoi,\n\nKun je het bedrag van de pinomzet, € 4.100,00, doorboeken naar {NEW}?\n\nSmit",
                     f"Hi,\n\nCould you transfer the daily revenue (EUR 4,100.00) to savings account {NEW} today?\n\nSmit",
                     f"Hi,\n\nThe CEO asks that you send the turnover of EUR 4,100.00 to {NEW} today.\n\nSmit",
                     f"Hi,\n\nYou will transfer the daily turnover of EUR 4,100.00 to {NEW} today.\n\nSmit",
                     f"Hi,\n\nThe CEO will need you to send the turnover of EUR 4,100.00 to {NEW} today.\n\nSmit",
                     f"Hoi,\n\nJe gaat vandaag de dagomzet van € 4.100,00 overmaken naar {NEW}.\n\nSmit",
                     f"Hoi,\n\nDe bank zal de omzet van € 4.100,00 niet doorboeken, dus maak die vandaag over naar "
                     f"{NEW}.\n\nSmit",
                     f"Hi,\n\nThe sweep has run. Please top up EUR 500.00 to {NEW} today.\n\nSmit",
                     f"Hoi,\n\nPeter gaat € 9.800,00 betalen aan {NEW}, regel jij dat vandaag?\n\nSmit",
                     f"Hoi,\n\nHet bedrag van € 9.800,00 is overgezet naar {NEW}, svp nog vandaag bevestigen.\n\nSmit",
                     f"Hi,\n\nAccounting will pay EUR 4,100.00 to {NEW} today, please approve it in the bank app.\n\nSmit"):
            with self.subTest(body=body[:50]):
                self.assertEqual(verdict(body, **SMIT), "blocked")


SUMMARY = ("Samenvatting:\n\nOmzet hoog tarief: € 41.220,00\nAf te dragen btw: € 8.656,20\n"
           "Te betalen aan de Belastingdienst: € 5.537,75\n\n")
QUOTE = ("Hello,\n\nOur quote:\nPanels: EUR 2,340.00\nInstallation: EUR 1,140.00\nTotal: EUR 3,480.00\n\n"
         "The invoice follows after completion; no advance payment is needed.\n\nKind regards,\nMaarten\nIBAN {}")


class AmountNextToIban(unittest.TestCase):
    def test_amount_in_the_iban_paragraph_wins(self):
        for body, amount in (
                ("Beste,\n\n" + SUMMARY + "De betaling doe je zelf via het betalingskenmerk. Mijn factuur volgt los "
                 f"(zoals altijd € 425,00 op {spaced(KNOWN)}).\n\nPaul", "425.00"),
                ("Beste leden,\n\nUitgaven evenementen: € 12.980\nReserve: € 6.115\n\nDe contributie blijft € 720 per "
                 f"bedrijf. Graag voor 31 januari overmaken op {spaced(KNOWN)}.\n\nGerda", "720.00"),
                ("Beste klant,\n\nDe salarisronde is verwerkt. Totaal uitbetaald: € 9.240,00.\n\nOnze kosten "
                 f"(€ 45,00) worden zoals altijd afgeschreven van rekening {KNOWN}.\n\nSalarisDirect", None)):
            with self.subTest(amount=amount):
                p, v, res = run(body=body)
                if amount:
                    self.assertEqual((p["action"], p["amount"], v), ("pay_invoice", amount, "ok"), failed(res))
                else:
                    self.assertNotEqual(v, "blocked")

    def test_one_paragraph_mail_keeps_the_usual_pick(self):
        p, _v, _res = run(body=f"Factuur 2026-77\nSubtotaal: € 800,00\nBtw: € 168,00\nTotaal: € 968,00\nIBAN {KNOWN}")
        self.assertEqual(p["amount"], "968.00")

    def test_known_quote_with_invoice_later_warns(self):
        p, v, res = run(body=QUOTE.format(KNOWN))
        self.assertEqual((v, failed(res)), ("warn", {"C3": "warn"}))

    def test_fraud_variants_still_block(self):
        pol = checks.Policy()
        pol.limits["pay_invoice"] = "1000.00"
        pol.explicit_limits.add("pay_invoice")
        for body, kw in (
                ("Beste,\n\n" + SUMMARY + f"Graag € 425,00 overmaken op {spaced(NEW)}.\n\nPaul", {}),
                ("Beste,\n\n" + SUMMARY + f"Graag het bedrag van € 5.537,75 vandaag overmaken op {NEW} t.n.v. "
                 "Belastingdienst.\n\nPaul", {}),
                (QUOTE.format(NEW), {}),
                (f"Hello,\n\nThe invoice follows; no advance payment is needed, but please pay the EUR 3,480.00 "
                 f"booking fee to {NEW} today.\n\nMaarten", {}),
                (f"Hello,\n\nOur bank details have changed. The invoice follows; no advance payment is needed. Pay "
                 f"EUR 3,480.00 to {KNOWN}.\n\nMaarten", {}),
                (f"Hello,\n\nThe invoice follows after completion. Please pay EUR 3,480.00 to {KNOWN}.\n\nMaarten",
                 {"policy": pol}),
                (f"Hoi,\n\nTotaal factuur: € 9.800,00\n\nAanbetaling € 400,00 graag vandaag op {NEW}.\n\nMark", {})):
            with self.subTest(body=body[-70:]):
                p, v, res = run(body=body, **kw)
                self.assertEqual(v, "blocked", (p["action"], p["amount"], failed(res)))


class AmountDots(unittest.TestCase):
    def test_thousands_dot_does_not_cut_the_verb(self):
        asks = checks.find_pay_asks(f"Maak 12.400,00 vandaag over naar {NEW}.")
        self.assertEqual(asks, {NEW: "Maak 12.400,00 vandaag over"})  # cue shown as written

    def test_strong_ask_beats_weak_no_obligation_words(self):
        for body in (f"Hoi,\n\nMaak 12.400,00 vandaag over naar {NEW}. Geen enkele verplichting voor de klant, wel "
                     "urgent.\n\nPeter",
                     f"Hoi,\n\nMaak € 12.400,00 vandaag over naar {NEW}, het is voor de crowdfunding met de donateurs."
                     "\n\nPeter",
                     f"Hoi,\n\nBoek € 1.250,00 vandaag over naar {NEW}, zonder enige verplichting.\n\nPeter"):
            with self.subTest(body=body[6:40]):
                self.assertEqual(verdict(body, **CEO), "blocked")


class DonationWordsOnStrongAsk(unittest.TestCase):
    def test_set_amount_ask_with_charity_words_blocks(self):
        for body in (f"Hoi,\n\nGraag € 4.750,00 betalen aan {NEW} voor het goede doel, vertrouwelijk.\n\nPeter",
                     f"Hoi,\n\nKun je vandaag € 9.800,00 overmaken naar {NEW}? Een donatie aan de stichting, "
                     "vrijwillig natuurlijk maar wel vandaag.\n\nPeter",
                     f"Hi,\n\nPlease transfer EUR 4,750.00 to {NEW} today, it is an optional donation to our charity "
                     "partner. Keep it quiet.\n\nPeter",
                     f"Hoi,\n\nMaak 12.400,00 over naar {NEW} voor het goede doel.\n\nPeter"):
            with self.subTest(body=body[6:40]):
                self.assertEqual(verdict(body, **CEO), "blocked")

    def test_real_appeals_still_warn(self):
        for body in (f"Beste donateur,\n\nSteun ons werk: doneer vrijwillig op {NEW} t.n.v. Stichting Groen. Elk "
                     "bedrag is welkom.\n\nStichting Groen",
                     f"Beste leden,\n\nWilt u doneren? Maak een vrijwillige gift over op {NEW} t.n.v. Stichting Groen."
                     "\n\nStichting Groen",
                     f"Beste klant,\n\nDit jaar steunen we het goede doel. Wie wil, kan € 25,00 doneren op {NEW}. Geen "
                     "verplichting.\n\nDrukkerij",
                     f"Hi,\n\nOur annual charity drive: please donate EUR 50.00 to {NEW} if you can. It is voluntary."
                     "\n\nThe board",
                     f"Beste vrienden,\n\nU kunt uw gift van € 50,00 overmaken op {NEW} t.n.v. Stichting Groen. Giften "
                     "zijn aftrekbaar (ANBI).\n\nStichting Groen",
                     f"Beste vrienden,\n\nWie wil, kan € 25,00 overmaken op {NEW} voor het goede doel.\n\nStichting "
                     "Groen",
                     f"Hi,\n\nIf you prefer to pay yearly you can pay EUR 1,009.20 to {NEW}. This is optional.\n\nClub"):
            with self.subTest(body=body[:50]):
                self.assertEqual(verdict(body, **CHARITY), "warn")


def card(body, sender="anouk@bakkerij.example", name="Anouk"):
    msg = {"id": "q", "subject": "Bericht", "from": sender, "from_name": name, "date": "2026-11-01", "body": body}
    with tempfile.TemporaryDirectory() as d:
        ws = Workspace(d)
        ws.save("transactions", history())
        ws.save("messages", [msg])
        return shadow.draft_cards(ws, RulePlanner(), checks.Policy(), messages=[msg])[0]


SIG = f"\n\nGroet,\nAnouk\n\n--\nBakkerij\nIBAN {spaced(OWN, False)}\nKvK 81234567"


class OwnAccountDraft(unittest.TestCase):
    def test_own_iban_in_signature_is_no_payment(self):
        c = card("Hoi Erwin,\n\nDe pinomzet was € 38.412, contant € 4.105. De factuur (€ 1.260) staat open." + SIG)
        self.assertEqual((c["action"], c["verdict"]), ("no_action", "none"))

    def test_asked_own_transfer_keeps_its_draft(self):
        c = card(f"Hoi,\n\nGraag € 4.105,00 overmaken naar {OWN}.\n\nAnouk")
        self.assertEqual((c["action"], c["amount"], c["verdict"]), ("pay_invoice", "4105.00", "ok"))

    def test_other_ibans_still_block_on_the_no_action_card(self):
        for body in (f"Hoi,\n\nOnze bankgegevens zijn gewijzigd, nieuw: {NEW}. Factuur € 815,00 staat open." + SIG,
                     f"Hoi,\n\nFactuur € 815,00 staat open, graag naar {NEW}.\n\nOude gegevens: {KNOWN}" + SIG):
            with self.subTest(body=body[6:40]):
                self.assertEqual(card(body)["verdict"], "blocked")


if __name__ == "__main__":
    unittest.main()
