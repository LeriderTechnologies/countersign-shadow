# SPDX-License-Identifier: Apache-2.0
"""Over-breadth probes (written by a reviewer, not from any held-out set): fraud shapes that abuse each rule
the held-out fixes loosened. Each must still be stopped (blocked); a larger-than-usual invoice to the KNOWN IBAN with a reason
warns, as designed. P3 (crowdfunding / "geen enkele verplichting" next to a CEO-fraud ask) and P8 (a pay ask on a
"weekomzet"-labelled amount) slipped through at 3ab4b35 and are fixed in checks.py."""
from __future__ import annotations

import unittest

try:
    from tests.test_heldout_regressions import KNOWN, NEW, NEW2, failed, run, spaced
except ImportError:  # python3 -m unittest discover -s tests
    from test_heldout_regressions import KNOWN, NEW, NEW2, failed, run, spaced

CEO = dict(sender="ceo.janssen@gmail.com", name="Peter Janssen")


class QaProbes(unittest.TestCase):
    def blocked(self, **kw):
        p, v, res = run(**kw)
        self.assertEqual(v, "blocked", (p["action"], failed(res)))
        return p, res

    def test_reason_with_new_iban(self):
        self.blocked(body="Beste klant,\n\nDit is de jaarafrekening, hoger dan de gewone kwartaalnota vanwege extra "
                          f"werkzaamheden. Graag € 2.480,00 overmaken naar {NEW} t.n.v. Drukkerij Bladwijzer.\n\nMark")

    def test_reason_new_iban_in_body_known_in_quoted_thread(self):
        self.blocked(body="Hoi,\n\nVanwege de seizoensdrukte is deze factuur hoger dan normaal: € 3.150,00. Wil je dit "
                          f"deze keer overmaken naar {NEW}?\n\nGroet, Mark\n\n> Op 1 okt schreef Drukkerij Bladwijzer:\n"
                          f"> Factuur € 815,00, graag naar {KNOWN}.")

    def test_reason_new_iban_then_known_named(self):
        self.blocked(body="Hoi,\n\nDit is een eenmalige projectfactuur, hoger dan normaal: € 3.150,00. Betaal deze graag "
                          f"op {NEW}. Normaal gebruiken we {KNOWN} maar die even niet.\n\nGroet, Mark")

    def test_reason_known_but_bank_change_in_quoted_thread(self):
        self.blocked(body=f"Hoi,\n\nDe jaarfactuur is hoger dan gewoonlijk: € 4.200,00, graag naar {KNOWN}.\n\n"
                          f"> Eerder bericht: onze bankgegevens zijn gewijzigd, nieuw: {NEW}")

    def test_crowdfunding_and_no_obligation_on_ceo_fraud(self):
        self.blocked(**CEO, body=f"Hoi,\n\nKun je vandaag nog € 9.800,00 overmaken naar {NEW} t.n.v. Stichting Groei? "
                                 "Het is voor onze crowdfunding campagne met de donateurs, vertrouwelijk. Geen enkele "
                                 "verplichting voor de klant maar wel urgent voor ons.\n\nPeter")

    def test_campaign_words_with_strong_ask(self):
        for w in ("crowdfundingactie", "donateurs", "donors"):
            with self.subTest(w=w):
                self.blocked(**CEO, subject="Factuur", body=f"Hoi,\n\nFactuur {w}: graag € 4.750,00 betalen aan {NEW} "
                                                            "t.n.v. Event Partners, kenmerk SP-2211.\n\nPeter")

    def test_refund_done_plus_release_fee(self):
        self.blocked(sender="support@lumenlamps-refunds.example", name="Lumen Lamps",
                     body="Dear customer,\n\nYour refund of €486.50 for order LL-77120 has been processed. To release "
                          f"it, a verification fee of €24.95 must be paid to {NEW} (ref LL-77120).\n\nRefund team")

    def test_nl_refund_done_plus_passive_fee_spaced_iban(self):
        self.blocked(sender="klantenservice@hubo-retour.example", name="Hubo",
                     body="Beste klant,\n\nWij hebben het bedrag van € 312,00 voor bestelling 402555 teruggestort. Om "
                          "de terugbetaling vrij te geven kunnen de administratiekosten van € 19,95 worden betaald op "
                          f"{spaced(NEW)} o.v.v. 402555.\n\nKlantenservice")

    def test_spaced_iban_in_bank_change_mail(self):
        self.blocked(sender="info@drukkerij-bladwijzer.example",
                     body="Beste klant,\n\nOnze betaalinstructies zijn gewijzigd. Factuur 2026-311 van € 790,00 graag "
                          f"naar {spaced(NEW)} t.n.v. Drukkerij Bladwijzer.\n\nMark")

    def test_kenmerk_block_with_new_iban(self):
        self.blocked(body="Beste klant,\n\nHet openstaande bedrag van € 845,00 mag op onderstaande rekening worden "
                          f"voldaan.\n\nBladwijzer Holding\n{spaced(NEW)}\nKenmerk: 2026-0412\n\nMark")

    def test_report_label_on_a_pay_ask(self):
        for w in ("weekomzet", "omzet", "dagomzet", "pinomzet"):
            with self.subTest(w=w):
                self.blocked(sender="boekhouder.smit@gmail.com", name="Smit",
                             body=f"Hoi,\n\nMaak de {w} van € 12.400,00 vandaag over naar de tussenrekening {NEW}, "
                                  "dan regel ik de btw.\n\nSmit")

    def test_account_switched_with_no_action_wording(self):
        self.blocked(sender="ar@cloudnest-hosting.example", name="CloudNest",
                     body="Remittance advice\n\nOur collection account has now switched. No further action is required "
                          f"for past invoices; open items of EUR 1,240.00 go to {NEW}.\n\nAccounts receivable")

    def test_excl_line_with_new_iban(self):
        p, _res = self.blocked(body="Factuur 2026-990\nSubtotaal excl. btw: € 9.000,00\nBtw 21%: € 1.890,00\n"
                                    f"Totaal: € 10.890,00\n\nTe betalen op {NEW}.\n\nMark")
        self.assertEqual(str(p["amount"]), "10890.00")

    def test_known_iban_with_reason_warns(self):  # designed: C3 warns, the money goes to the known account
        _p, v, res = run(body=f"Factuur project website: € 6.400,00, graag overmaken naar {KNOWN}.\n\nMark")
        self.assertEqual(v, "warn")
        self.assertEqual(failed(res), {"C3": "warn"})

    def test_reported_figure_without_ask_stays_a_report(self):
        _p, v, _res = run(sender="eigenaar@bladwijzer.example", name="Eigenaar",
                          body="Weekoverzicht\n\nDe weekomzet was € 12.400,00, iets hoger dan vorige week.\n\nGroet")
        self.assertNotEqual(v, "blocked")


class SecondRoundProbes(unittest.TestCase):
    """Second round (new variants, not from any held-out set). Weak-tier campaign/optional words next to other strong ask
    forms; a reported-figure label with a move verb ("zet .. over", "move .. to", "stuur .. door", "send .. to") that
    was silent at c621dca, plus guards that a report or the sender's own transfer stays quiet."""
    def verdict(self, body, **kw):
        _p, v, res = run(body=body, **kw)
        return v, failed(res)

    def test_weak_tier_words_with_other_strong_asks(self):
        for body in (f"Hoi,\n\nWil je vandaag € 7.300,00 overmaken naar {NEW}? Er is geen enkele verplichting voor de "
                     "klant, maar het moet wel vandaag.\n\nPeter",
                     f"Hoi,\n\nBetaal aub € 3.980,00 aan {NEW} t.n.v. Event Partners BV. Dit is zonder enige "
                     "verplichting voor onze partners.\n\nPeter",
                     f"Hi,\n\nPlease transfer EUR 5,600.00 to {NEW} today for our donors dinner.\n\nPeter",
                     f"Hoi,\n\nDe factuur van de crowdfundingcampagne van € 6.250,00 dient vandaag te worden betaald "
                     f"op {spaced(NEW)}.\n\nPeter"):
            with self.subTest(body=body[:40]):
                self.assertEqual(self.verdict(body, **CEO)[0], "blocked")

    def test_report_label_with_move_verb(self):
        for body in (f"Hoi,\n\nZet de dagomzet van € 4.100,00 vandaag over naar {NEW}.\n\nSmit",
                     f"Hi,\n\nMove the daily revenue of € 4.100,00 to {NEW} today.\n\nThanks",
                     f"Hoi,\n\nStuur de pinomzet van € 4.100,00 vandaag door naar {NEW}.\n\nSmit",
                     f"Hi,\n\nSend the week turnover of € 12.400,00 to {NEW} today.\n\nThanks",
                     f"Hoi,\n\nGraag de kasomzet van € 2.310,00 overzetten naar {NEW}.\n\nSmit"):
            with self.subTest(body=body[:40]):
                self.assertEqual(self.verdict(body, sender="boekhouder.smit@gmail.com", name="Smit")[0], "blocked")

    def test_report_label_guards(self):
        kw = dict(sender="eigenaar@bakkerij-vos.example", name="Eigenaar")
        for body in (f"Weekoverzicht\n\nWe zetten de weekomzet van € 12.400,00 morgen over naar {NEW}.\n\nGroet",
                     f"Weekoverzicht\n\nIk maak de weekomzet van € 12.400,00 morgen over naar {NEW}.\n\nGroet",
                     f"Weekoverzicht\n\nDe weekomzet van € 12.400,00 steeg naar een record, zie {NEW}.\n\nGroet",
                     f"Weekoverzicht\n\nDe pinomzet van € 12.400,00 is gisteren bijgeschreven op {NEW}.\n\nGroet"):
            with self.subTest(body=body[14:50]):
                self.assertNotEqual(self.verdict(body, **kw)[0], "blocked")
        v, f = self.verdict(f"Hoi,\n\nZet de dagomzet van € 4.100,00 vandaag over naar {KNOWN}.\n\nSmit",
                            sender="boekhouder.smit@gmail.com", name="Smit")
        self.assertNotEqual(v, "blocked", f)


class ThirdRoundProbes(unittest.TestCase):
    """Third round (new variants, not from any held-out set): a move verb keeps a reported-figure anchor only when it
    asks (imperative, request word, bare infinitive), not in a third-person payout notice."""
    def verdict(self, body, **kw):
        return run(body=body, **kw)[1]

    def test_third_person_move_notice_is_no_ask(self):
        kw = dict(sender="payouts@adyen.example", name="Adyen")
        for body in (f"Beste klant,\n\nAdyen zet de pinomzet van € 4.100,00 morgen over naar {NEW2}.\n\nAdyen",
                     f"Hello,\n\nAdyen will move the daily revenue of EUR 4,100.00 to {NEW2} tomorrow.\n\nAdyen",
                     f"Beste klant,\n\nDe bank gaat de omzet van € 18.250,00 doorboeken naar {NEW2}, uw "
                     "spaarrekening.\n\nAdyen"):
            with self.subTest(body=body[14:50]):
                self.assertNotEqual(self.verdict(body, **kw), "blocked")

    def test_move_asks_still_block(self):
        kw = dict(sender="boekhouder.smit@gmail.com", name="Smit")
        for body in (f"Hi,\n\nPlease sweep the daily revenue of EUR 4,100.00 into {NEW} today.\n\nSmit",
                     f"Hoi Mark, zet de dagomzet van € 4.100,00 vandaag over naar {NEW}.\n\nSmit",
                     f"Hoi,\n\nKun je de kasomzet van € 2.310,00 vandaag doorstorten naar {spaced(NEW)}?\n\nSmit",
                     f"Hoi,\n\nVandaag nog de dagomzet van € 4.100,00 overzetten naar {NEW}.\n\nSmit",
                     f"Hi,\n\nMark, could you move the weekly revenue of EUR 9,870.00 to {NEW} today?\n\nSmit",
                     f"Geachte,\n\nWij vragen u de omzet van € 8.900,00 over te zetten naar {NEW}.\n\nSmit"):
            with self.subTest(body=body[:40]):
                self.assertEqual(self.verdict(body, **kw), "blocked")


if __name__ == "__main__":
    unittest.main()
