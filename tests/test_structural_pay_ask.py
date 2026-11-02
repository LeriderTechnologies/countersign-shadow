# SPDX-License-Identifier: Apache-2.0
"""Regression tests: the STRUCTURAL pay-ask check (checks.find_structural_asks).

An IBAN you never paid, that never paid you and that is not your own account, with an amount or a due date next to
it, blocks in C14 even without a pay verb ("should be credited to", "Bank:/Account:/Amount:" blocks, "het restant
van 95 euro graag op X", "future invoices are paid into X"). Exempt: source IBANs, the reader's own account
("uw rekening X"), signature/footer lines, settled or sender-pays sentences. Known IBANs, own accounts and official
payee IBANs are never blocked by it (the history check runs first).
"""
from __future__ import annotations

import contextlib
import io
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

from countersign_shadow import checks, iban as ibanlib, samples
from countersign_shadow.__main__ import demo_workspace
from countersign_shadow.planners import normalise_proposal

NEW = ibanlib.make_nl("TRIO", "0555123456")


def sp(i):
    return " ".join(i[k:k + 4] for k in range(0, len(i), 4))


X = sp(NEW)


class Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        with contextlib.redirect_stdout(io.StringIO()):
            cls.ws = demo_workspace(Path(cls._tmp.name) / "ws")
        cls.history = cls.ws.transactions
        cls.known = next(t["iban"] for t in cls.history if Decimal(t["amount"]) < 0 and t["iban"])

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def sev(self, body, action="send_reminder"):
        msg = {"id": "q", "subject": "Factuur A-1", "from": "info@nieuw.example", "from_name": "Atelier Nieuw",
               "date": "2026-10-25", "body": body}
        p = normalise_proposal("q", {"action": action, "amount": "95.00",
                                     "payee": {"name": "Atelier Nieuw", "iban": "", "email": "info@nieuw.example"},
                                     "invoice_ref": "A-1", "evidence_quote": body[:20]})
        bad = [c for c in checks.evaluate(p, msg, self.history, checks.Policy())
               if c.rule.startswith("C14") and not c.ok]
        return "block" if any(c.severity == "block" for c in bad) else ("warn" if bad else "ok")


class TestStructuralAsksBlock(Base):
    def test_verbless_and_passive_asks_block(self):
        for body in (
                f"The invoice amount should be credited to account {X} within 14 days.",
                f"Kindly process the attached invoice.\n\nBank: Trionos\nAccount: {X}\nAmount: EUR 95.00",
                f"Het restant van 95 euro graag op {X}, dan is alles verrekend.",
                f"Graag ontvangen wij het bedrag van 95 euro op {X}; alvast bedankt.",
                f"Our accounts team asks that all future invoices are paid into {X}.",
                f"We'd appreciate it if the funds could be sent to {X} at your earliest convenience.",
                f"Factuur A-1\nIBAN: {X}\nVervalt: 05-11-2026",
                f"Totaal EUR 95,00 | IBAN {X} | KvK 12345678",
                f"Groet, Jan\n\nPS: het bedrag van 95 euro graag nog deze week op {X} zetten.",
        ):
            for action in ("send_reminder", "send_invoice", "refund", "payout"):
                with self.subTest(body=body, action=action):
                    self.assertEqual(self.sev(body, action), "block")

    def test_find_structural_asks_names_the_anchor(self):
        self.assertEqual(checks.find_structural_asks(f"Bedrag: EUR 95,00\nIBAN: {X}"), {NEW: "EUR 95,00"})


class TestStructuralAsksDoNotBlockLegit(Base):
    def test_known_own_and_official_ibans_pass(self):
        own = sp(samples.OWN)
        bd = "NL86 INGB 0002 4455 88"
        for body in (f"Factuur A-1: totaal EUR 95,00, binnen 14 dagen op {sp(self.known)}.",
                     f"Factuur 2026-014\nBedrag: EUR 95,00\nIBAN: {own}\nVervaldatum: 05-11-2026",
                     f"Voor de aanslag van EUR 95 t.n.v. Belastingdienst op {bd} uiterlijk 30-11-2026; zie de brief.",
                     f"Met vriendelijke groet,\nJan\nIBAN {own}\n\nUw factuur van EUR 95 staat klaar."):
            with self.subTest(body=body):
                self.assertNotEqual(self.sev(body), "block")

    def test_signature_and_footer_ibans_do_not_count(self):
        for body in (f"Je bestelling van EUR 95 is vandaag verzonden.\n\nMet vriendelijke groet,\nShop Noord\nIBAN {X}",
                     f"Order total EUR 95, shipped today.\n\nRegards,\nAnna\n--\nAnna Design | IBAN {X}",
                     f"De offerte van EUR 950 volgt morgen.\nPiet | KvK 12345678 | IBAN {X}"):
            with self.subTest(body=body):
                self.assertNotEqual(self.sev(body), "block")

    def test_reader_account_source_and_settled_sentences_do_not_count(self):
        for body in (f"Uw terugbetaling van EUR 95 wordt binnen 5 dagen gestort op uw rekening {X}.",
                     f"Your refund of EUR 95 will be credited to your account {X} within 5 days.",
                     f"Wij hebben de terugbetaling van 95 euro vandaag overgemaakt naar {X}.",
                     f"Ik maak de 95 euro morgen over. Mijn IBAN is {X}.",
                     f"We received EUR 95 from {X} on 25-10-2026, thank you.",
                     f"Bedankt, het bedrag van EUR 95 is betaald. Onze gegevens: {X}.",
                     f"> Het bedrag van 95 euro graag op {X}\nDank, is geregeld."):
            with self.subTest(body=body):
                self.assertNotEqual(self.sev(body), "block")

    def test_bare_iban_without_amount_or_date_still_only_warns(self):
        self.assertEqual(self.sev(f"Ons rekeningnummer is {X}."), "warn")
        self.assertEqual(self.sev(f"Giften zijn welkom op {X}."), "warn")


if __name__ == "__main__":
    unittest.main()
