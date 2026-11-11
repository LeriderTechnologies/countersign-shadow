# SPDX-License-Identifier: Apache-2.0
"""The review page and the start window are Dutch for a pilot user: plain check names instead of the
internal rule ids, amounts as "€ 96,80" (nl-NL) and dates as "22 oktober 2026". Synthetic demo data only.

Snapshot: tests/data/dutch_review_page.txt is the visible text of the demo review page. After an intended change,
regenerate it with  UPDATE_SNAPSHOT=1 python3 -m unittest tests.test_dutch_review_page  and read the diff.
"""
from __future__ import annotations

import contextlib
import html
import io
import os
import re
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from decimal import Decimal
from pathlib import Path

from countersign_shadow import __main__ as cli
from countersign_shadow import feedback as fbmod
from countersign_shadow import launcher, nl
from countersign_shadow.checks import Check
from countersign_shadow.planners import make_planner
from countersign_shadow.web import make_handler, make_server, md_html, render_card

SNAPSHOT = Path(__file__).resolve().parent / "data" / "dutch_review_page.txt"
RULE_ID_RE = re.compile(r"\b[CVWP]\d{1,2} [a-z]")         # "W1 no pressure language"
DOT_AMOUNT_RE = re.compile(r"(?<![\d.,])\d+\.\d{2}(?![\d.,])")  # "96.80"
ISO_DATE_RE = re.compile(r"\b\d{4}-\d{2}-\d{2}\b")
ENGLISH = ("planner:", "none found", "first of its kind", "paid on", "never paid", "not found", "checksum",
           "IBAN CHANGED", "per-action limit", "no quote")


def visible_text(page: str) -> str:
    page = re.sub(r"<style>.*?</style>", "", page, flags=re.S)
    page = re.sub(r"<[^>]+>", "\n", page)
    return "\n".join(x.strip() for x in html.unescape(page).splitlines() if x.strip()) + "\n"


def outside_messages(page: str) -> str:
    """The page minus the quoted mail bodies (<pre>): those are the user's own text, in whatever language."""
    return visible_text(re.sub(r"<pre>.*?</pre>", "", page, flags=re.S))


class Formatting(unittest.TestCase):
    def test_amounts(self):
        self.assertEqual(nl.bedrag("96.80"), "€ 96,80")
        self.assertEqual(nl.bedrag(Decimal("1250")), "€ 1.250,00")
        self.assertEqual(nl.bedrag("1234567.5"), "€ 1.234.567,50")
        self.assertEqual(nl.bedrag(0.1), "€ 0,10")
        self.assertEqual(nl.bedrag("-12.5"), "€ -12,50")
        self.assertEqual(nl.bedrag("38"), "€ 38,00")
        self.assertEqual(nl.bedrag(None), "")
        self.assertEqual(nl.bedrag("veel"), "veel")  # no number: shown as it came
        self.assertEqual(nl.bedragen(["1312.40", "12.00"]), "€ 1.312,40, € 12,00")

    def test_dates(self):
        self.assertEqual(nl.datum("2026-10-22"), "22 oktober 2026")
        self.assertEqual(nl.datum("2026-01-05"), "5 januari 2026")
        self.assertEqual(nl.datum("2026-03-01T09:30:00+00:00"), "1 maart 2026, 09:30")
        self.assertEqual(nl.datum("2026-03-01 00:00"), "1 maart 2026")
        self.assertEqual(nl.datum("?"), "?")
        self.assertEqual(nl.datum("22 oktober 2026"), "22 oktober 2026")
        self.assertEqual(nl.datum(""), "")

    def test_every_rule_has_a_dutch_name(self):
        src = (Path(cli.__file__).parent / "checks.py").read_text(encoding="utf-8")
        ids = set(re.findall(r'"([CVWP]\d{1,2}) [a-zA-Z]', src))
        self.assertTrue(ids)
        self.assertEqual(ids - set(nl.RULE_NL), set())
        for ok_txt, bad_txt in nl.RULE_NL.values():
            self.assertNotEqual(ok_txt, bad_txt)
        self.assertEqual(nl.regel("W1 no pressure language", True), "Geen druk of haast in het bericht")
        self.assertEqual(nl.regel("W1 no pressure language", False), "Het bericht zet je onder druk of maakt haast")
        self.assertNotIn("Z9", nl.regel("Z9 some new rule", False))

    def test_planner_names(self):
        self.assertEqual(nl.planner("rules (offline)"), "vaste regels, op deze computer")
        self.assertIn("Claude", nl.planner("Claude API (claude-x)"))
        self.assertEqual(nl.planner("local model (m @ h:1)"), "taalmodel op deze computer")


class ReviewPage(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        with contextlib.redirect_stdout(io.StringIO()):
            cls.ws = cli.demo_workspace(Path(cls.tmp.name) / "demo")
            cli.cmd_draft(cls.ws, make_planner("rules"))
        H = make_handler(cls.ws, "TOKEN")
        cls.page = H.__new__(H).index().decode()

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_no_rule_ids_dot_amounts_or_iso_dates(self):
        text = outside_messages(self.page)
        self.assertIsNone(RULE_ID_RE.search(text), RULE_ID_RE.search(text))
        self.assertEqual(DOT_AMOUNT_RE.findall(text), [])
        self.assertEqual(ISO_DATE_RE.findall(text), [])
        for word in ENGLISH:
            self.assertNotIn(word, text)
        self.assertIn("€ 312,40", text)
        self.assertIn("22 oktober 2026", text)
        self.assertIn("⚠ Het bericht zet je onder druk of maakt haast", text)

    def test_rule_ids_stay_in_the_cards(self):
        cards = self.ws.load("cards", [])
        rules = {ch["rule"] for c in cards for ch in c["checks"]}
        self.assertIn("W1 no pressure language", rules)
        self.assertTrue(all(re.match(r"\d+\.\d{2}$", c["amount"]) for c in cards if c.get("amount")))

    def test_snapshot(self):
        got = visible_text(self.page)
        if os.environ.get("UPDATE_SNAPSHOT") or not SNAPSHOT.exists():
            SNAPSHOT.write_text(got, encoding="utf-8")
        self.assertEqual(got, SNAPSHOT.read_text(encoding="utf-8"))

    def test_summary_is_dutch(self):
        cards = self.ws.load("cards", [])
        fbmod.record(self.ws, cards[0], "right", "Klopt, € 96,80 is juist", may_quote=True)
        md = fbmod.summary_markdown(fbmod.summary(self.ws))
        self.assertNotIn("Read this", md)
        self.assertNotIn("rules (offline)", md)
        self.assertNotIn("Per beoordelingsmethode", md)  # one method: no table of its own
        self.assertIsNone(ISO_DATE_RE.search(md.splitlines()[0]))
        for code in ("blocked", "pay_invoice", "flag_suspicious"):
            self.assertNotIn(f"| {code} |", md)
        page = md_html(md)
        self.assertNotIn("|", page)
        self.assertNotIn("# ", page)
        self.assertIn("<table", page)
        card = render_card(cards[0], {"body": ""}, fbmod.record(self.ws, cards[0], "dangerous"), "T")
        self.assertIn("Jouw oordeel: <b>gevaarlijk</b>", card)


class CheckTexts(unittest.TestCase):
    def card(self, checks, **kw):
        c = {"id": "c-1", "action": "pay_invoice", "action_nl": "Factuur betalen", "subject": "s", "from": "a@b.nl",
             "date": "2026-10-22", "planner": "rules (offline)", "verdict": "blocked", "verdict_nl": "x",
             "amount": "1250.00", "payee": {}, "rationale": "", "checks": [x.as_dict() for x in checks]}
        c.update(kw)
        return render_card(c, {"body": ""}, None, "T")

    def test_failed_and_passed_checks(self):
        out = self.card([Check("C5 IBAN known from your own payments", False, "never paid NL..", "block", "Uitleg."),
                         Check("W2 no change of bank details", True, "none found", "warn")])
        self.assertIn("✗ Je hebt nog nooit naar dit rekeningnummer betaald</li>", out)
        self.assertIn("✓ Geen nieuwe bankgegevens in het bericht</li>", out)
        self.assertIn("€ 1.250,00", out)
        self.assertNotIn("never paid", out)
        self.assertNotIn("C5", out)

    def test_limit_and_pdf_texts_use_dutch_amounts(self):
        from countersign_shadow.checks import Policy, evaluate
        msg = {"id": "m1", "subject": "Factuur F-1", "from": "x@lev.example", "from_name": "Lev BV",
               "date": "2026-10-22", "body": "Factuur F-1. Te betalen € 9.800,00 op NL91ABNA0417164300.",
               "pdfs": [{"name": "f.pdf", "text": "Totaal € 9.900,00 IBAN NL91ABNA0417164300",
                         "ibans": ["NL91ABNA0417164300"], "bad_ibans": [], "total": "9900.00"}]}
        prop = {"source_id": "m1", "action": "pay_invoice", "amount": "9800.00", "currency": "EUR",
                "payee": {"name": "Lev BV", "iban": "NL91ABNA0417164300"}, "evidence_quote": "Te betalen € 9.800,00",
                "invoice_ref": "F-1"}
        checks = {c.rule.split()[0]: c for c in evaluate(prop, msg, [], Policy())}
        self.assertIn("€ 9.800,00", checks["C3"].uitleg)
        self.assertIn("€ 9.900,00", checks["P4"].uitleg)
        self.assertNotIn("9800.00", checks["C3"].uitleg + checks["P4"].uitleg)


class Errors(unittest.TestCase):
    def test_review_and_start_window_errors_are_dutch(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        from countersign_shadow.store import Workspace
        srv = make_server(Workspace(Path(tmp.name) / "ws"), 0)
        app = launcher.Launcher(Path(tmp.name), Path(tmp.name) / "in", open_dir=lambda p: None)
        lsrv = launcher.make_launcher_server(app, 0)
        for s in (srv, lsrv):
            threading.Thread(target=s.serve_forever, daemon=True).start()
            self.addCleanup(s.server_close)
            self.addCleanup(s.shutdown)
            url = f"http://127.0.0.1:{s.server_address[1]}"
            with self.assertRaises(urllib.error.HTTPError) as cm:
                urllib.request.urlopen(url + "/bestaat-niet", timeout=5)
            self.assertEqual(cm.exception.read().decode("utf-8"), "Pagina niet gevonden.")
            with self.assertRaises(urllib.error.HTTPError) as cm:
                urllib.request.urlopen(urllib.request.Request(url + "/stop" if s is lsrv else url + "/feedback",
                                                              data=b"csrf=fout"), timeout=5)
            self.assertIn("laad de pagina opnieuw", cm.exception.read().decode("utf-8"))
        with urllib.request.urlopen(f"http://127.0.0.1:{lsrv.server_address[1]}/", timeout=5) as r:
            home = visible_text(r.read().decode("utf-8"))
        self.assertIsNone(RULE_ID_RE.search(home))
        self.assertEqual(DOT_AMOUNT_RE.findall(home), [])


if __name__ == "__main__":
    unittest.main()
