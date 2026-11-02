# SPDX-License-Identifier: Apache-2.0
"""Regression tests: the code checks block on FACTS of the message, whatever action label a planner picks.

A local-model run (Qwen3-4B planner) found two unsafe drafts that passed:
  * 04-nieuwe-iban-fraude: the model drafted send_reminder/warn, so the IBAN checks (pay_invoice only) never ran.
  * 16-dubbele-factuur: the model had labelled 01 (same invoice) as send_reminder, so C11 (whose key includes the
    action) did not see the repeat, and C6 only looks at payments already made.
Fixes: C14 (every IBAN in the message, every outgoing action) and C15 (same payment requested in an earlier message).
Run: python3 -m unittest discover -s tests -v
"""
from __future__ import annotations

import contextlib
import io
import tempfile
import unittest
from pathlib import Path

from countersign_shadow import checks, iban as ibanlib, samples
from countersign_shadow.__main__ import demo_workspace
from countersign_shadow.planners import normalise_proposal
from countersign_shadow.planners.llm import extract_json
from countersign_shadow.shadow import draft_cards

# Saved raw replies of the Qwen3-4B planner, verbatim fields.
QWEN_01 = """{"action": "send_reminder", "amount": "312.40", "currency": "EUR",
 "payee": {"name": "Kartonfabriek Noord BV", "iban": "NL20 INGB 0001 2345 67",
           "email": "debiteuren@kartonfabriek-noord.example"},
 "invoice_ref": "F-2026-0412", "order_ref": "",
 "evidence_quote": "Volgens onze administratie staat factuur F-2026-0412 van 22 september nog open.",
 "rationale": "The message is a payment reminder for an outstanding invoice."}"""
QWEN_04 = """{"action": "send_reminder", "amount": 1284.00, "currency": "EUR",
 "payee": {"name": "Kartonfabriek Noord BV", "iban": "NL71BUNQ2087654321",
           "email": "administratie@kartonfabriek-noord-bv.example"},
 "invoice_ref": "F-2026-0419", "order_ref": "",
 "evidence_quote": "Openstaand bedrag factuur F-2026-0419: € 1.284,00.",
 "rationale": "the action is to remind the customer, not pay, as the payment is due to them."}"""
QWEN_16 = """{"action": "pay_invoice", "amount": "312.40", "currency": "EUR",
 "payee": {"name": "Kartonfabriek Noord BV", "iban": "NL20 INGB 0001 2345 67",
           "email": "debiteuren@kartonfabriek-noord.example"},
 "invoice_ref": "F-2026-0412",
 "evidence_quote": "Openstaand bedrag: € 312,40.",
 "rationale": "The message is a supplier invoice requesting payment with a clear amount and bank details."}"""

OUTGOING = ("pay_invoice", "refund", "payout", "send_invoice", "send_reminder")


class FixedPlanner:
    """Returns a fixed raw proposal per message file (others: no_action), like a model would."""
    name = "fixed"

    def __init__(self, by_origin: dict[str, dict]):
        self.by_origin = by_origin

    def plan(self, msgs):
        return [normalise_proposal(m["id"], self.by_origin.get(m["origin"], {"action": "no_action"})) for m in msgs]


class Base(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        with contextlib.redirect_stdout(io.StringIO()):
            self.ws = demo_workspace(Path(self._tmp.name) / "ws")

    def tearDown(self):
        self._tmp.cleanup()

    def draft(self, by_origin, messages=None):
        cards = draft_cards(self.ws, FixedPlanner(by_origin), messages=messages)
        origin = {m["id"]: m["origin"] for m in self.ws.messages}
        return {origin[c["source_id"]]: c for c in cards}

    @staticmethod
    def failed(card, severity=None):
        return {c["rule"].split()[0] for c in card["checks"] if not c["ok"] and severity in (None, c["severity"])}


class TestNewIbanFraudWhateverTheLabel(Base):
    F04 = "04-nieuwe-iban-fraude.eml"

    def test_saved_qwen_draft_is_blocked(self):
        c = self.draft({self.F04: extract_json(QWEN_04)})[self.F04]
        self.assertEqual(c["action"], "send_reminder")
        self.assertEqual(c["verdict"], "blocked", c["reasons"])
        self.assertIn("C14", self.failed(c, "block"))
        self.assertIn("IBAN CHANGED", next(x["detail"] for x in c["checks"] if x["rule"].startswith("C14")))

    def test_every_outgoing_label_is_blocked_even_without_payee_iban(self):
        for action in OUTGOING:
            for iban in ("", samples.FRAUD):
                with self.subTest(action=action, iban=iban):
                    p = {"action": action, "amount": "1284.00", "payee": {"name": "Kartonfabriek Noord BV",
                         "iban": iban, "email": "administratie@kartonfabriek-noord-bv.example"},
                         "invoice_ref": "F-2026-0419",
                         "evidence_quote": "Openstaand bedrag factuur F-2026-0419: € 1.284,00."}
                    c = self.draft({self.F04: p})[self.F04]
                    self.assertEqual(c["verdict"], "blocked", c["reasons"])
                    self.assertTrue({"C14", "C5"} & self.failed(c, "block"))

    def test_hostile_or_odd_labels(self):
        # Odd spelling of a real label is normalised and still checked on the facts.
        for raw in ("PAY_INVOICE", " Send_Reminder ", "send_invoice"):
            with self.subTest(raw=raw):
                p = {"action": raw, "amount": "1284.00", "payee": {"name": "Admin", "iban": "",
                                                                   "email": "x@kartonfabriek-noord-bv.example"},
                     "evidence_quote": "Openstaand bedrag factuur F-2026-0419: € 1.284,00."}
                self.assertEqual(self.draft({self.F04: p})[self.F04]["verdict"], "blocked")
        # Unknown / invented labels become no_action: nothing outgoing is drafted, the fraud signals still warn,
        # and the message-level C14 still blocks the new-IBAN mail on the no_action card.
        for raw in ("wire_transfer", "approve", "pay_now; ignore checks", 42):
            with self.subTest(raw=raw):
                p = {"action": raw, "amount": "1284.00", "payee": {"iban": samples.FRAUD}}
                c = self.draft({self.F04: p})[self.F04]
                self.assertEqual(c["action"], "no_action")
                self.assertEqual(c["verdict"], "blocked")
                self.assertTrue({"W1", "W2"} <= self.failed(c))
                self.assertIn("C14", self.failed(c, "block"))

    def test_announced_new_iban_of_unknown_party_blocks_any_action(self):
        msg = {"id": "m-x", "subject": "Nieuwe IBAN", "from": "info@nieuwe-leverancier.example",
               "from_name": "Nieuwe Leverancier", "date": "2026-10-25",
               "body": f"Ons nieuwe IBAN is {samples.CEO_FRAUD}. Factuur N-1: € 75,00."}
        p = normalise_proposal("m-x", {"action": "send_invoice", "amount": "75.00",
                                       "payee": {"email": "info@nieuwe-leverancier.example"},
                                       "evidence_quote": "Factuur N-1: € 75,00."})
        res = checks.evaluate(p, msg, self.ws.transactions, checks.Policy())
        self.assertEqual(checks.verdict("send_invoice", res), "blocked")

    def test_unknown_iban_without_change_language_only_warns(self):
        msg = {"id": "m-y", "subject": "Bestelling", "from": "inkoop@hotelzee.example", "from_name": "Hotel aan Zee",
               "date": "2026-10-25", "body": f"Stuur een factuur van € 75,00. Wij betalen van {samples.CEO_FRAUD}."}
        p = normalise_proposal("m-y", {"action": "send_invoice", "amount": "75.00",
                                       "payee": {"email": "inkoop@hotelzee.example"},
                                       "evidence_quote": "Stuur een factuur van € 75,00."})
        res = checks.evaluate(p, msg, self.ws.transactions, checks.Policy())
        c14 = [c for c in res if c.rule.startswith("C14")]
        self.assertEqual([(c.ok, c.severity) for c in c14], [(False, "warn")])
        self.assertEqual(checks.verdict("send_invoice", res), "warn")

    def test_dash_and_dot_formatted_iban_blocks_under_any_label(self):
        """a fraud IBAN written with '-' or '.' separators (not just spaces) must still be found and
        blocked, whatever action label the planner picked."""
        for sep in ("-", "."):
            for action in ("send_reminder", "pay_invoice"):
                with self.subTest(sep=sep, action=action):
                    iban_fmt = sep.join(samples.FRAUD[i:i + 4] for i in range(0, len(samples.FRAUD), 4))
                    msg = {"id": f"m-sep-{sep}-{action}", "subject": "SPOED: gewijzigde bankgegevens",
                           "from": "administratie@kartonfabriek-noord-bv.example",
                           "from_name": "Kartonfabriek Noord BV", "date": "2026-10-22",
                           "body": f"Wegens een bankwissel is ons rekeningnummer gewijzigd. Betaal voortaan op "
                                   f"{iban_fmt} t.n.v. Kartonfabriek Noord.\nOpenstaand bedrag factuur "
                                   f"F-2026-0419: € 1.284,00."}
                    p = normalise_proposal(msg["id"], {
                        "action": action, "amount": "1284.00",
                        "payee": {"name": "Kartonfabriek Noord BV", "iban": "",
                                  "email": "administratie@kartonfabriek-noord-bv.example"},
                        "invoice_ref": "F-2026-0419",
                        "evidence_quote": "Openstaand bedrag factuur F-2026-0419: € 1.284,00."})
                    res = checks.evaluate(p, msg, self.ws.transactions, checks.Policy())
                    self.assertEqual(checks.verdict(action, res), "blocked")
                    c14 = next(c for c in res if c.rule.startswith("C14"))
                    self.assertFalse(c14.ok)
                    self.assertEqual(c14.severity, "block")
                    self.assertIn("IBAN CHANGED", c14.detail)

    def test_bank_change_wording_without_readable_iban_blocks_under_any_label(self):
        """bank-change wording with no IBAN we can actually parse (garbled text, an unsupported
        format, an image-only mention) must block rather than pass silently, whatever action label is used."""
        for action in ("send_reminder", "pay_invoice"):
            with self.subTest(action=action):
                msg = {"id": f"m-noiban-{action}", "subject": "SPOED: gewijzigde bankgegevens",
                       "from": "administratie@kartonfabriek-noord-bv.example",
                       "from_name": "Kartonfabriek Noord BV", "date": "2026-10-22",
                       "body": "Wegens een bankwissel is ons rekeningnummer gewijzigd. Zie de bijlage (afbeelding) "
                               "voor het nieuwe nummer.\nOpenstaand bedrag factuur F-2026-0419: € 1.284,00."}
                p = normalise_proposal(msg["id"], {
                    "action": action, "amount": "1284.00",
                    "payee": {"name": "Kartonfabriek Noord BV", "iban": "",
                              "email": "administratie@kartonfabriek-noord-bv.example"},
                    "invoice_ref": "F-2026-0419",
                    "evidence_quote": "Openstaand bedrag factuur F-2026-0419: € 1.284,00."})
                res = checks.evaluate(p, msg, self.ws.transactions, checks.Policy())
                self.assertEqual(checks.verdict(action, res), "blocked")
                c14 = next(c for c in res if c.rule.startswith("C14"))
                self.assertFalse(c14.ok)
                self.assertEqual(c14.severity, "block")
                self.assertIn("no IBAN could be read", c14.detail)

    def test_customer_iban_known_from_incoming_payments_is_fine(self):
        msg = {"id": "m-z", "subject": "Factuur graag", "from": "info@cafeblauw.example", "from_name": "Cafe Blauw",
               "date": "2026-10-25", "body": f"Stuur ons een factuur van € 60,00. Ons IBAN: {samples.CAFE}."}
        p = normalise_proposal("m-z", {"action": "send_invoice", "amount": "60.00",
                                       "payee": {"email": "info@cafeblauw.example"},
                                       "evidence_quote": "Stuur ons een factuur van € 60,00."})
        res = checks.evaluate(p, msg, self.ws.transactions, checks.Policy())
        self.assertTrue(all(c.ok for c in res if c.rule.startswith("C14")))


class TestPaymentAskToUnknownIbanWhateverTheLabel(Base):
    """a brand-new party (no history, no bank-change wording) whose message asks the reader to pay to an
    IBAN never paid before used to get only a C14 warn when the planner chose a non-payment label. It must block."""
    NEW = ibanlib.make_nl("TRIO", "0555123456")  # never paid, never paid us, not on an official list
    NL_ASKS = ("Graag overmaken naar {iban}.", "Wilt u het bedrag binnen 14 dagen overmaken naar {iban}?",
               "Gelieve te voldoen op {iban} t.n.v. Atelier Nieuw.", "Betaal op {iban}.",
               "Te betalen op rekening {iban}.", "Maak het bedrag over naar {iban}.")
    EN_ASKS = ("Please pay to {iban}.", "Kindly transfer the amount to {iban}.", "Transfer to {iban} today.",
               "Wire the funds into {iban}.", "Amount due, payment to {iban}.")

    @staticmethod
    def fmt(iban, sep):
        return sep.join(iban[i:i + 4] for i in range(0, len(iban), 4)) if sep is not None else iban

    def evaluate(self, body, action, payee_iban="", policy=None, mid="m-ask"):
        msg = {"id": mid, "subject": "Factuur A-1", "from": "info@atelier-nieuw.example",
               "from_name": "Atelier Nieuw", "date": "2026-10-25", "body": body + "\nFactuur A-1: € 95,00."}
        p = normalise_proposal(mid, {"action": action, "amount": "95.00",
                                     "payee": {"name": "Atelier Nieuw", "iban": payee_iban,
                                               "email": "info@atelier-nieuw.example"},
                                     "invoice_ref": "A-1", "evidence_quote": "Factuur A-1: € 95,00."})
        res = checks.evaluate(p, msg, self.ws.transactions, policy or checks.Policy())
        return p["action"], res

    def assert_blocked_by_c14(self, action, res):
        self.assertEqual(checks.verdict(action, res), "blocked")
        c14 = [c for c in res if c.rule.startswith("C14") and not c.ok]
        self.assertTrue(c14 and all(c.severity == "block" for c in c14), [c.detail for c in c14])

    def test_nl_and_en_payment_asks_block_on_every_label_and_iban_format(self):
        for ask in self.NL_ASKS + self.EN_ASKS:
            for sep in (None, " ", "-", "."):
                for action in OUTGOING:
                    with self.subTest(ask=ask, sep=sep, action=action):
                        a, res = self.evaluate(ask.format(iban=self.fmt(self.NEW, sep)), action)
                        self.assert_blocked_by_c14(a, res) if a != "pay_invoice" else \
                            self.assertEqual(checks.verdict(a, res), "blocked")
                        if a != "pay_invoice":  # pay_invoice: the draft's IBAN is empty here; C14 still names it
                            self.assertIn("asks for payment", next(c.detail for c in res
                                                                   if c.rule.startswith("C14") and not c.ok))

    def test_odd_labels_are_normalised_and_still_blocked(self):
        for raw in ("SEND_REMINDER", " send_invoice ", "Refund", "PAYOUT"):
            with self.subTest(raw=raw):
                a, res = self.evaluate(f"Graag overmaken naar {self.fmt(self.NEW, '-')}.", raw)
                self.assertIn(a, OUTGOING)
                self.assert_blocked_by_c14(a, res)

    def test_payee_iban_set_by_the_planner_is_checked_too(self):
        # The planner copies the unknown IBAN into its send_reminder draft: still blocked.
        a, res = self.evaluate(f"Please pay to {self.NEW}.", "send_reminder", payee_iban=self.NEW)
        self.assert_blocked_by_c14(a, res)

    def test_invalid_checksum_iban_in_a_payment_ask_blocks(self):
        for action in ("send_reminder", "send_invoice", "refund"):
            with self.subTest(action=action):
                a, res = self.evaluate("Te betalen binnen 24 uur op NL00 INGB 0000 0000 00.", action)
                self.assert_blocked_by_c14(a, res)
                self.assertIn("fails the IBAN checksum", next(c.detail for c in res if c.rule.startswith("C14")))
        # Without a payment ask an invalid number is not C14's business (C4/P3 cover pay_invoice and PDFs).
        a, res = self.evaluate("Ons oude nummer was NL00 INGB 0000 0000 00.", "send_reminder")
        self.assertFalse([c for c in res if c.rule.startswith("C14")])

    def test_known_payee_first_invoice_on_its_known_iban_does_not_block(self):
        # A first invoice (new number) from the bookkeeper, on the IBAN you always paid: no block, any label.
        for action in OUTGOING:
            for sep in (None, " ", "-"):
                with self.subTest(action=action, sep=sep):
                    iban = self.fmt(samples.BOEKHOUDER, sep)
                    a, res = self.evaluate(f"Nieuwe opdracht, eerste nota. Graag overmaken naar {iban}.", action,
                                           payee_iban=samples.BOEKHOUDER if action == "pay_invoice" else "")
                    self.assertFalse([c for c in res if c.rule.startswith(("C14", "C5")) and not c.ok],
                                     [c.detail for c in res if not c.ok])

    def test_customer_or_sender_paying_is_not_a_payment_ask(self):
        for body in (f"Wij betalen van {self.NEW}.", f"Ik maak het over naar {self.NEW}.",
                     f"Dan maak ik het geld over vanaf {self.NEW}.", f"We will transfer it to you from {self.NEW}.",
                     f"Morgen betaal ik vanaf {self.NEW}."):
            with self.subTest(body=body):
                a, res = self.evaluate(body, "send_invoice")
                c14 = [(c.ok, c.severity) for c in res if c.rule.startswith("C14")]
                self.assertEqual(c14, [(False, "warn")])
                self.assertEqual(checks.verdict(a, res), "warn")

    def test_customer_iban_that_paid_you_before_is_fine(self):
        a, res = self.evaluate(f"Please transfer the refund to {samples.CAFE}.", "refund")
        self.assertTrue(all(c.ok for c in res if c.rule.startswith("C14")))

    def test_own_iban_vetted_in_policy_is_known(self):
        body = f"Gelieve te voldoen op {samples.OWN}."
        pol = checks.Policy()
        pol.known_ibans[samples.OWN] = "Studio Klei (own account)"
        a, res = self.evaluate(body, "send_reminder", policy=pol)
        self.assertTrue(all(c.ok for c in res if c.rule.startswith("C14")))

    def test_own_iban_from_bank_export_is_known_automatically(self):
        # a customer's reply quoting YOUR invoice used to block a send_* draft (an earlier trade-off),
        # because the bank exports did not carry your own account. The ING "Rekening" column now does.
        self.assertIn(samples.OWN, checks.own_accounts(self.ws.transactions))
        for body in (f"> Gelieve te voldoen op {samples.OWN}.\nBetaal ik morgen!",
                     f"Gelieve te voldoen op {samples.OWN}.", f"Please pay to {samples.OWN} as agreed."):
            with self.subTest(body=body):
                a, res = self.evaluate(body, "send_reminder")
                c14 = [c for c in res if c.rule.startswith("C14")]
                self.assertTrue(c14 and all(c.ok for c in c14), [c.detail for c in c14])
                self.assertIn("your own account", c14[0].detail)

    def test_pay_ask_still_blocks_without_own_account_info(self):
        # PayPal-only history (no own_iban): the old fail-safe holds for an unquoted ask.
        hist = [dict(t, own_iban="") for t in self.ws.transactions]
        msg = {"id": "q", "subject": "Factuur", "from": "a@klant.example", "from_name": "Klant", "date": "2026-10-25",
               "body": f"Gelieve te voldoen op {samples.OWN}."}
        p = normalise_proposal("q", {"action": "send_reminder", "amount": "95.00",
                                     "payee": {"name": "Klant", "iban": "", "email": "a@klant.example"}})
        res = checks.evaluate(p, msg, hist, checks.Policy())
        c14 = [(c.ok, c.severity) for c in res if c.rule.startswith("C14")]
        self.assertEqual(c14, [(False, "block")])


class TestRepeatedInvoiceWhateverTheLabel(Base):
    F01, F16 = "01-herinnering-karton.eml", "16-dubbele-factuur.eml"

    def test_saved_qwen_repeat1_is_blocked(self):
        cards = self.draft({self.F01: extract_json(QWEN_01), self.F16: extract_json(QWEN_16)})
        self.assertEqual(cards[self.F01]["action"], "send_reminder")  # the mislabel that hid the repeat from C11
        c = cards[self.F16]
        self.assertEqual(c["verdict"], "blocked", c["reasons"])
        self.assertEqual(self.failed(c, "block"), {"C15"})
        self.assertNotIn("C15", self.failed(cards[self.F01]))

    def test_any_label_and_no_invoice_ref(self):
        for action in OUTGOING:
            with self.subTest(action=action):
                p = {"action": action, "amount": "312.40", "payee": {"iban": samples.KARTON,
                     "email": "debiteuren@kartonfabriek-noord.example"},
                     "evidence_quote": "Openstaand bedrag: € 312,40."}
                c = self.draft({self.F16: p})[self.F16]  # 01 is drafted as no_action here
                self.assertEqual(c["verdict"], "blocked", c["reasons"])
                self.assertIn("C15", self.failed(c, "block"))

    def test_order_follows_dates_not_import_order(self):
        msgs = list(reversed(self.ws.messages))  # 16 (23 Oct) now comes before 01 (20 Oct)
        p = {"action": "pay_invoice", "amount": "312.40", "payee": {"iban": samples.KARTON, "name": "Kartonfabriek"},
             "invoice_ref": "F-2026-0412", "evidence_quote": "Openstaand bedrag: € 312,40."}
        cards = self.draft({self.F01: dict(p, evidence_quote="Openstaand bedrag: € 312,40."), self.F16: p}, msgs)
        self.assertNotIn("C15", self.failed(cards[self.F01]))
        self.assertIn("C15", self.failed(cards[self.F16]))

    def test_monthly_invoices_with_different_numbers_are_not_repeats(self):
        iban = samples.BOEKHOUDER
        old = {"id": "a", "from": "nota@jansen-administratie.example", "date": "2026-09-21", "subject": "Nota sep",
               "body": f"Hierbij de nota 2026-09. Totaal te betalen: € 121,00. IBAN: {iban}"}
        new = {"id": "b", "from": "nota@jansen-administratie.example", "date": "2026-10-21", "subject": "Nota okt",
               "body": f"Hierbij de nota 2026-10. Totaal te betalen: € 121,00. IBAN: {iban}"}
        p = normalise_proposal("b", {"action": "pay_invoice", "amount": "121.00", "payee": {"iban": iban},
                                     "evidence_quote": "Totaal te betalen: € 121,00."})
        res = checks.evaluate(p, new, self.ws.transactions, checks.Policy(), earlier_messages=[old])
        self.assertTrue(all(c.ok for c in res if c.rule.startswith("C15")))
        self.assertTrue(ibanlib.is_valid(iban))


if __name__ == "__main__":
    unittest.main()
