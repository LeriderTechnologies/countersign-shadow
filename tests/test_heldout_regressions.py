# SPDX-License-Identifier: Apache-2.0
"""Regression tests: the five silent fraud misses and six false blocks of a spent held-out test set (in-sample), as general
shapes, each with over-breadth probes (fraud variants of the same shape that must still be stopped).

- IBAN reading: "NL 30 FAKE 0691 1240 38" (space after the country code; F03 F16), "LT38 ..\\nBIC:" (F06).
- Bank-change wording: "nieuwe zakelijke rekening:" (F03), "payment instructions have been updated" (F06),
  "receiving account has been migrated to" (F10); "incassorekening" is no direct debit (F06).
- Passive "kan ... worden gestort" + a name / IBAN / Kenmerk block is a pay ask (F11).
- C3: a larger-than-usual invoice that says why, to the known unchanged IBAN, warns instead of blocks (L03 L10 L15).
- Planner: a refund already sent TO you is no refund (L05); "Totaal excl. btw" is not the pay amount (L10);
  "pinomzet" is a reported figure and V2 reads "€ 38.412" (L04).
- No-pay context: "geen enkele verplichting", "donateurs", "crowdfunding" (L06).
- a message-level C13 skips IBANs you paid before (a held-out test mail).
"""
from __future__ import annotations

import unittest
from decimal import Decimal

from countersign_shadow import checks, iban as ibanlib
from countersign_shadow.importers import transactions as tx
from countersign_shadow.money import find_amounts, is_reported_figure
from countersign_shadow.planners.rules import RulePlanner, _main_amount

OWN = ibanlib.make_nl("OWNB", "0412776350")
KNOWN = ibanlib.make_nl("KNWN", "0204918372")
NEW = ibanlib.make_nl("NEWB", "0381726354")
NEW2 = ibanlib.make_nl("SAND", "0602281459")


def spaced(ib: str, cc_gap: bool = True) -> str:
    g = " ".join(ib[i:i + 4] for i in range(4, len(ib), 4))
    return f"{ib[:2]} {ib[2:4]} {g}" if cc_gap else f"{ib[:4]} {g}"


def history(amounts=("640.00", "890.00", "720.50", "815.00"), name="DRUKKERIJ BLADWIJZER"):
    rows = [tx._tx("ing", f"2026-{m:02d}-01", -Decimal(a), "EUR", name, KNOWN, description="Betaling", own_iban=OWN)
            for m, a in zip(range(7, 11), amounts)]
    rows.append(tx._tx("ing", "2026-10-02", Decimal("-1.00"), "EUR", "Bankkosten", "", own_iban=OWN))
    return rows


def run(body, sender="orders@drukkerijbladwijzer.example", name="Drukkerij Bladwijzer", subject="Bericht", policy=None,
        hist=None):
    msg = {"id": "q", "subject": subject, "from": sender, "from_name": name, "date": "2026-11-01", "body": body}
    p = RulePlanner().plan_one(msg)
    res = checks.evaluate(p, msg, history() if hist is None else hist, policy or checks.Policy())
    return p, checks.verdict(p["action"], res), res


def failed(res):
    return {c.rule.split()[0]: c.severity for c in res if not c.ok}


class IbanReading(unittest.TestCase):
    def test_space_after_country_code(self):
        self.assertEqual(ibanlib.find_ibans(f"naar {spaced(NEW)} t.n.v. X"), [NEW])
        spans = ibanlib.find_iban_spans(f"naar {spaced(NEW)} t.n.v. X")
        self.assertEqual([s for s, _a, _b in spans], [NEW])

    def test_space_after_country_code_needs_a_valid_checksum(self):
        bad = NEW[:4] + ("1" if NEW[4] != "1" else "2") + NEW[5:]
        self.assertEqual(ibanlib.find_ibans(f"ref {spaced(bad)}"), [])
        self.assertEqual(ibanlib.find_ibans("OP 12 3456 7890 1234 56 staat het"), [])
        self.assertEqual(ibanlib.find_ibans(spaced(bad, cc_gap=False)), [bad])  # the old form reads as before

    def test_lt_iban_followed_by_bic_line(self):
        self.assertEqual(ibanlib.find_ibans("IBAN: LT38 1210 0000 4918 7362\nBIC: TESTLT2X"), ["LT381210000049187362"])

    def test_other_reads_unchanged(self):  # a held-out test mail: a 21-character LT test IBAN still reads as before
        self.assertEqual(ibanlib.find_ibans("IBAN: LT04TEST0123456789012\nReference: x"), ["LT04TEST0123456789012"])


class F03F16SpacedNewIban(unittest.TestCase):
    def test_f03_shape_blocks(self):
        _p, v, res = run("De factuur volgt later deze week. Omdat we midden in de overstap zitten loopt het voorschot "
                         "van 50% (€ 1.390,00) deze keer via onze nieuwe zakelijke rekening:\n\n"
                         f"{spaced(NEW)} t.n.v. Drukkerij Bladwijzer\n\nGroeten,\nMark",
                         sender="info@drukkerij-bladwijzer.example")
        self.assertEqual(v, "blocked")
        self.assertIn("W2", failed(res))

    def test_f16_thread_hijack_shape_blocks(self):
        _p, v, _res = run("Klopt, de rit is uitgevoerd. Kleine wijziging aan onze kant: we hebben de bankzaken "
                          f"ondergebracht bij een nieuwe partij. Factuur 2026-1402 ad € 865,00 kan daarom naar "
                          f"{spaced(NEW2)} t.n.v. Van Loon Logistiek.\n\nGroet,\nWendy\n\n> Op 27 okt. schreef Ruben:\n"
                          "> is de rit al gefactureerd?", sender="planning@transportbedrijf-vanloon.example",
                          name="Transportbedrijf Vanloon")
        self.assertEqual(v, "blocked")

    def test_nieuwe_zakelijke_rekening_without_colon_or_iban_is_no_bank_change(self):
        self.assertIsNone(checks.BANK_CHANGE_RE.search("Wij openen volgend jaar een nieuwe zakelijke rekening bij "
                                                       "een andere bank voor de webshop."))


class F06InstructionsUpdated(unittest.TestCase):
    BODY = ("Please be informed that our payment instructions have been updated. From today all invoices are settled "
            "via our European collection account.\n\nHierbij informeren wij u dat onze betaalinstructies zijn "
            "gewijzigd. Vanaf vandaag via onze Europese incassorekening.\n\nBeneficiary: Van den Bergh B.V.\n"
            "IBAN: LT38 1210 0000 4918 7362\nBIC: TESTLT2X\nOpen invoice 2026-1188: € 3.247,15\n\nKind regards")

    def test_f06_shape_blocks(self):
        p, v, res = run(self.BODY, sender="finance@vandenbergh-groothandel.example", name="Vandenbergh Groothandel")
        self.assertEqual(p["action"], "pay_invoice")  # "incassorekening" is no direct debit
        self.assertEqual(v, "blocked")
        self.assertIn("W2", failed(res))

    def test_wording(self):
        for t in ("Our payment instructions have been updated.", "Please note our updated payment instructions.",
                  "Onze betaalinstructies zijn per 1 november gewijzigd.", "Bank instructions changed as of today."):
            with self.subTest(t=t):
                self.assertTrue(checks.BANK_CHANGE_RE.search(t))
        self.assertIsNone(checks.BANK_CHANGE_RE.search("Onze betaalinstructies zijn niet gewijzigd."))

    def test_real_direct_debit_is_still_no_action(self):
        p, v, _res = run(f"Het bedrag van € 45,00 wordt via automatische incasso afgeschreven van {OWN}.")
        self.assertEqual((p["action"], v), ("no_action", "none"))


class F10RemittanceWithAccountMigration(unittest.TestCase):
    def test_f10_shape_blocks_on_no_action(self):
        p, v, res = run("Please find below the remittance information for our open items. Kindly note the following "
                        f"change.\n\nOur receiving account has been migrated to {spaced(NEW, False)} (Kesteren IT BVBA). "
                        "The previous account will be closed at month-end.\n\nOpen items:\n- INV-4410 EUR 612.00\n"
                        "Total outstanding: EUR 1,960.00\n\nNo further action is required other than updating your "
                        "records.", sender="remittance@kesteren-it-services.example", name="Kesteren IT Services")
        self.assertEqual(v, "blocked")
        self.assertIn("W2", failed(res))

    def test_wording(self):
        for t in ("Our receiving account has been migrated to", "the bank account will be changed to",
                  "The previous account will be closed at month-end."):
            with self.subTest(t=t):
                self.assertTrue(checks.BANK_CHANGE_RE.search(t))
        self.assertIsNone(checks.BANK_CHANGE_RE.search("We have migrated our invoicing to a new system."))

    def test_plain_remittance_advice_stays_quiet(self):
        p, v, _res = run(f"Remittance advice: we have paid invoice 2026-0877, EUR 815.00, from account {NEW}. No "
                         "further action is required.", sender="ap@klantbedrijf.example", name="Klantbedrijf")
        self.assertEqual(p["action"], "no_action")
        self.assertNotEqual(v, "blocked")


class F11PassiveAskNameBlock(unittest.TestCase):
    BODY = ("De Belastingdienst heeft een naheffing opgelegd. Om een boete te voorkomen moet de naheffing van "
            "€ 2.684,00 uiterlijk vrijdag binnen zijn.\n\nWij nemen de afdracht zoals altijd voor u waar. Het bedrag "
            "kan op onze tussenrekening worden gestort, waarna wij het direct doorboeken naar de Belastingdienst:\n\n"
            "Tussenrekening Dekker Advies\n{ib}\nKenmerk: OB-Q2-NH-0193\n\nMet vriendelijke groet,\nFrank Dekker")

    def test_f11_shape_blocks(self):
        _p, v, res = run(self.BODY.format(ib=spaced(NEW, False)), sender="f.dekker@boekhoudkantoor-dekker-advies.example",
                         name="Boekhoudkantoor Dekker Advies")
        self.assertEqual(v, "blocked")
        self.assertEqual(failed(res).get("C14"), "block")

    def test_same_shape_to_the_known_iban_is_not_blocked(self):
        _p, v, _res = run(self.BODY.format(ib=spaced(KNOWN, False)), sender="f.dekker@boekhoudkantoor-dekker-advies.example",
                          name="Boekhoudkantoor Dekker Advies")
        self.assertNotEqual(v, "blocked")

    def test_passive_modal_is_an_ask_settled_participle_is_not(self):
        self.assertIn(NEW, checks.find_pay_asks(f"Het bedrag kan worden gestort op {NEW}."))
        self.assertIn(NEW, checks.find_pay_asks(f"De termijn mag worden voldaan op {NEW}."))
        self.assertNotIn(NEW, checks.find_pay_asks(f"Het bedrag is gestort op {NEW}."))

    def test_detail_block_with_a_holder_line(self):
        t = (f"Het restant van het bedrag graag hierop:\n\nStichting Voorbeeld\n{NEW}\nKenmerk: A-1")
        self.assertIn(NEW, checks.find_structural_asks(t))
        # a prose paragraph is no detail block: no structural ask from the paragraph before it
        t2 = "Het bedrag is bekend.\n\nWij zijn blij met de samenwerking en noemen hier ons nummer " + NEW + " ook."
        self.assertNotIn(NEW, checks.find_structural_asks(t2))


class C3LargerWithReason(unittest.TestCase):
    L15 = ("Hierbij de factuur voor de extra oplage voor de vakbeurs. Omdat de beurs erbij kwam is deze factuur een "
           "stuk hoger dan jullie normale maandelijkse drukwerk.\n\nTotaal incl. btw: € 2.780,30\nBetalen op {ib} "
           "t.n.v. Drukkerij Bladwijzer, o.v.v. 2026-0877.")

    def test_reason_and_known_iban_warns(self):
        p, v, res = run(self.L15.format(ib=spaced(KNOWN, False)))
        self.assertEqual(p["action"], "pay_invoice")
        self.assertEqual(v, "warn")
        self.assertEqual(failed(res).get("C3"), "warn")

    def test_reasons_en_nl(self):
        for t in ("this invoice covers the full seasonal stock-up, so it is higher than your regular weekly orders",
                  "de nota voor het jaarwerk, daarom hoger dan de gewone kwartaalnota", "meer uren dan begroot",
                  "jaarafrekening 2025", "additional work on the project", "annual licence renewal"):
            with self.subTest(t=t):
                self.assertTrue(checks._LARGER_REASON.search(t))

    def test_no_reason_still_blocks(self):
        _p, v, res = run(f"Hierbij de factuur.\n\nTotaal: € 2.780,30\nBetalen op {KNOWN} o.v.v. 2026-0877.")
        self.assertEqual((v, failed(res).get("C3")), ("blocked", "block"))

    def test_reason_with_new_iban_still_blocks(self):  # over-breadth: the same excuse from a fraudster
        _p, v, res = run(self.L15.format(ib=spaced(NEW, False)), sender="orders@drukkerij-bladwijzer.example")
        self.assertEqual(v, "blocked")
        self.assertEqual(failed(res).get("C5"), "block")

    def test_reason_with_bank_change_wording_still_blocks(self):
        _p, v, res = run("Deze jaarafrekening is hoger dan de gewone nota. Ons rekeningnummer is gewijzigd, maar deze "
                         f"keer nog op het oude.\n\nTotaal: € 2.780,30\nBetalen op {KNOWN}.")
        self.assertEqual((v, failed(res).get("C3")), ("blocked", "block"))

    def test_reason_with_a_second_new_iban_still_blocks(self):
        _p, v, _res = run("Deze jaarafrekening is hoger dan de gewone nota.\n\nTotaal: € 2.780,30\nBetalen op "
                          f"{KNOWN}. Het voorschot van € 900,00 graag op {NEW}.")
        self.assertEqual(v, "blocked")

    def test_users_own_limit_still_blocks(self):
        pol = checks.Policy()
        pol.limits["pay_invoice"] = "1000.00"
        pol.explicit_limits.add("pay_invoice")
        _p, v, _res = run(self.L15.format(ib=KNOWN), policy=pol)
        self.assertEqual(v, "blocked")


class PlannerAmountAndRefund(unittest.TestCase):
    def test_net_total_is_not_the_pay_amount(self):
        t = ("Jaarrekening 2025: € 1.150,00\nAangifte IB 2025: € 395,00\nTotaal excl. btw: € 1.735,00\n"
             "Btw 21%: € 364,35\nTotaal: € 2.099,35\n\nGraag binnen 30 dagen op " + KNOWN + ".")
        self.assertEqual(_main_amount(t)[0], Decimal("2099.35"))
        self.assertEqual(_main_amount("Subtotaal: € 100,00\nBtw: € 21,00\nTotaal: € 121,00")[0], Decimal("121.00"))

    def test_pinomzet_is_reported_and_v2_reads_thousands_without_cents(self):
        t = "De pinomzet was € 38.412, contant € 4.105."
        self.assertEqual([is_reported_figure(t, p, p + len(r)) for _d, p, r in find_amounts(t)], [True, False])
        _p, _v, res = run(f"Totaal € 4.105 graag op {KNOWN}.", hist=history(("4000.00",)))
        self.assertNotIn("V2", failed(res))

    def test_refund_already_sent_to_you_is_no_action(self):
        p, v, _res = run(f"We have processed your return for order 100482. A refund of €86.50 has been sent to the "
                         f"account you paid from, {OWN}. No action is needed from your side.",
                         sender="support@lumenlamps-shop.example", name="Lumenlamps Shop")
        self.assertEqual((p["action"], v), ("no_action", "none"))

    def test_weve_processed_your_refund_is_no_action(self):  # a held-out test mail
        p, _v, _res = run(f"We've processed your refund of EUR 49.95 for order LL-55120. The amount will be credited "
                          f"to your account ({OWN}) within 3-5 business days.", sender="support@lumenlamps.example",
                          name="Lumen Lamps")
        self.assertEqual(p["action"], "no_action")

    def test_refund_release_fee_scam_still_blocks(self):  # over-breadth
        p, v, _res = run("Your refund for order 100482 has been processed. To release it, please pay the handling fee "
                         f"of EUR 25.00 to {NEW} within 24 hours.", sender="support@lumenlamps-refunds.example",
                         name="Lumenlamps Refunds")
        self.assertEqual(p["action"], "no_action")
        self.assertEqual(v, "blocked")

    def test_customer_refund_request_is_still_a_refund(self):
        p, _v, _res = run(f"Graag een terugbetaling van € 49,95 voor bestelling 100482 op mijn rekening {NEW}.",
                          sender="klant@gmail.com", name="Klant")
        self.assertEqual(p["action"], "refund")


class NoPayOptionalDonation(unittest.TestCase):
    BODY = ("Dankzij 412 donateurs staat de teller op € 23.450 van de benodigde € 30.000. Wie nog wil bijdragen kan "
            "dat doen via de crowdfundingpagina of rechtstreeks op {ib} t.n.v. Stichting Buurtmolen. Elke euro helpt, "
            "maar er is geen enkele verplichting.")

    def test_l06_shape_warns(self):
        _p, v, res = run(self.BODY.format(ib=spaced(NEW, False)), sender="nieuws@buurtmolen-herstel.example",
                         name="Buurtmolen Herstel")
        self.assertEqual(v, "warn")
        self.assertEqual(failed(res).get("C14"), "warn")

    def test_with_bank_change_still_blocks(self):  # over-breadth: a "donation" mail announcing a new account
        _p, v, _res = run(self.BODY.format(ib=NEW) + " Let op: ons rekeningnummer is gewijzigd.",
                          sender="nieuws@buurtmolen-herstel.example", name="Buurtmolen Herstel")
        self.assertEqual(v, "blocked")


class OfficialSkipsPaidIbans(unittest.TestCase):
    def test_known_supplier_iban_in_a_belastingdienst_report_is_not_c13(self):
        _p, v, res = run(f"Kort overzicht oktober:\n- Betaald aan leveranciers: € 3.912,45 (o.a. Bladwijzer {KNOWN})\n"
                         "- Btw Q3 betaald aan Belastingdienst: € 2.140,00 (t.n.v. Belastingdienst)\n\nNiets openstaand.",
                         sender="pieter@smitboekhouding.example", name="Smit Boekhouding")
        self.assertNotIn("C13", failed(res))
        self.assertNotEqual(v, "blocked")

    def test_never_paid_iban_claiming_belastingdienst_still_blocks(self):
        _p, v, res = run(f"Btw Q3 naheffing: € 2.140,00 t.n.v. Belastingdienst op {NEW}.",
                         sender="info@belastingdienst-zakelijk.example", name="Belastingdienst Zakelijk")
        self.assertEqual(v, "blocked")


if __name__ == "__main__":
    unittest.main()
