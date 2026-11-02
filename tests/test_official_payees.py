# SPDX-License-Identifier: Apache-2.0
"""C13 official payees (official.py): a message claiming to be the Belastingdienst (or Dienst Toeslagen, DUO) must
use an IBAN from that body's official list. All messages and histories here are made up; the IBANs in the table
are the real official ones, the fraud IBANs are synthetic.
Run: python3 -m unittest tests.test_official_payees -v"""
from __future__ import annotations

import unittest

from countersign_shadow import checks, iban as ibanlib, official

BD_MAIN = "NL04RABO0200112244"      # official: most-used Belastingdienst IBAN since 2026-05-01
BD_OLD = "NL86INGB0002445588"       # official: old ING number, still processed
DUO = "NL45INGB0705001903"          # official: DUO studieschuld
CJIB_M = "NL56INGB0705005100"       # official: CJIB verkeersboete (M), cjib.nl 2026-10-28
FAKE = ibanlib.make_nl("RABO", "0200112245")   # one digit off the real one, valid checksum
MULE = ibanlib.make_nl("INGB", "0006543210")


def decide(payee_name: str, iban: str, from_name: str = "", sender: str = "noreply@mail.example",
           history_iban: str | None = None, extra: str = "") -> tuple[str, dict]:
    body = f"Te betalen: € 1.250,00 op {iban} t.n.v. {payee_name or 'ons'}, kenmerk 2026-88123.{extra}"
    message = {"subject": "Aanslag", "body": body, "from": sender, "from_name": from_name, "date": "2026-10-24",
               "pdfs": []}
    proposal = {"action": "pay_invoice", "amount": "1250.00", "currency": "EUR",
                "payee": {"name": payee_name, "iban": iban, "email": ""}, "invoice_ref": "2026-88123",
                "order_ref": "", "evidence_quote": body, "rationale": ""}
    history = [] if not history_iban else [
        {"amount": "-80.00", "currency": "EUR", "date": "2026-03-01", "iban": history_iban,
         "counterparty": payee_name, "email": "", "description": "", "reference": "", "status": ""}]
    policy = checks.Policy(limits={**checks.Policy().limits, "pay_invoice": "5000.00"})
    result = checks.evaluate(proposal, message, history, policy)
    return checks.verdict("pay_invoice", result), {c.rule[:3]: c for c in result}


class TestOfficialTable(unittest.TestCase):
    def test_every_iban_has_a_valid_checksum_and_an_official_source(self):
        for p in official.OFFICIAL_PAYEES:
            self.assertTrue(p.source.startswith(("https://www.belastingdienst.nl/", "https://duo.nl/",
                                                 "https://www.cjib.nl/")), p.source)
            self.assertRegex(p.checked, r"^\d{4}-\d{2}-\d{2}$")
            for ib in p.ibans:
                with self.subTest(payee=p.key, iban=ib):
                    self.assertTrue(ibanlib.is_valid(ib))

    def test_belastingdienst_list_matches_the_official_page(self):
        bd = next(p for p in official.OFFICIAL_PAYEES if p.key == "belastingdienst")
        self.assertEqual(sum("RABO" in i for i in bd.ibans), 8)   # 8 Rabobank numbers on the page, 2026-10-27
        self.assertEqual(sum("INGB" in i for i in bd.ibans), 27)  # 27 old ING numbers on the page
        self.assertTrue(bd.complete)
        self.assertIn(BD_MAIN, bd.ibans)
        self.assertIn(BD_OLD, bd.ibans)

    def test_mere_mention_in_body_is_not_a_claim(self):
        self.assertEqual(official.claimed_by(["Boekhoudkantoor Smit"], "smit-boekhouding.example",
                                             "Ik heb je aangifte bij de Belastingdienst ingediend."), [])

    def test_claims_by_name_domain_and_tnv_line(self):
        for names, dom, text in [(["Belastingdienst"], "", ""), (["BELASTING DIENST"], "", ""),
                                 ([""], "belasting-dienst-nl.example", ""),
                                 ([""], "x.example", "Graag overmaken t.n.v. Belastingdienst Apeldoorn")]:
            with self.subTest(names=names, dom=dom):
                self.assertEqual([p.key for p in official.claimed_by(names, dom, text)], ["belastingdienst"])

    def test_duo_claim_is_narrow(self):
        self.assertEqual([p.key for p in official.claimed_by(["DUO"])], ["duo"])
        self.assertEqual([p.key for p in official.claimed_by(["Dienst Uitvoering Onderwijs"])], ["duo"])
        self.assertEqual(official.claimed_by(["Duo Design Studio"]), [])


class TestC13(unittest.TestCase):
    def test_fake_belastingdienst_iban_blocks_with_phone_number(self):
        v, c = decide("Belastingdienst", FAKE, from_name="Belastingdienst")
        self.assertFalse(c["C13"].ok)
        self.assertEqual(c["C13"].severity, "block")
        self.assertEqual(v, "blocked")
        self.assertIn("0800 - 0543", c["C13"].uitleg)
        self.assertIn("IBAN-Naam-Check", c["C13"].uitleg)
        self.assertIn(official.BD_LIST_URL, c["C13"].uitleg)

    def test_mule_account_with_plausible_name_still_blocks(self):
        # the VoP gap: the payer types the name the fraudster gave ("J. de Vries") and the bank says "match";
        # the sender still claims to be the Belastingdienst
        v, c = decide("J. de Vries", MULE, from_name="Belastingdienst Inning",
                      sender="inning@belastingdienst-betalen.example")
        self.assertFalse(c["C13"].ok)
        self.assertEqual(v, "blocked")

    def test_even_a_previously_paid_fake_iban_blocks(self):
        # you paid this fake "Belastingdienst" IBAN once before: C5 passes, C13 still blocks
        v, c = decide("Belastingdienst", FAKE, history_iban=FAKE)
        self.assertTrue(c["C5 "].ok)
        self.assertFalse(c["C13"].ok)
        self.assertEqual(v, "blocked")

    def test_real_iban_reassures_and_counts_as_known(self):
        for ib in (BD_MAIN, BD_OLD, "NL04RABO0200144448"):
            with self.subTest(iban=ib):
                v, c = decide("Belastingdienst", ib, from_name="Belastingdienst")
                self.assertTrue(c["C13"].ok)
                self.assertIn("officiële lijst van Belastingdienst", c["C13"].goed)
                self.assertTrue(c["C5 "].ok)  # first payment to the real tax office is not "never paid before"
                self.assertEqual(v, "ok")
        _, c = decide("Belastingdienst", BD_OLD)
        self.assertIn("oud ING-rekeningnummer", c["C13"].goed)

    def test_official_iban_without_claim_still_gets_reassurance(self):
        _, c = decide("Mijn Boekhouder", BD_MAIN, from_name="Boekhoudkantoor Smit")
        self.assertTrue(c["C13"].ok)
        self.assertIn("officieel rekeningnummer van Belastingdienst", c["C13"].goed)

    def test_no_claim_and_no_official_iban_means_no_c13(self):
        _, c = decide("Jansen Bouw BV", MULE, from_name="Jansen Bouw")
        self.assertNotIn("C13", c)

    def test_duo_mismatch_is_a_warning_not_a_block(self):
        # DUO's page is not presented as a complete list, so a mismatch warns
        v, c = decide("DUO", MULE, from_name="DUO", history_iban=MULE)
        self.assertFalse(c["C13"].ok)
        self.assertEqual(c["C13"].severity, "warn")
        self.assertEqual(v, "warn")
        _, c = decide("Dienst Uitvoering Onderwijs", DUO)
        self.assertTrue(c["C13"].ok)

    def test_cjib_list_matches_the_official_page(self):
        cj = next(p for p in official.OFFICIAL_PAYEES if p.key == "cjib")
        self.assertEqual(len(cj.ibans), 4)   # 4 numbers (M, O, Z, S) on cjib.nl, 2026-10-28
        self.assertFalse(cj.complete)        # page does not present the list as complete
        self.assertIn(CJIB_M, cj.ibans)

    def test_fake_cjib_fine_warns_and_real_one_reassures(self):
        v, c = decide("CJIB", MULE, from_name="CJIB Leeuwarden", sender="boete@cjib-betaling.example")
        self.assertFalse(c["C13"].ok)
        self.assertEqual(c["C13"].severity, "warn")
        _, c = decide("Centraal Justitieel Incassobureau", CJIB_M)
        self.assertTrue(c["C13"].ok)
        self.assertEqual([p.key for p in official.claimed_by([""], "cjib-betaling.example", "")], ["cjib"])

    def test_belastingdienst_iban_for_a_duo_claim_is_a_mismatch(self):
        _, c = decide("DUO", BD_MAIN, from_name="DUO")
        self.assertFalse(c["C13"].ok)

    def test_toeslagen_on_shared_main_iban_is_ok(self):
        _, c = decide("Dienst Toeslagen", BD_MAIN, from_name="Dienst Toeslagen")
        self.assertTrue(c["C13"].ok)

    def test_reassurance_reaches_the_card(self):
        from countersign_shadow import web
        _, c = decide("Belastingdienst", BD_MAIN)
        card = {"id": "c-1", "action": "pay_invoice", "action_nl": "Factuur betalen", "subject": "s", "from": "f",
                "date": "d", "planner": "rules", "verdict": "ok", "verdict_nl": "ok", "amount": "1250.00",
                "payee": {}, "rationale": "", "checks": [x.as_dict() for x in c.values()]}
        html = web.render_card(card, None, None, "tok")
        self.assertIn('class="reason g"', html)
        self.assertIn("officiële lijst", html)


if __name__ == "__main__":
    unittest.main()
