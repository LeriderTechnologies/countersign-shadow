# SPDX-License-Identifier: Apache-2.0
"""Regression tests.

- The user's own IBAN is read from the bank exports ("Rekening" / "IBAN/BBAN" / account column, CAMT.053
Stmt/Acct, MT940 :25:) and counts as known in the IBAN history checks, automatically on import.
- C14's "the message asks the reader to pay" is a proximity rule per IBAN (checks.find_pay_asks) instead of
an earlier grab-bag regex.
"""
from __future__ import annotations

import contextlib
import io
import tempfile
import unittest
from pathlib import Path

from countersign_shadow import checks, iban as ibanlib, samples
from countersign_shadow.__main__ import demo_workspace
from countersign_shadow.importers import transactions as tx
from countersign_shadow.planners import normalise_proposal

NEW = ibanlib.make_nl("TRIO", "0555123456")
OWN2 = ibanlib.make_nl("ASNB", "0123456789")


def sp(i):
    return " ".join(i[k:k + 4] for k in range(0, len(i), 4))


class TestOwnIbanFromBankExports(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def _write(self, name, content, enc="utf-8"):
        p = self.tmp / name
        p.write_text(content, encoding=enc)
        return p

    def test_ing_and_rabo_sample_exports_carry_own_iban(self):
        src = samples.write_samples(self.tmp / "s")
        _, ing = tx.import_file(src / "ing.csv")
        _, rabo = tx.import_file(src / "rabobank.csv")
        _, pp = tx.import_file(src / "paypal.csv")
        self.assertEqual({r["own_iban"] for r in ing}, {samples.OWN})
        self.assertEqual({r["own_iban"] for r in rabo}, {ibanlib.make_nl("RABO", "0300000001")})
        self.assertEqual({r["own_iban"] for r in pp}, {""})

    def test_own_iban_does_not_change_transaction_ids(self):
        a = tx._tx("ing", "2026-10-01", "-1.00", "EUR", "X", NEW)
        b = tx._tx("ing", "2026-10-01", "-1.00", "EUR", "X", NEW, own_iban=samples.OWN)
        self.assertEqual(a["id"], b["id"])
        self.assertEqual((a["own_iban"], b["own_iban"]), ("", samples.OWN))

    def test_invalid_own_account_is_ignored(self):
        self.assertEqual(tx._tx("ing", "2026-10-01", "-1.00", "EUR", own_iban="NL00INGB0000000000")["own_iban"], "")
        self.assertEqual(tx._tx("ing", "2026-10-01", "-1.00", "EUR", own_iban="456123000")["own_iban"], "")

    def test_camt053_statement_account(self):
        xml = f"""<?xml version="1.0"?><Document xmlns="urn:iso:std:iso:20022:tech:xsd:camt.053.001.02"><BkToCstmrStmt>
<Stmt><Acct><Id><IBAN>{OWN2}</IBAN></Id></Acct><Ntry><Amt Ccy="EUR">10.00</Amt><CdtDbtInd>DBIT</CdtDbtInd>
<BookgDt><Dt>2026-10-01</Dt></BookgDt></Ntry></Stmt></BkToCstmrStmt></Document>"""
        _, rows = tx.import_file(self._write("s.xml", xml))
        self.assertEqual(rows[0]["own_iban"], OWN2)

    def test_mt940_account_tag(self):
        for tag25 in (f"{OWN2}EUR", f"ASNBNL21/{OWN2}", OWN2):
            with self.subTest(tag25=tag25):
                content = (f":20:S1\n:25:{tag25}\n:28C:1/1\n:60F:C261001EUR1000,00\n:61:2610010101D19,00NTRFNONREF\n"
                           f":86:IBAN: {NEW} Naam: X Omschrijving: Y\n:62F:C261002EUR981,00\n")
                _, rows = tx.import_file(self._write("s.sta", content))
                self.assertEqual(rows[0]["own_iban"], OWN2)

    def test_volksbank_je_rekening(self):
        content = ("Datum;Je rekening;Van / naar;Naam;Bedrag bij/af;Omschrijving\n"
                   f"01-10-2026;{OWN2};{NEW};X;-10,00;Y\n")
        _, rows = tx.import_file(self._write("v.csv", content), fmt="volksbank")
        self.assertEqual(rows[0]["own_iban"], OWN2)


class Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        with contextlib.redirect_stdout(io.StringIO()):
            cls.ws = demo_workspace(Path(cls._tmp.name) / "ws")
        cls.history = cls.ws.transactions

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def c14(self, body, action="send_reminder", history=None, payee_iban=""):
        msg = {"id": "q", "subject": "Factuur A-1", "from": "info@nieuw.example", "from_name": "Atelier Nieuw",
               "date": "2026-10-25", "body": body}
        p = normalise_proposal("q", {"action": action, "amount": "95.00",
                                     "payee": {"name": "Atelier Nieuw", "iban": payee_iban, "email": "info@nieuw.example"},
                                     "invoice_ref": "A-1", "evidence_quote": body[:20]})
        res = checks.evaluate(p, msg, self.history if history is None else history, checks.Policy())
        return [c for c in res if c.rule.startswith("C14")]

    def sev(self, body, action="send_reminder", **kw):
        bad = [c for c in self.c14(body, action, **kw) if not c.ok]
        return "block" if any(c.severity == "block" for c in bad) else ("warn" if bad else "ok")


class TestOwnIbanIsKnown(Base):
    def test_demo_workspace_knows_both_own_accounts(self):
        self.assertEqual(checks.own_accounts(self.history), {samples.OWN, ibanlib.make_nl("RABO", "0300000001")})

    def test_pay_ask_to_own_account_passes_on_every_label(self):
        for action in ("send_reminder", "send_invoice", "refund", "payout"):
            with self.subTest(action=action):
                self.assertEqual(self.sev(f"Graag overmaken naar {sp(samples.OWN)}.", action), "ok")

    def test_pay_invoice_to_own_account_is_known_in_c5(self):
        msg = {"id": "q", "subject": "x", "from": "a@b.example", "from_name": "Spaar", "date": "2026-10-25",
               "body": f"Overboeking naar spaarrekening {samples.OWN}, 95,00."}
        p = normalise_proposal("q", {"action": "pay_invoice", "amount": "95.00",
                                     "payee": {"name": "Spaar", "iban": samples.OWN}})
        c5 = [c for c in checks.evaluate(p, msg, self.history, checks.Policy()) if c.rule.startswith("C5")]
        self.assertTrue(c5[0].ok and "own account" in c5[0].detail)

    def test_c14_fail_safe_intact_for_unknown_iban(self):
        self.assertEqual(self.sev(f"Graag overmaken naar {sp(NEW)}."), "block")


class TestProximityPayAsk(Base):
    def test_cue_far_from_iban_does_not_count(self):
        filler = "Wij hopen dat alles goed gaat met het project en kijken uit naar de oplevering in december. " * 2
        self.assertEqual(self.sev(f"Graag overmaken zoals altijd. {filler}Ons nummer {sp(NEW)} voor de volledigheid."),
                         "warn")
        self.assertEqual(self.sev(f"Graag overmaken naar {sp(NEW)}."), "block")

    def test_quoted_lines_are_skipped(self):
        self.assertEqual(self.sev(f"> Gelieve te voldoen op {sp(NEW)}.\nDank, ik kijk ernaar."), "warn")
        self.assertEqual(self.sev(f"Gelieve te voldoen op {sp(NEW)}.\n> Eerder bericht."), "block")

    def test_first_person_payer_statements(self):
        for body in (f"Wij hebben betaald vanaf {sp(NEW)}.", f"Ik betaal morgen naar uw rekening; de mijne is {sp(NEW)}.",
                     f"Betaal ik morgen, rekening {sp(NEW)}.", f"I'll pay you tomorrow, my account is {sp(NEW)}.",
                     f"Our payment details: {sp(NEW)}. We'll pay your invoice Friday."):
            with self.subTest(body=body):
                self.assertNotEqual(self.sev(body, "send_invoice"), "block")

    def test_second_person_asks_block(self):
        for body in (f"Wij verzoeken u het bedrag te betalen op {sp(NEW)}.", f"You can pay to {sp(NEW)}.",
                     f"Please pay to {sp(NEW)}.", f"Our bank details: {sp(NEW)}. Due within 7 days.",
                     f"Bedrag graag op {sp(NEW)} t.n.v. Atelier Nieuw."):
            with self.subTest(body=body):
                self.assertEqual(self.sev(body), "block")

    def test_source_iban_is_not_a_destination(self):
        for body in (f"Betaling ontvangen van rekening {sp(NEW)}.", f"The invoice was paid from {sp(NEW)}.",
                     f"Ter info: de betaling is voldaan, afzender {sp(NEW)}."):
            with self.subTest(body=body):
                self.assertEqual(self.sev(body, "send_invoice"), "warn")

    def test_other_channel_is_not_an_ask_to_the_iban(self):
        self.assertEqual(self.sev(f"Onze rekening {sp(NEW)}. Betaal voortaan met iDEAL."), "warn")
        self.assertEqual(self.sev(f"Our IBAN {sp(NEW)}. Pay by card at the counter."), "warn")

    def test_refund_wording_never_downgrades_an_ask_to_a_never_seen_iban(self):
        # QA FIX: these all used to drop to a warn.
        for body, action in ((f"To process your refund, please pay the EUR 95 handling fee to {sp(NEW)}.", "refund"),
                             (f"Om uw terugbetaling te ontvangen vragen wij u 95 euro over te maken naar {sp(NEW)}.",
                              "refund"),
                             (f"Retour: graag het bedrag overmaken op {sp(NEW)}.", "send_reminder"),
                             (f"Please refund-transfer the 95 euro deposit to our new account {sp(NEW)}.", "refund"),
                             (f"Kun je de refund betalen? Mijn rekening is {sp(NEW)}.", "refund")):
            with self.subTest(body=body):
                self.assertEqual(self.sev(body, action), "block")

    def test_refund_to_known_or_sender_source_account_passes(self):
        # A refund to the account the customer paid you from (in your history) is fine; a refund you SENT is no ask.
        self.assertEqual(self.sev(f"Kun je de refund betalen naar {sp(samples.CAFE)}?", "refund"), "ok")
        self.assertEqual(self.sev(f"Please transfer the refund to {sp(samples.CAFE)}.", "refund"), "ok")
        self.assertNotEqual(self.sev(f"Wij hebben de terugbetaling overgemaakt naar uw rekening {sp(NEW)}.", "refund"),
                            "block")
        self.assertNotEqual(self.sev(f"I sent the refund to {sp(NEW)} yesterday.", "refund"), "block")

    def test_invalid_checksum_with_nearby_ask_blocks(self):
        self.assertEqual(self.sev("Graag overmaken naar NL00 INGB 0000 0000 00."), "block")
        self.assertEqual(self.sev("Graag overmaken. " + "x " * 60 + "Ref NL00 INGB 0000 0000 00."), "ok")

    def test_find_pay_asks_returns_cue_per_iban(self):
        asks = checks.find_pay_asks(f"Stuur het geld naar {sp(NEW)}. " + "Verder alles goed hier " * 5 +
                                    f"en ons kantoor heeft ook nog {sp(samples.OWN)}.")
        self.assertIn(NEW, asks)
        self.assertNotIn(samples.OWN, asks)


if __name__ == "__main__":
    unittest.main()
