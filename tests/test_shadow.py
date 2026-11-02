# SPDX-License-Identifier: Apache-2.0
"""Tests: importers, rule planner + checks on the synthetic set, feedback/summary, web UI, planner adapters,
and the "no code path can send money" guarantees. Run: python3 -m unittest discover -s tests -v"""
from __future__ import annotations

import ast
import contextlib
import io
import json
import re
import socket
import tempfile
import threading
import unittest
import urllib.error
import urllib.parse
import urllib.request
from decimal import Decimal
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock

from countersign_shadow import feedback as fb
from countersign_shadow import iban, money, samples
from countersign_shadow.__main__ import demo_workspace, main
from countersign_shadow.importers import inbox as inbox_imp
from countersign_shadow.importers import transactions as tx
from countersign_shadow.planners import make_planner, normalise_proposal
from countersign_shadow.planners.llm import ClaudePlanner, OpenAICompatPlanner, extract_json
from countersign_shadow.shadow import draft_cards, run
from countersign_shadow.store import Workspace

PKG = Path(__file__).resolve().parent.parent / "countersign_shadow"


class Base(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        with contextlib.redirect_stdout(io.StringIO()):
            self.ws = demo_workspace(self.tmp / "ws")
        self.src = self.tmp / "ws" / "samples"

    def tearDown(self):
        self._tmp.cleanup()

    def cards(self):
        cards = run(self.ws, make_planner("rules"))
        origin = {m["id"]: m["origin"] for m in self.ws.messages}
        return {origin[c["source_id"]]: c for c in cards}


class TestMoneyAndIban(unittest.TestCase):
    def test_parse_signed(self):
        for s, want in [("+99,01", "99.01"), ("-10,00", "-10.00"), ("1.234,56", "1234.56"), ("-1,234.56", "-1234.56"),
                        ("12.5", "12.50"), ("38.00", "38.00"), ("1.234", "1234.00"), ("€ 7", "7.00")]:
            self.assertEqual(money.parse_signed(s), Decimal(want), s)
        with self.assertRaises(money.MoneyError):
            money.parse_signed("abc")

    def test_find_amounts(self):
        got = [str(d) for d, *_ in money.find_amounts("Totaal € 1.284,00 of 38.00 EUR en 450,- euro, niet 2026")]
        self.assertEqual(got, ["1284.00", "38.00", "450.00"])

    def test_find_amounts_trailing_euro_sign(self):
        # AMOUNT_RE's trailing-currency branch ended in a plain \b, but \b never matches right after "€"
        # unless the next char is a \w char -- so "7,50 €" at end of line/sentence/string never matched.
        cases = {
            "Totaal: 7,50 €": ["7.50"],  # end of string
            "Totaal: 7,50 €.": ["7.50"],  # end of sentence (punctuation after)
            "bedrag 1.234,56 € te betalen": ["1234.56"],  # mid-sentence (space after)
            "250€.": ["250.00"],  # no space before €
            "250€": ["250.00"],
            "Totaal: 250\xa0€": ["250.00"],  # NBSP between number and €
            "Totaal: 250\xa0EUR": ["250.00"],  # NBSP + EUR suffix
            "Totaal: 250 EUR.": ["250.00"],  # EUR suffix already worked, stays working
        }
        for text, want in cases.items():
            with self.subTest(text=text):
                self.assertEqual([str(d) for d, *_ in money.find_amounts(text)], want)
        # a word character glued right after "€" is not a real amount boundary
        self.assertEqual(money.find_amounts("prijs is 7,50 €abc"), [])

    def test_parse_amount_rejects_float_and_negative(self):
        for bad in (1.5, "-3", "0", "1.234"):
            with self.assertRaises(money.MoneyError):
                money.parse_amount(bad)

    def test_iban(self):
        self.assertTrue(iban.is_valid("NL91 ABNA 0417 1643 00"))
        self.assertFalse(iban.is_valid("NL91ABNA0417164301"))
        self.assertFalse(iban.is_valid(samples.BROKEN))
        self.assertEqual(iban.find_ibans(f"betaal op {samples.fmt_nl(samples.KARTON)} t.n.v. X"), [samples.KARTON])


class TestImporters(Base):
    def test_eml_and_mbox_give_same_messages(self):
        eml = inbox_imp.import_paths([self.src / "inbox"])
        mbx = inbox_imp.import_paths([self.src / "inbox.mbox"])
        self.assertEqual(len(eml), 21)
        self.assertEqual({m["id"] for m in eml}, {m["id"] for m in mbx})
        m = next(x for x in eml if x["origin"].startswith("06"))
        self.assertEqual(m["from"], "lena@example.com")
        self.assertIn("€ 38,00", m["body"])
        self.assertEqual(m["date"], "2026-10-19")

    def test_html_only_mail(self):
        p = self.tmp / "h.eml"
        p.write_text("From: a@b.example\nSubject: x\nContent-Type: text/html; charset=utf-8\n\n"
                     "<p>Factuur F-1 &euro; 12,00</p><script>evil()</script>", encoding="utf-8")
        m = inbox_imp.import_paths([p])[0]
        self.assertIn("€ 12,00", m["body"])
        self.assertNotIn("evil", m["body"])

    def test_detect_formats(self):
        for name, kind in [("paypal.csv", "paypal"), ("ing.csv", "ing"), ("rabobank.csv", "rabobank"),
                           ("abnamro.txt", "abnamro")]:
            self.assertEqual(tx.import_file(self.src / name)[0], kind)
        bad = self.tmp / "x.csv"
        bad.write_text("a,b,c\n1,2,3\n")
        with self.assertRaises(tx.ImportError_):
            tx.import_file(bad)

    def test_paypal_rows(self):
        _, rows = tx.import_file(self.src / "paypal.csv")
        lena = next(r for r in rows if r["reference"] == "1042")
        self.assertEqual((lena["amount"], lena["email"], lena["date"]), ("38.00", "lena@example.com", "2026-10-14"))
        ana = [r for r in rows if r["email"] == "ana.photo@example.com"]
        self.assertTrue(all(Decimal(r["amount"]) < 0 for r in ana))

    def test_ing_rows_both_delimiters(self):
        _, rows = tx.import_file(self.src / "ing.csv")
        self.assertEqual(rows[0]["amount"], "-287.15")
        self.assertEqual(rows[0]["iban"], samples.KARTON)
        self.assertEqual(rows[0]["date"], "2026-07-25")
        self.assertIn("F-2026-0301", rows[0]["reference"])
        old = self.tmp / "ing_old.csv"  # older comma version without the last two columns
        old.write_text('"Datum","Naam / Omschrijving","Rekening","Tegenrekening","Code","Af Bij","Bedrag (EUR)",'
                       '"MutatieSoort","Mededelingen"\n"20260101","X BV","NL1","NL2","GT","Bij","1.234,50",'
                       '"Online bankieren","Omschrijving: test"\n', encoding="utf-8")
        kind, rows = tx.import_file(old)
        self.assertEqual((kind, rows[0]["amount"]), ("ing", "1234.50"))

    def test_rabobank_rows(self):
        _, rows = tx.import_file(self.src / "rabobank.csv")
        self.assertEqual(len(rows), 4)
        self.assertEqual(rows[2]["amount"], "275.00")
        self.assertEqual(rows[0]["iban"], samples.BOEKHOUDER)
        self.assertEqual(rows[0]["counterparty"], "Administratiekantoor Jansen")

    def test_abnamro_rows_both_description_styles(self):
        _, rows = tx.import_file(self.src / "abnamro.txt")
        self.assertEqual(rows[0]["iban"], samples.GLAZUUR)
        self.assertEqual(rows[0]["counterparty"], "Glazuurhandel De Vries")
        self.assertEqual(rows[1]["iban"], samples.CAFE)
        self.assertEqual(rows[1]["amount"], "440.00")
        self.assertIn("2026-027", rows[1]["description"])

    def test_abnamro_structured_opposing_columns(self):
        # 10-column no-header layout confirmed against firefly-iii's maintained "nl/abnamro/default.json"
        # import config (github.com/firefly-iii/import-configurations, checked 2026-10-24): account, currency,
        # transaction date, 2 ignored columns, interest date, amount, description, opposing name, opposing IBAN.
        # Here the opposing name/IBAN are their own columns, NOT embedded as labelled text in the description
        # (unlike the /TRTP/ and "IBAN: ... Naam: ..." styles in samples/abnamro.txt).
        line = "\t".join(["123456789", "EUR", "20261001", "20261001", "500,00", "719,00", "+219,00",
                           "Factuur 2026-099", "Glazuurhandel De Vries", samples.GLAZUUR])
        p = self.tmp / "abn_structured.txt"
        p.write_text(line + "\n", encoding="utf-8")
        kind, rows = tx.import_file(p, fmt="abnamro")
        self.assertEqual(kind, "abnamro")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["amount"], "219.00")
        self.assertEqual(rows[0]["counterparty"], "Glazuurhandel De Vries")
        self.assertEqual(rows[0]["iban"], samples.GLAZUUR)

    def test_paypal_dutch_headers(self):
        # Dutch headers confirmed against SnelStart's PayPal-import help page (kennispleinweb.snelstart.nl/nl/
        # paypal, checked 2026-10-24), which lists the exact 19 Dutch field names PayPal's own
        # "Activiteitendownload" export uses.
        header = ("Datum,Tijd,Tijdzone,Naam,Type,Status,Valuta,Bruto,Kosten,Net,Van e-mailadres,"
                  "Naar e-mailadres,Transactiereferentie,BTW,Reference TxnID,Factuurnummer,"
                  "Ontvangstbewijsreferentie,Saldo,Onderwerp\n")
        row = ('14/10/2026,10:00:00,CEST,"Lena Bakker",Website-betaling,Voltooid,EUR,38.00,0.00,38.00,'
              'lena@example.com,sanne@studioklei.example,1AB23456CD789012E,0.00,,1042,,38.00,Mok blauw\n')
        p = self.tmp / "paypal_nl.csv"
        p.write_text(header + row, encoding="utf-8")
        kind, rows = tx.import_file(p, fmt="paypal")
        self.assertEqual((kind, len(rows)), ("paypal", 1))
        r = rows[0]
        self.assertEqual((r["amount"], r["email"], r["date"], r["reference"]),
                         ("38.00", "lena@example.com", "2026-10-14", "1042"))

    def test_missing_amount_column_fails_loudly_in_dutch(self):
        cases = {
            "paypal": "Datum,Naam,Status\n14/10/2026,X,Voltooid\n",
            "ing": '"Datum";"Naam / Omschrijving";"Af Bij"\n"20260101";"X";"Bij"\n',
            "rabobank": '"Datum","Naam tegenpartij"\n"2026-01-01","X"\n',
        }
        for fmt, content in cases.items():
            with self.subTest(fmt):
                p = self.tmp / f"missing_{fmt}.csv"
                p.write_text(content, encoding="utf-8")
                with self.assertRaises(tx.ImportError_) as cm:
                    tx.import_file(p, fmt=fmt)
                self.assertIn("bestand", str(cm.exception))
                self.assertIn("niet gevonden", str(cm.exception))
        p = self.tmp / "missing_abnamro.txt"
        p.write_text("Transactiedatum\tOmschrijving\n20260101\tX\n", encoding="utf-8")
        with self.assertRaises(tx.ImportError_) as cm:
            tx.import_file(p, fmt="abnamro")
        self.assertIn("bedragkolom", str(cm.exception))

    def test_camt053_rows(self):
        # Structure confirmed against a real camt.053.001.02 sample (jasperkrijgsman/dutch-sepa-iso20022,
        # GitHub, checked 2026-10-24): Document/BkToCstmrStmt/Stmt/Ntry/NtryDtls/TxDtls/RltdPties/Dbtr|Cdtr.
        asnb = iban.make_nl("ASNB", "0123456789")
        xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<Document xmlns="urn:iso:std:iso:20022:tech:xsd:camt.053.001.02">
  <BkToCstmrStmt>
    <Stmt>
      <Acct><Id><IBAN>{asnb}</IBAN></Id></Acct>
      <Ntry>
        <Amt Ccy="EUR">219.00</Amt>
        <CdtDbtInd>DBIT</CdtDbtInd>
        <BookgDt><Dt>2026-10-01</Dt></BookgDt>
        <NtryDtls><TxDtls>
          <Refs><EndToEndId>F-2026-099</EndToEndId></Refs>
          <RltdPties>
            <Cdtr><Nm>Glazuurhandel De Vries</Nm></Cdtr>
            <CdtrAcct><Id><IBAN>{samples.GLAZUUR}</IBAN></Id></CdtrAcct>
          </RltdPties>
          <RmtInf><Ustrd>Factuur 2026-099</Ustrd></RmtInf>
        </TxDtls></NtryDtls>
      </Ntry>
      <Ntry>
        <Amt Ccy="EUR">450.00</Amt>
        <CdtDbtInd>CRDT</CdtDbtInd>
        <BookgDt><Dt>2026-10-02</Dt></BookgDt>
        <NtryDtls><TxDtls>
          <RltdPties>
            <Dbtr><Nm>Cafe Blauw</Nm></Dbtr>
            <DbtrAcct><Id><IBAN>{samples.CAFE}</IBAN></Id></DbtrAcct>
          </RltdPties>
          <RmtInf><Ustrd>Factuur 2026-031</Ustrd></RmtInf>
        </TxDtls></NtryDtls>
      </Ntry>
    </Stmt>
  </BkToCstmrStmt>
</Document>"""
        p = self.tmp / "statement.xml"
        p.write_text(xml, encoding="utf-8")
        kind, rows = tx.import_file(p)
        self.assertEqual((kind, len(rows)), ("camt053", 2))
        self.assertEqual((rows[0]["amount"], rows[0]["iban"], rows[0]["counterparty"], rows[0]["reference"]),
                         ("-219.00", samples.GLAZUUR, "Glazuurhandel De Vries", "F-2026-099"))
        self.assertEqual((rows[1]["amount"], rows[1]["iban"]), ("450.00", samples.CAFE))

    def test_camt053_bad_xml_and_no_entries(self):
        with self.assertRaises(tx.ImportError_):
            tx.import_file(self._write("broken.xml", "<Document><notclosed></Document>"), fmt="camt053")
        empty = ('<?xml version="1.0"?><Document xmlns="urn:iso:std:iso:20022:tech:xsd:camt.053.001.02">'
                 "<BkToCstmrStmt><Stmt></Stmt></BkToCstmrStmt></Document>")
        with self.assertRaises(tx.ImportError_):
            tx.import_file(self._write("empty.xml", empty), fmt="camt053")

    def test_mt940_rows_with_abn_style_structured_narrative(self):
        # :61:/:86: pairing and field formats confirmed against the actively maintained WoLpH/mt940 Python
        # library's README (checked 2026-10-24): YYMMDD date, C/D indicator, decimal-comma amount. The two
        # :86: narrative styles reuse parse_abn_description, already confirmed for ABN AMRO.
        karton = samples.KARTON
        content = (
            ":20:STMT001\n:25:NL02KNAB0123456789EUR\n:28C:1/1\n:60F:C261001EUR1000,00\n"
            ":61:2610010101D219,00NTRFNONREF\n"
            f":86:/TRTP/SEPA OVERBOEKING/IBAN/{samples.GLAZUUR}/NAME/Glazuurhandel De Vries/REMI/Factuur "
            "2026-099/EREF/F-2026-099\n"
            ":61:2610020102C450,00NTRFNONREF\n"
            f":86:IBAN: {karton} Naam: Kartonfabriek Noord Omschrijving: Factuur 2026-031\n"
            ":62F:C261002EUR1231,00\n"
        )
        p = self._write("statement.sta", content)
        kind, rows = tx.import_file(p)
        self.assertEqual((kind, len(rows)), ("mt940", 2))
        self.assertEqual((rows[0]["amount"], rows[0]["iban"], rows[0]["counterparty"], rows[0]["reference"]),
                         ("-219.00", samples.GLAZUUR, "Glazuurhandel De Vries", "F-2026-099"))
        self.assertEqual((rows[1]["amount"], rows[1]["iban"], rows[1]["counterparty"]),
                         ("450.00", karton, "Kartonfabriek Noord"))

    def test_mt940_no_transaction_lines_fails_loudly(self):
        with self.assertRaises(tx.ImportError_):
            tx.import_file(self._write("empty.sta", ":20:STMT001\n:28C:1/1\n:60F:C261001EUR1000,00\n"),
                           fmt="mt940")

    def test_volksbank_rows(self):
        # Header confirmed against RegioBank's own help page (www.regiobank.nl/zakelijk/service/
        # rekeningafschriften.html, checked 2026-10-24); SNS and RegioBank folded into the "ASN Bank" brand
        # on 2025-07-01 (Yuki), all three share de Volksbank's platform -- not independently re-confirmed for
        # ASN's/SNS's own (pre-merger) export, see docs/FORMATS.md.
        asnb = iban.make_nl("ASNB", "0123456789")
        header = ("Datum;Je rekening;Van / naar;Naam;Valuta saldo;Saldo voor boeking;Valuta boeking;"
                  "Bedrag bij/af;Verwerkingsdatum;Valutadatum;Code;Type;Volgnummer;Betalingskenmerk;"
                  "Omschrijving;Afschriftnummer;Categorie\n")
        row = (f"20261001;{asnb};{samples.GLAZUUR};Glazuurhandel De Vries;EUR;1000,00;EUR;-219,00;"
               "20261001;20261001;BA;Betaling;1;F-2026-099;Factuur 2026-099;1;Inkoop\n")
        p = self._write("volksbank.csv", header + row)
        kind, rows = tx.import_file(p)
        self.assertEqual((kind, len(rows)), ("volksbank", 1))
        r = rows[0]
        self.assertEqual((r["amount"], r["iban"], r["counterparty"], r["date"], r["reference"]),
                         ("-219.00", samples.GLAZUUR, "Glazuurhandel De Vries", "2026-10-01", "F-2026-099"))

    def test_volksbank_missing_amount_column_fails_loudly_in_dutch(self):
        p = self._write("bad_volksbank.csv", "Datum;Je rekening;Naam\n20261001;NL1;X\n")
        with self.assertRaises(tx.ImportError_) as cm:
            tx.import_file(p, fmt="volksbank")
        self.assertIn("niet gevonden", str(cm.exception))

    def _write(self, name: str, content: str) -> Path:
        p = self.tmp / name
        p.write_text(content, encoding="utf-8")
        return p

    def test_folder_import_finds_eml_mbox_dir_and_bank_files(self):
        from countersign_shadow.__main__ import import_folder
        import shutil
        folder = self.tmp / "mijn-gegevens"
        (folder / "Inbox.mbox").mkdir(parents=True)  # Apple Mail export: a folder with an extensionless "mbox"
        shutil.copy(self.src / "inbox.mbox", folder / "Inbox.mbox" / "mbox")
        (folder / "Inbox.mbox" / "table_of_contents").write_bytes(b"\x00binary")
        shutil.copy(self.src / "rabobank.csv", folder / "rabo.csv")
        ws = Workspace(self.tmp / "fresh")
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(import_folder(ws, folder), (21, 4))

    def test_reimport_is_idempotent(self):
        before = len(self.ws.transactions)
        with contextlib.redirect_stdout(io.StringIO()):
            main(["--data", str(self.ws.root), "import-transactions", str(self.src / "ing.csv")])
        self.assertEqual(len(self.ws.transactions), before)


class TestRulesAndChecks(Base):
    def test_expected_action_and_verdict_for_every_sample(self):
        cards = self.cards()
        self.assertEqual(len(cards), len(samples.EXPECTED))
        for name, (action, verdict) in samples.EXPECTED.items():
            with self.subTest(name):
                self.assertEqual((cards[name]["action"], cards[name]["verdict"]), (action, verdict),
                                 cards[name]["reasons"])

    def _failed(self, card):
        return {c["rule"].split()[0] for c in card["checks"] if not c["ok"]}

    def test_new_iban_fraud_is_caught_by_history(self):
        c = self.cards()["04-nieuwe-iban-fraude.eml"]
        self.assertIn("C5", self._failed(c))
        self.assertIn("IBAN CHANGED", next(x["detail"] for x in c["checks"] if x["rule"].startswith("C5")))
        self.assertTrue({"W1", "W2"} <= self._failed(c))

    def test_specific_reasons(self):
        cards = self.cards()
        self.assertEqual(self._failed(cards["02-herinnering-glazuur-al-betaald.eml"]), {"C6"})
        self.assertTrue({"C4", "C13"} <= self._failed(cards["14-nep-belasting.eml"]))  # C13: not an official IBAN
        self.assertEqual(self._failed(cards["16-dubbele-factuur.eml"]), {"C11", "C15"})
        self.assertIn("C7", self._failed(cards["07-refund-te-hoog.eml"]))
        self.assertIn("C8", self._failed(cards["09-onbekende-freelancer.eml"]))
        self.assertEqual(self._failed(cards["11-openstaand-al-betaald.eml"]), {"C10"})
        self.assertIn("C3", self._failed(cards["20-grote-factuur-glazuur.eml"]))

    def test_without_history_known_suppliers_are_not_trusted(self):
        self.ws.save("transactions", [])
        c = self.cards()["01-herinnering-karton.eml"]
        self.assertEqual(c["verdict"], "blocked")
        self.assertIn("C5", self._failed(c))

    def test_policy_file_overrides(self):
        (self.ws.root / "policy.json").write_text(json.dumps({"limits": {"pay_invoice": "2000.00"}}))
        self.assertEqual(self.cards()["20-grote-factuur-glazuur.eml"]["verdict"], "ok")

    def test_lying_planner_is_caught(self):
        class Liar:
            name = "liar"

            def plan(self, msgs):
                m = next(x for x in msgs if x["origin"].startswith("01"))
                return [normalise_proposal(m["id"], {
                    "action": "pay_invoice", "amount": "3120.40", "payee": {"iban": samples.FRAUD},
                    "invoice_ref": "F-2026-0412", "evidence_quote": "Pay now, it is fine."})]
        c = draft_cards(self.ws, Liar())[0]
        self.assertEqual(c["verdict"], "blocked")
        self.assertTrue({"C3", "V1", "V2", "V3", "C5"} <= self._failed(c))


class TestFeedback(Base):
    def test_record_and_anonymised_summary(self):
        cards = list(self.cards().values())
        fb.record(self.ws, cards[0], "right", "Klopt, deze betaal ik altijd.", may_quote=True)
        fb.record(self.ws, cards[3], "dangerous",
                  "Bijna ingetrapt! Mail van info@kartonfabriek.example, IBAN NL20INGB0001234567, € 1.284,00, bel 06-12345678",
                  may_quote=True)
        fb.record(self.ws, cards[5], "wrong", "Lena is mijn zus, privé", may_quote=False)
        with self.assertRaises(ValueError):
            fb.record(self.ws, cards[1], "maybe")
        s = fb.summary(self.ws)
        self.assertEqual(s["cards_reviewed"], 3)
        self.assertEqual(s["marks"], {"right": 1, "dangerous": 1, "wrong": 1})
        blob = json.dumps(s, ensure_ascii=False) + fb.summary_markdown(s)
        for leak in ("kartonfabriek.example", "NL20INGB", "1.284", "12345678", "Lena", "zus", "Betalingsherinnering",
                     "lena@example.com"):
            self.assertNotIn(leak, blob)
        self.assertEqual(len(s["quotes"]), 2)
        self.assertIn("[e-mail]", s["quotes"][1]["quote"])

    def test_summary_command_writes_share_files(self):
        self.cards()
        with contextlib.redirect_stdout(io.StringIO()):
            main(["--data", str(self.ws.root), "summary"])
        self.assertTrue((self.ws.root / "deel-mij" / "samenvatting.md").exists())
        json.loads((self.ws.root / "deel-mij" / "samenvatting.json").read_text())


class TestWeb(Base):
    def setUp(self):
        super().setUp()
        self.cards()
        from countersign_shadow.web import make_server
        self.srv = make_server(self.ws, 0)
        self.base = f"http://127.0.0.1:{self.srv.server_address[1]}"
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def tearDown(self):
        self.srv.shutdown()
        self.srv.server_close()
        super().tearDown()

    def post(self, path, data):
        req = urllib.request.Request(self.base + path, data=urllib.parse.urlencode(data).encode(), method="POST")
        opener = urllib.request.build_opener(NoRedirect)
        try:
            return opener.open(req).status
        except urllib.error.HTTPError as exc:
            return exc.code

    def test_page_feedback_and_csrf(self):
        page = urllib.request.urlopen(self.base + "/").read().decode()
        self.assertIn("Countersign Shadow", page)
        self.assertNotRegex(page.lower(), r">\s*(approve|goedkeuren|betalen|pay now|send)\s*<")
        token = re.search(r'name="csrf" value="([^"]+)"', page).group(1)
        card = re.search(r'name="card" value="([^"]+)"', page).group(1)
        self.assertEqual(self.post("/feedback", {"csrf": "nope", "card": card, "mark": "right"}), 403)
        self.assertEqual(self.post("/feedback", {"csrf": token, "card": card, "mark": "dangerous",
                                                 "comment": "eng", "may_quote": "1"}), 303)
        self.assertEqual(self.ws.load("feedback", {})[card]["mark"], "dangerous")
        self.assertEqual(self.post("/approve", {"csrf": token, "card": card}), 404)
        self.assertIn("samenvatting", urllib.request.urlopen(self.base + "/samenvatting").read().decode().lower())
        self.assertEqual(self.post("/samenvatting", {"csrf": token}), 200)
        self.assertTrue((self.ws.root / "deel-mij" / "samenvatting.json").exists())

    def test_binds_localhost_only(self):
        self.assertEqual(self.srv.server_address[0], "127.0.0.1")


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


class StubLLM(BaseHTTPRequestHandler):
    """Fake OpenAI-compatible server. Answers every message with a pay_invoice for the IBAN it finds."""
    seen: list = []

    def log_message(self, *a):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        StubLLM.seen.append((self.path, body))
        text = body["messages"][-1]["content"]
        found = iban.find_ibans(text)
        if found:
            reply = {"action": "pay_invoice", "amount": "312.40", "currency": "EUR",
                     "payee": {"name": "x", "iban": found[-1], "email": ""}, "invoice_ref": "",
                     "order_ref": "", "evidence_quote": "", "rationale": "stub"}
            content = "Sure! ```json\n" + json.dumps(reply) + "\n```"
        else:
            content = "I think nothing is needed."  # not JSON -> must degrade to no_action
        out = json.dumps({"choices": [{"message": {"role": "assistant", "content": content}}]}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)


class TestPlannerAdapters(Base):
    def test_local_adapter_against_stub_server(self):
        srv = ThreadingHTTPServer(("127.0.0.1", 0), StubLLM)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            StubLLM.seen = []
            p = OpenAICompatPlanner(base_url=f"http://127.0.0.1:{srv.server_address[1]}/v1", model="tiny-test")
            cards = {c["source_id"]: c for c in draft_cards(self.ws, p)}
        finally:
            srv.shutdown()
            srv.server_close()
        self.assertEqual(len(StubLLM.seen), 21)
        path, body = StubLLM.seen[0]
        self.assertEqual(path, "/v1/chat/completions")
        self.assertEqual(body["model"], "tiny-test")
        self.assertEqual(body["messages"][0]["role"], "system")
        origin = {m["origin"]: m["id"] for m in self.ws.messages}
        c01 = cards[origin["01-herinnering-karton.eml"]]
        self.assertEqual(c01["verdict"], "blocked")  # the stub gives no evidence quote -> V1 fails
        self.assertEqual({x["rule"][:2] for x in c01["checks"] if not x["ok"]}, {"V1"})
        self.assertEqual(cards[origin["04-nieuwe-iban-fraude.eml"]]["verdict"], "blocked")  # wrong amount + new IBAN
        self.assertEqual(cards[origin["12-nieuwsbrief.eml"]]["action"], "no_action")

    def test_local_adapter_refuses_remote_hosts(self):
        with self.assertRaises(ValueError):
            OpenAICompatPlanner(base_url="https://api.example.com/v1")
        with self.assertRaises(ValueError):
            OpenAICompatPlanner(base_url="file:///etc/passwd")
        OpenAICompatPlanner(base_url="https://api.example.com/v1", allow_remote=True)

    def test_unreachable_planner_degrades_to_no_action(self):
        p = OpenAICompatPlanner(base_url="http://127.0.0.1:9/v1", timeout=2)
        c = draft_cards(self.ws, p, messages=self.ws.messages[:1])[0]
        self.assertEqual(c["action"], "no_action")
        self.assertIn("planner error", c["rationale"])

    def test_claude_adapter_with_fake_client(self):
        calls = []

        class Block:
            type = "text"
            text = '{"action": "flag_suspicious", "rationale": "phish"}'

        class Resp:
            stop_reason = "end_turn"
            content = [Block()]

        class Messages:
            def create(self, **kw):
                calls.append(kw)
                return Resp()

        class Client:
            messages = Messages()

        p = ClaudePlanner(client=Client())
        c = draft_cards(self.ws, p, messages=self.ws.messages[:2])
        self.assertEqual([x["verdict"] for x in c], ["flagged", "flagged"])
        self.assertEqual(calls[0]["model"], "claude-haiku-4-5")
        self.assertIn("untrusted", calls[0]["messages"][0]["content"])

    def test_claude_planner_needs_key(self):
        with mock.patch.dict("os.environ", {"ANTHROPIC_API_KEY": ""}):
            with self.assertRaises(RuntimeError):
                ClaudePlanner()

    def test_extract_json(self):
        self.assertEqual(extract_json('blah {"a": {"b": 1}} tail'), {"a": {"b": 1}})
        self.assertIsNone(extract_json("no json"))
        self.assertEqual(normalise_proposal("m", {"action": "wire_everything"})["action"], "no_action")


NETWORK_MODULES = {"urllib.request", "http.client", "socket", "ssl", "requests", "httpx", "aiohttp", "smtplib",
                   "ftplib", "telnetlib", "xmlrpc.client", "anthropic", "paypalrestsdk", "openai"}


def _imports(path: Path) -> set[str]:
    names = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            names |= {a.name for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
            names |= {f"{node.module}.{a.name}" for a in node.names}
    return names


class TestNoMoneyPath(Base):
    """Shadow mode's core promise: there is no code path that can move money or send anything."""

    def sources(self, vendored: bool = False):
        """Our own modules; with vendored=True also the unmodified third-party copies in _vendor/."""
        return sorted(p for p in PKG.rglob("*.py") if vendored or "_vendor" not in p.relative_to(PKG).parts)

    def test_only_the_planner_module_can_open_connections(self):
        users = {p.relative_to(PKG).as_posix() for p in self.sources(vendored=True)
                 if any(i == m or i.startswith(m + ".") for i in _imports(p) for m in NETWORK_MODULES)}
        self.assertEqual(users, {"planners/llm.py"})

    def test_no_payment_or_mail_endpoints_in_code(self):
        bad = re.compile(r"(?i)paypal\.com|api-m\.|/v\d/payments|/payouts|checkout/orders|smtp|sendmail|"
                         r"psd2|openbanking|/v\d/transfers|ing\.nl/|rabobank\.nl/|abnamro\.nl/")
        for p in self.sources():
            self.assertIsNone(bad.search(p.read_text(encoding="utf-8")), p.name)

    def test_no_execute_style_functions(self):
        bad = re.compile(r"(?i)^(execute|approve|pay|transfer|send|payout|refund|capture|submit|wire)(_|$)")
        for p in self.sources():
            for node in ast.walk(ast.parse(p.read_text(encoding="utf-8"))):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    self.assertIsNone(bad.match(node.name), f"{p.name}: {node.name}")

    def test_full_pipeline_runs_with_network_disabled(self):
        def deny(*a, **k):
            raise AssertionError("network access attempted")
        with mock.patch.object(socket.socket, "connect", deny), mock.patch.object(socket, "create_connection", deny):
            with contextlib.redirect_stdout(io.StringIO()):
                ws = demo_workspace(self.tmp / "offline")
            cards = run(ws, make_planner("rules"))
            fb.record(ws, cards[0], "right")
            fb.summary(ws)
        self.assertEqual(len(cards), 21)

    def test_cards_have_no_executable_state(self):
        for c in self.cards().values():
            self.assertNotIn(c["verdict"], {"executed", "approved", "paid", "sent"})
            self.assertFalse({"executed_at", "payment", "transaction_id"} & set(c))


if __name__ == "__main__":
    unittest.main()
