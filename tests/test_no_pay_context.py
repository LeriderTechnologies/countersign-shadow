# SPDX-License-Identifier: Apache-2.0
"""Regression tests (tuned on spent held-out test sets; a later held-out set confirms).

1. no-pay context: a never-paid IBAN block (C5 / C14, and C3 on such a card) becomes a WARN when the mail says
   paying is optional, it was already paid, the money comes in, it goes by direct debit, it is a quote, a donation
   appeal, or the IBAN is an example in a fraud warning. Guards: IBAN CHANGED, bank-change wording, redirect of future
   payments, failed / stopped direct debit, debt-collection "incasso", a deposit on a quote, an outright pay ask on
   "already paid" / "incoming" mails.
2. C3 limit from your own history: 1.5x your largest earlier payment to that IBAN (block above the flat default
   too, warn below it); flat default without history or when policy.json sets the limit; no limit to your own account.
3. C14 "no IBAN could be read" only when the message really has no readable IBAN.
4. passive "dient te worden overgemaakt"; amount in the paragraph AFTER a label block; noun-first "betaalgegevens
   .. gewijzigd"; clock times ("vanaf 14:00", "14.00 uur") are no amounts and no source accounts.
"""
from __future__ import annotations

import unittest
from decimal import Decimal

from countersign_shadow import checks, iban as ibanlib
from countersign_shadow.importers import transactions as tx
from countersign_shadow.money import find_bare_amounts
from countersign_shadow.planners import normalise_proposal
from countersign_shadow.planners.rules import RulePlanner

OWN = ibanlib.make_nl("OWNB", "0412776350")
KNOWN = ibanlib.make_nl("KNWN", "0204918372")
NEW = ibanlib.make_nl("NEWB", "0108293745")


def sp(i):
    return " ".join(i[k:k + 4] for k in range(0, len(i), 4))


X, K, O = sp(NEW), sp(KNOWN), sp(OWN)


def history(paid=Decimal("250.00")):
    rows = [tx._tx("ing", d, -paid, "EUR", "DRUKKERIJ PETERS B.V.", KNOWN, description="Betaling", own_iban=OWN)
            for d in ("2026-08-01", "2026-09-01", "2026-10-01")]
    rows.append(tx._tx("ing", "2026-10-02", Decimal("-1.00"), "EUR", "Bankkosten", "", own_iban=OWN))
    return rows


def run(body, action="no_action", iban="", amount="95.00", name="Atelier Nieuw", hist=None, policy=None,
        sender="info@nieuw.example"):
    msg = {"id": "q", "subject": "Bericht", "from": sender, "from_name": name, "date": "2026-11-01", "body": body}
    p = normalise_proposal("q", {"action": action, "amount": amount if action != "no_action" else None,
                                 "payee": {"name": name, "iban": iban, "email": sender},
                                 "evidence_quote": body[:20]})
    res = checks.evaluate(p, msg, history() if hist is None else hist, policy or checks.Policy())
    return checks.verdict(action, res), res


def failed(res, prefix):
    return [c for c in res if c.rule.startswith(prefix) and not c.ok]


# ------------------------------------------------------------------------------------------- 1. no-pay context
NOPAY = {
    "optional": f"Wie wil bijdragen kan dit doen op {X} t.n.v. Tennisvereniging. Er is geen verplichting.",
    "optional_en": f"If you prefer to pay yearly you can pay EUR 1,009.20 to {X}. This is optional.",
    "donation": f"Met € 25 help je een pup. Maak je gift over op {X} t.n.v. Stichting Opvang. Giften zijn aftrekbaar.",
    "direct_debit": f"De aanslag bedraagt € 684,20 en wordt automatisch afgeschreven. Handmatig betalen kan ook op {X}.",
    "direct_debit_list": f"- Hosting Oost, fact. H-2291: € 59,00 ({X}), loopt via incasso",
    "quote": (f"Hierbij de offerte. Totaal: € 3.480,00. Deze offerte is 30 dagen geldig. Na oplevering ontvangt u "
              f"de factuur, te voldoen binnen 14 dagen op {X}."),
    "paid_receipt": f"Ticket: EUR 299.00\nStatus: PAID\n\nOur bank details for your records:\nEvents Ltd, IBAN {X}",
    "incoming_source": f"Good news: we received EUR 1,250.00 from {X} (Hendriks Interieur) for order 7731.",
    "incoming_remit": f"We have transferred EUR 2,150.00 to your account {O}.\n\nPaid from: Harbourline Ltd, {X}",
    "no_action": f"Overzicht:\n- Drukkerij Peters: € 340,00 ({X}), vervalt 12-11\n\nJe hoeft nu niets te doen.",
    "fraud_example": (f"Criminals ask you to move your balance to a 'safe account' such as {X}. We will never ask "
                      f"you to transfer money."),
}


class TestNoPayContextSoftens(unittest.TestCase):
    def test_no_action_cards_warn_not_block(self):
        for key, body in NOPAY.items():
            with self.subTest(key=key):
                v, res = run(body)
                c14 = failed(res, "C14")
                if key.startswith("incoming"):  # the IBAN is a source / payer: no ask at all, nothing to soften
                    self.assertEqual((v, c14), ("none", []))
                    continue
                self.assertEqual(v, "warn", res)
                self.assertTrue(c14 and all(c.severity == "warn" and checks.NOPAY_TAG in c.detail for c in c14))

    def test_pay_invoice_card_c5_and_c3_soften(self):
        """A planner that drafts a payment anyway (the rule planner does, on these four): C5 and C3 warn."""
        for key in ("incoming_source", "incoming_remit", "quote", "donation"):
            with self.subTest(key=key):
                v, res = run(NOPAY[key], "pay_invoice", NEW, amount="2150.00")
                self.assertEqual(failed(res, "C5")[0].severity, "warn")
                self.assertEqual(failed(res, "C3")[0].severity, "warn")
                # only V2 may still block (the test amount is not in every body): nothing from C3 / C5 / C14
                self.assertLessEqual({c.rule[:2] for c in res if not c.ok and c.severity == "block"}, {"V2"})

    def test_refund_to_senders_own_account_c14_softens_but_c7_still_blocks(self):
        body = f"Could you please refund the EUR 64.95 for order #20481 to my account {X} (C. Whitfield)?"
        v, res = run(body, "refund", NEW, amount="64.95")
        self.assertEqual(failed(res, "C14")[0].severity, "warn")
        self.assertEqual(failed(res, "C7")[0].severity, "block")
        self.assertEqual(v, "blocked")


class TestNoPayContextGuards(unittest.TestCase):
    def blocks(self, body, action="no_action", iban=""):
        v, res = run(body, action, iban, amount="95.00")
        self.assertEqual(v, "blocked", res)

    def test_bank_change_and_redirect_wording_keep_the_block(self):
        for body in (NOPAY["optional"] + " Let op: nieuw rekeningnummer.",
                     f"Future invoices will be paid into the account below.\n\nBeneficiary: Billing UAB\nIBAN: {X}\n\n"
                     f"No action is needed for direct debits already in place. Your next invoice (EUR 149.00) follows.",
                     f"Voortaan graag op {X}, bedrag € 95,00. De incasso vervalt.",
                     f"De betaalgegevens zijn gewijzigd. Bedrag € 95,00 graag op {X}. Dit is niet verplicht."):
            with self.subTest(body=body):
                self.blocks(body)

    def test_failed_or_stopped_direct_debit_and_collection_threats_block(self):
        for body in (f"Uw incasso is mislukt. Betaal het bedrag van € 95,00 vandaag op {X}.",
                     f"Your direct debit failed. Please pay EUR 95.00 to {X}.",
                     f"We stoppen met automatische incasso. Betaal voortaan € 95,00 op {X}.",
                     f"Betaal € 95,00 op {X}, anders dragen we de vordering over ter incasso."):
            with self.subTest(body=body):
                self.blocks(body)

    def test_outright_ask_beats_paid_incoming_and_no_action_wording(self):
        for body in (f"Your last invoice is already paid. For invoice 2 please pay EUR 95.00 to {X}.",
                     f"We have received your order. Please transfer EUR 95.00 to {X}.",
                     f"Je hoeft nu niets te doen behalve € 95,00 overmaken naar {X}."):
            with self.subTest(body=body):
                self.blocks(body)

    def test_quote_with_deposit_blocks(self):
        self.blocks(f"Hierbij de offerte. Bij akkoord graag een aanbetaling van € 950,00 op {X}.")

    def test_iban_changed_is_never_softened(self):
        body = f"Wie wil bijdragen kan dit doen op {X}. Er is geen verplichting. Bedrag € 95,00."
        v, res = run(body, name="Drukkerij Peters")
        self.assertEqual(v, "blocked")
        self.assertTrue(any("IBAN CHANGED" in c.detail and c.severity == "block" for c in failed(res, "C14")))

    def test_plain_never_paid_ask_still_blocks(self):
        self.blocks(f"Graag € 95,00 overmaken op {X} t.n.v. Atelier Nieuw.")
        self.blocks(f"Invoice A-1: EUR 95.00.\n\nIBAN: {X}", "pay_invoice", NEW)


# ------------------------------------------------------------------------------------------- 2. C3 from history
class TestC3FromHistory(unittest.TestCase):
    def c3(self, amount, iban=KNOWN, paid="250.00", policy=None):
        body = f"Factuur A-1, totaal € {amount}. Graag overmaken op {sp(iban)}."
        _v, res = run(body, "pay_invoice", iban, amount=amount, name="Drukkerij Peters",
                      hist=history(Decimal(paid)), policy=policy)
        return next(c for c in res if c.rule.startswith("C3"))

    def test_history_raises_the_limit_above_the_flat_default(self):
        c = self.c3("2000.00", paid="1500.00")
        self.assertTrue(c.ok, c.detail)
        self.assertIn("1.5x", c.detail)
        self.assertFalse(self.c3("2400.00", paid="1500.00").ok)
        self.assertEqual(self.c3("2400.00", paid="1500.00").severity, "block")

    def test_above_history_but_below_flat_warns(self):
        c = self.c3("400.00")
        self.assertFalse(c.ok)
        self.assertEqual(c.severity, "warn")
        self.assertTrue(self.c3("375.00").ok)

    def test_no_history_keeps_the_flat_default(self):
        c = self.c3("1600.00", iban=NEW)
        self.assertFalse(c.ok)
        self.assertEqual(c.severity, "block")
        self.assertEqual(c.detail, "1600.00 <= 1500.00")
        self.assertTrue(self.c3("1400.00", iban=NEW).ok)

    def test_explicit_policy_limit_wins(self):
        pol = checks.Policy()
        pol.limits["pay_invoice"] = "300.00"
        pol.explicit_limits.add("pay_invoice")
        c = self.c3("350.00", policy=pol)
        self.assertFalse(c.ok)
        self.assertEqual(c.detail, "350.00 <= 300.00")

    def test_own_account_has_no_limit(self):
        body = f"The quote of EUR 2,400.00 is signed.\n\n--\nLisa | KvK 70123456 | IBAN {O}"
        v, res = run(body, "pay_invoice", OWN, amount="2400.00")
        self.assertTrue(next(c for c in res if c.rule.startswith("C3")).ok)
        self.assertEqual(v, "ok")


# ------------------------------------------------------------------------------------------- 3. C14 message
class TestNoIbanMessage(unittest.TestCase):
    def test_payee_iban_filtered_is_not_no_iban_could_be_read(self):
        body = f"Ons nieuwe rekeningnummer is {X}. Openstaand bedrag factuur F-1: € 95,00."
        _v, res = run(body, "pay_invoice", NEW)
        self.assertFalse(any("no IBAN could be read" in c.detail for c in res))
        self.assertEqual(failed(res, "C5")[0].severity, "block")

    def test_really_no_iban_still_blocks(self):
        body = "Ons rekeningnummer is gewijzigd, zie de bijlage. Openstaand bedrag factuur F-1: € 95,00."
        v, res = run(body, "pay_invoice", "")
        self.assertTrue(any("no IBAN could be read" in c.detail for c in res))
        self.assertEqual(v, "blocked")


# ------------------------------------------------------------------------------------------- 4. misses
class TestM1144Misses(unittest.TestCase):
    def test_passive_dient_te_worden_overgemaakt_is_a_pay_ask(self):
        for body in (f"Toekomstige abonnementskosten dienen te worden overgemaakt naar {X}.",
                     f"De huur dient vanaf 14:00 vandaag op het volgende rekeningnummer te worden overgemaakt: {X}.",
                     f"Het bedrag moet uiterlijk vrijdag worden betaald op {X}."):
            with self.subTest(body=body):
                self.assertIn(NEW, checks.find_pay_asks(body))
                self.assertEqual(run(body)[0], "blocked")
        self.assertNotIn(NEW, checks.find_pay_asks(f"Het bedrag is gisteren overgemaakt vanaf {X}."))

    def test_amount_in_the_paragraph_after_a_label_block(self):
        for body in (f"We werken met een andere rekening.\n\nIBAN: {X}\n\nDe factuur (€ 640,00) sturen we hierna toe.",
                     f"Nieuw verwerkingsbureau.\n\nRekeningnummer: {X}\nNaam: Tank BV\n\n"
                     f"Verbruik afgelopen maand: 212 liter, bedrag 318,40."):
            with self.subTest(body=body):
                self.assertIn(NEW, checks.find_structural_asks(body))
                self.assertEqual(run(body)[0], "blocked")

    def test_paragraph_after_guards(self):
        for body in (f"Onze gegevens:\n\nIBAN: {X}\n\nTot ziens op de beurs!",
                     f"Onze gegevens:\n\nIBAN: {X}\n\nWij hebben € 95,00 naar u overgemaakt.",
                     f"Hoi,\n\nIBAN: {X}\nNaam: Atelier\n\nWe komen om 14.00 uur langs."):
            with self.subTest(body=body):
                self.assertNotIn(NEW, checks.find_structural_asks(body))

    def test_noun_first_betaalgegevens_gewijzigd(self):
        for body in ("Per 1 december zijn de betaalgegevens voor zakelijke klanten gewijzigd.",
                     "Ons rekeningnummer is per deze maand veranderd.", "Betaalgegevens aangepast."):
            with self.subTest(body=body):
                self.assertTrue(checks.BANK_CHANGE_RE.search(body))
        for body in ("Onze bankgegevens zijn NIET gewijzigd.",
                     "E-mails melden dat onze bankgegevens zijn gewijzigd. Dat is niet zo: onze bankgegevens zijn "
                     "niet gewijzigd.", "Uw rekeningnummer staat in Mijn Omgeving."):
            with self.subTest(body=body):
                self.assertFalse(checks.BANK_CHANGE_RE.search(body))

    def test_clock_time_is_no_amount_and_no_source(self):
        self.assertEqual(find_bare_amounts("vanaf 14.00 uur, om 9.30, until 17.30 CET, vanaf 14:00"), [])
        self.assertEqual([r for _a, _b, r in find_bare_amounts("Totaal 14.00 en 14,00")], ["14.00", "14,00"])
        self.assertFalse(checks._is_source(f"vanaf 14:00 op {X}", len("vanaf 14:00 op ")))
        p = RulePlanner().plan_one({"id": "t", "from": "a@b.example", "subject": "Levering",
                                    "body": "Het totaal: vanaf 14.00 uur leveren we.\nFactuur volgt."})
        self.assertIsNone(p["amount"])


if __name__ == "__main__":
    unittest.main()
