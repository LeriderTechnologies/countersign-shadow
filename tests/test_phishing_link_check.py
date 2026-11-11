# SPDX-License-Identifier: Apache-2.0
"""A phishing card names its link or login ask (W4) instead of showing only "Geen druk of haast".
Synthetic demo data only."""
from __future__ import annotations

import contextlib
import io
import re
import tempfile
import unittest
from pathlib import Path

from countersign_shadow import __main__ as cli
from countersign_shadow import nl
from countersign_shadow.checks import Policy, evaluate, verdict
from countersign_shadow.planners import make_planner
from countersign_shadow.web import make_handler

W4 = "W4 no suspicious link or login request"


def w4(body: str, sender: str, action: str = "flag_suspicious", subject: str = "Bericht"):
    msg = {"from": sender, "from_name": "", "subject": subject, "body": body, "date": "2026-10-21"}
    prop = {"action": action, "amount": None, "currency": "EUR", "payee": {}}
    checks = evaluate(prop, msg, [], Policy())
    return next((c for c in checks if c.rule == W4), None), verdict(action, checks)


class LinkLoginCheck(unittest.TestCase):
    def test_hit_lookalike_link_and_login_ask(self):
        c, v = w4("Uw rekening wordt geblokkeerd. Log in via https://ing-zakelijk-verificatie.example/login om uw "
                  "gegevens te bevestigen.", "beveiliging@ing-zakelijk-verificatie.example")
        self.assertEqual(v, "flagged")
        self.assertFalse(c.ok)
        self.assertEqual(c.severity, "warn")
        self.assertIn("ing-zakelijk-verificatie.example", c.uitleg)   # the bare domain is named
        self.assertIn("lijkt op ING", c.uitleg)
        self.assertIn('("Log in")', c.uitleg)
        self.assertNotIn("https://", c.uitleg)                          # no link to click, only the domain
        self.assertEqual(nl.regel(c.rule, False), "Verdachte link of vraag om in te loggen of je gegevens in te vullen")

    def test_hit_link_to_other_domain_than_sender(self):
        c, _ = w4("Uw pakket wacht. Betaal de verzendkosten op www.track-pakket-nu.example binnen 2 dagen.",
                  "info@bezorgdienst.example")
        self.assertFalse(c.ok)
        self.assertIn("track-pakket-nu.example, niet naar het domein van de afzender (bezorgdienst.example)", c.uitleg)

    def test_hit_lookalike_sender_and_bank_details_ask(self):
        c, _ = w4("Uw account is beperkt. Vul uw bankgegevens opnieuw in om het geld vrij te geven.",
                  "service@paypa1-secure.example")
        self.assertFalse(c.ok)
        self.assertIn("paypa1-secure.example) lijkt op PayPal", c.uitleg)
        self.assertIn("gegevens in te vullen", c.uitleg)

    def test_clean_mail(self):
        # A flagged card with no link, no login ask and an honest sender: W4 passes (shown as a green line).
        c, v = w4("Uw abonnement verloopt volgende maand. Wij sturen u tegen die tijd een nieuwe factuur.",
                  "info@stroomenco.example")
        self.assertTrue(c.ok, c.detail)
        self.assertEqual(c.detail, "none found")
        self.assertEqual(v, "flagged")
        # A link to the sender's own domain is no hit either.
        c, _ = w4("De nieuwe catalogus staat op https://www.stroomenco.example/winter.", "nieuws@stroomenco.example")
        self.assertTrue(c.ok, c.detail)

    def test_official_belastingdienst_link_is_no_lookalike(self):
        # The real belastingdienst.nl (an official-list source) is never a lookalike or foreign link, whoever sends it.
        c, _ = w4("Lees meer op https://www.belastingdienst.nl/wps/wcm/connect/nl/contact/content/x",
                  "nieuws@boekhouder.example")
        self.assertTrue(c.ok, c.detail)
        c, _ = w4("Meer info op https://www.belasting-dienst-nl.example/aanslag", "aanslag@belasting-dienst-nl.example")
        self.assertFalse(c.ok)
        self.assertIn("lijkt op de Belastingdienst", c.uitleg)

    def test_only_on_phishing_cards(self):
        # Other cards keep their checks and verdicts: W4 is not added, so no scorecard moves.
        body = "Log in op https://ing-zakelijk-verificatie.example om uw gegevens te bevestigen."
        for action in ("no_action", "send_reminder"):
            c, _ = w4(body, "x@ing-zakelijk-verificatie.example", action=action)
            self.assertIsNone(c, action)
        c, v = w4("Nieuwsbrief: lees verder op https://www.ondernemers.example/tips", "nieuws@ondernemers.example",
                  action="no_action")
        self.assertIsNone(c)
        self.assertEqual(v, "none")


class DemoPage(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        with contextlib.redirect_stdout(io.StringIO()):
            cls.ws = cli.demo_workspace(Path(cls.tmp.name) / "demo")
            cli.cmd_draft(cls.ws, make_planner("rules"))
        H = make_handler(cls.ws, "TOKEN")
        cls.page = H.__new__(H).index().decode()
        cls.cards = cls.ws.load("cards", [])

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_every_demo_phishing_card_names_its_ask(self):
        flagged = [c for c in self.cards if c["action"] == "flag_suspicious"]
        self.assertEqual(len(flagged), 3)
        for c in flagged:
            ch = next(ch for ch in c["checks"] if ch["rule"] == W4)
            self.assertFalse(ch["ok"], c.get("subject"))
            self.assertTrue(ch["uitleg"].startswith(("Het bericht vraagt je", "De link gaat", "De afzender")))

    def test_domain_shown_never_clickable(self):
        self.assertIn("De link gaat naar ing-zakelijk-verificatie.example.", self.page)
        hrefs = re.findall(r'href="([^"]*)"', self.page)
        self.assertFalse([h for h in hrefs if "example" in h or h.startswith("http")], hrefs)

    def test_official_list_link_text_unchanged(self):
        # The fake Belastingdienst card still names the official list URL as plain text (C13), not as a link.
        self.assertIn("staat niet op de officiële lijst (https://www.belastingdienst.nl/", self.page)
        self.assertNotIn('href="https://www.belastingdienst.nl', self.page)


if __name__ == "__main__":
    unittest.main()
