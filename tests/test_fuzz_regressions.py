# SPDX-License-Identifier: Apache-2.0
"""Regression tests for the root causes found by the Importer fuzz.

One test class per root cause (RC1..RC8), each with a minimised reproducer. Contract under test: an importer
never crashes; a bad row is skipped with a Dutch warning that names any IBAN on it; an unreadable file gives
one friendly Dutch error. Run: python3 -m unittest discover -s tests -v
"""
from __future__ import annotations

import ast
import struct
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

from countersign_shadow import checks, iban as ibanlib, money
from countersign_shadow.importers import attachments, msg as msglib
from countersign_shadow.importers import transactions as tx

try:
    from .fixtures import make_msg, make_pdf
except ImportError:  # run via `unittest discover -s tests` (tests/ is then not a package)
    from fixtures import make_msg, make_pdf

DATA = Path(__file__).resolve().parent / "data" / "fuzz"
REPO = Path(__file__).resolve().parent.parent

ING_HEADER = ('"Datum";"Naam / Omschrijving";"Rekening";"Tegenrekening";"Code";"Af Bij";"Bedrag (EUR)";'
              '"Mutatiesoort";"Mededelingen"')
IBAN_A = ibanlib.make_nl("INGB", "0001234567")
IBAN_B = ibanlib.make_nl("RABO", "0155667788")


def ing_row(iban: str, amount: str = "10,00", name: str = "Kartonfabriek Noord BV") -> str:
    return f'"20261001";"{name}";"NL67INGB0006543210";"{iban}";"GT";"Af";"{amount}";"Online bankieren";"F-1"'


class _Files(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def write(self, name: str, content: str | bytes) -> Path:
        p = self.tmp / name
        p.write_bytes(content if isinstance(content, bytes) else content.encode("utf-8"))
        return p


class RC1ExtraColumns(_Files):
    """fuzz #1 (4381x): AttributeError 'list' object has no attribute 'strip' in _norm_keys. A CSV row with more
    fields than the header (unquoted comma in a name, Excel re-save) gave csv.DictReader a None key with a list."""

    def test_row_with_extra_columns_is_skipped_with_warning_naming_its_iban(self):
        p = self.write("ing.csv", "\n".join([ING_HEADER, ing_row(IBAN_A), ing_row(IBAN_B) + ';"extra";"cols"']))
        warnings: list[str] = []
        kind, rows = tx.import_file(p, fmt="ing", warnings=warnings)
        self.assertEqual([r["iban"] for r in rows], [IBAN_A])
        self.assertEqual(len(warnings), 1)
        self.assertIn("regel 3", warnings[0])
        self.assertIn("meer kolommen", warnings[0])
        self.assertIn(IBAN_B, warnings[0])  # never a silently dropped payee IBAN

    def test_trailing_empty_column_is_not_a_problem(self):
        p = self.write("ing.csv", "\n".join([ING_HEADER, ing_row(IBAN_A) + ";"]))
        warnings: list[str] = []
        _, rows = tx.import_file(p, fmt="ing", warnings=warnings)
        self.assertEqual(len(rows), 1)
        self.assertEqual(warnings, [])

    def test_every_row_shifted_is_one_friendly_error(self):
        # minimised from the fuzz input: the first header character was lost, so every row is one column wider
        p = self.write("paypal.csv", 'Date,Gross,Transaction ID\n14/10/2026,"5,00",X1,extra\n')
        with self.assertRaises(tx.ImportError_) as cm:
            tx.import_file(p, fmt="paypal")
        self.assertNotIsInstance(cm.exception, tx.ImportCrash)
        self.assertIn("geen enkele regel", str(cm.exception))


class RC2LineEndings(_Files):
    """fuzz #2 (748x): csv.Error 'new-line character seen in unquoted field' in _rows. A lone '\\r' (old-Mac line
    endings, a file damaged in transfer) inside an unquoted field crashed the csv module."""

    def test_lone_carriage_returns_are_read_as_line_breaks(self):
        text = "\r".join([ING_HEADER.replace('"', ""), ing_row(IBAN_A).replace('"', ""),
                          ing_row(IBAN_B).replace('"', "")]) + "\r\x0b"
        warnings: list[str] = []
        _, rows = tx.import_file(self.write("ing.csv", text), fmt="ing", warnings=warnings)
        self.assertEqual([r["iban"] for r in rows], [IBAN_A, IBAN_B])
        self.assertEqual(warnings, [])

    def test_unreadable_csv_is_one_friendly_dutch_error(self):
        # an unterminated quote swallows the rest of the file into one field larger than the csv field limit
        text = ING_HEADER + "\n" + '"20261001";"' + "x" * 200_000 + "\n"
        with self.assertRaises(tx.ImportError_) as cm:
            tx.import_file(self.write("ing.csv", text), fmt="ing")
        self.assertNotIsInstance(cm.exception, tx.ImportCrash)
        self.assertIn("beschadigd of afgekapt", str(cm.exception))


class RC3HugeAmounts(_Files):
    """fuzz #5, #12, #13 (64x + 15x + 7x): decimal.InvalidOperation in money.parse_signed (ABN AMRO), MT940 flush
    and CAMT.053: an amount with > 28 digits (corrupted file) overflowed Decimal.quantize."""

    HUGE = "9" * 200

    def test_parse_signed_raises_money_error_not_invalid_operation(self):
        for s in (self.HUGE + ",00", "+" + self.HUGE + "440,00", "9" * 5000):
            with self.assertRaises(money.MoneyError):
                money.parse_signed(s)

    def test_abnamro_row_with_huge_amount_is_skipped_with_warning(self):
        ok = f"456123000\tEUR\t20260910\t1520,10\t1406,60\t20260910\t-113,50\tIBAN: {IBAN_A} Naam: A BV"
        bad = f"456123000\tEUR\t20260915\t1406,60\t1846,60\t20260915\t+{self.HUGE},00\tIBAN: {IBAN_B} Naam: B"
        warnings: list[str] = []
        _, rows = tx.import_file(self.write("abn.txt", ok + "\n" + bad + "\n"), fmt="abnamro", warnings=warnings)
        self.assertEqual(len(rows), 1)
        self.assertEqual(len(warnings), 1)
        self.assertIn("regel 2", warnings[0])
        self.assertIn(IBAN_B, warnings[0])

    def test_mt940_huge_amount(self):
        text = (":20:REF1\n:25:NL55INGB0000000000\n:28C:1/1\n:60F:C190101EUR0,00\n"
                f":61:190101D{self.HUGE}0,01NTRFNONREF\n:86:/TRTP/SEPA/IBAN/{IBAN_B}/NAME/Karton/REMI/x/\n"
                f":61:190101D0,01NTRFNONREF\n:86:/TRTP/SEPA/IBAN/{IBAN_A}/NAME/Karton/REMI/y/\n:62F:C190101EUR0,00\n")
        warnings: list[str] = []
        _, rows = tx.import_file(self.write("s.sta", text), fmt="mt940", warnings=warnings)
        self.assertEqual([r["iban"] for r in rows], [IBAN_A])
        self.assertIn(IBAN_B, warnings[0])
        self.assertIn("regel 5", warnings[0])

    def _camt(self, amounts: list[str]) -> str:
        ntry = ('<Ntry><Amt Ccy="EUR">{a}</Amt><CdtDbtInd>DBIT</CdtDbtInd><BookgDt><Dt>2026-10-01</Dt></BookgDt>'
                '<NtryDtls><TxDtls><RltdPties><Cdtr><Nm>B</Nm></Cdtr><CdtrAcct><Id><IBAN>{i}</IBAN></Id></CdtrAcct>'
                '</RltdPties></TxDtls></NtryDtls></Ntry>')
        body = "".join(ntry.format(a=a, i=i) for a, i in amounts)
        return ('<?xml version="1.0"?><Document xmlns="urn:iso:std:iso:20022:tech:xsd:camt.053.001.02">'
                f"<BkToCstmrStmt><Stmt>{body}</Stmt></BkToCstmrStmt></Document>")

    def test_camt053_huge_and_non_finite_amounts(self):
        warnings: list[str] = []
        _, rows = tx.import_file(self.write("c.xml", self._camt([("12.50", IBAN_A), (self.HUGE + ".01", IBAN_B),
                                                                  ("NaN", IBAN_B), ("Infinity", IBAN_B)])),
                                 fmt="camt053", warnings=warnings)
        self.assertEqual([r["amount"] for r in rows], ["-12.50"])
        self.assertIn("3 regels overgeslagen", warnings[0])
        self.assertIn(IBAN_B, warnings[0])

    def test_camt053_only_bad_entry_is_one_friendly_error_naming_the_iban(self):
        with self.assertRaises(tx.ImportError_) as cm:
            tx.import_file(self.write("c.xml", self._camt([(self.HUGE, IBAN_B)])), fmt="camt053")
        self.assertIn(IBAN_B, str(cm.exception))


class RC4SilentRowSkips(_Files):
    """Found while fixing RC1-3 (design gap, not a fuzz crash): every importer used to drop an unreadable row
    with a bare `continue` -- no crash, but also no word to the user, even if that row held a payee IBAN."""

    def test_unreadable_amount_row_is_reported(self):
        p = self.write("ing.csv", "\n".join([ING_HEADER, ing_row(IBAN_A), ing_row(IBAN_B, amount="??")]))
        warnings: list[str] = []
        _, rows = tx.import_file(p, fmt="ing", warnings=warnings)
        self.assertEqual(len(rows), 1)
        self.assertRegex(warnings[0], r"^ING-bestand: 1 regel overgeslagen \(regel 3\): bedrag niet leesbaar")
        self.assertIn(IBAN_B, warnings[0])

    def test_blank_and_whitespace_rows_are_not_warnings(self):
        p = self.write("ing.csv", "\n".join([ING_HEADER, ing_row(IBAN_A), ";;;;;;;;", "\x0b", ""]))
        warnings: list[str] = []
        tx.import_file(p, fmt="ing", warnings=warnings)
        self.assertEqual(warnings, [])

    def test_mt940_unreadable_61_line_is_reported(self):
        text = (":20:REF1\n:61:garbage\n:86:/IBAN/" + IBAN_B + "/NAME/X/\n:61:190101D1,00NTRF\n:86:/IBAN/" +
                IBAN_A + "/NAME/Y/\n")
        warnings: list[str] = []
        _, rows = tx.import_file(self.write("s.sta", text), fmt="mt940", warnings=warnings)
        self.assertEqual(len(rows), 1)
        self.assertIn(IBAN_B, warnings[0])

    def test_cli_prints_the_warning(self):
        import contextlib
        import io

        from countersign_shadow.__main__ import cmd_import_tx
        from countersign_shadow.store import Workspace

        p = self.write("ing.csv", "\n".join([ING_HEADER, ing_row(IBAN_A), ing_row(IBAN_B, amount="??")]))
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            cmd_import_tx(Workspace(self.tmp / "ws"), [str(p)], "ing")
        self.assertIn("waarschuwing: ING-bestand: 1 regel overgeslagen", out.getvalue())


class RC5SafetyNet(_Files):
    """Defense in depth: any unexpected internal error becomes ImportCrash (a friendly Dutch ImportError_), so
    the pilot user never sees a traceback; the fuzz harness unwraps it and still reports it as a bug."""

    def test_unexpected_error_becomes_import_crash(self):
        p = self.write("ing.csv", "\n".join([ING_HEADER, ing_row(IBAN_A)]))
        with mock.patch.dict(tx.PARSERS, {"ing": mock.Mock(side_effect=KeyError("boom"))}):
            with self.assertRaises(tx.ImportCrash) as cm:
                tx.import_file(p, fmt="ing")
        self.assertIsInstance(cm.exception, tx.ImportError_)
        self.assertIsInstance(cm.exception.__cause__, KeyError)
        self.assertIn("kon niet gelezen worden", str(cm.exception))


class RC6DamagedMsg(unittest.TestCase):
    """fuzz #3, #7, #8, #18 (69x + 53x + 32x + 4x): ValueError / OverflowError / OSError / RecursionError from the
    vendored olefile on a damaged .msg (raised after OleFileIO() opened, so outside the existing try). Each is
    now one MsgUnsupported (Dutch, 'sla op als .eml'), which import_paths turns into an 'overgeslagen' line.
    Reproducers are the fuzz's own shrunk synthetic files."""

    def test_damaged_msg_files_raise_msg_unsupported(self):
        for name in ("005-ValueError", "006-OverflowError", "015-OSError", "018-RecursionError"):
            with self.subTest(name), self.assertRaises(msglib.MsgUnsupported) as cm:
                msglib.read_msg_parts(DATA / f"{name}.msg")
            self.assertIn(".eml", str(cm.exception))

    def test_inbox_import_reports_instead_of_crashing(self):
        from countersign_shadow.importers import inbox
        problems: list[str] = []
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "kapot.msg"
            p.write_bytes((DATA / "018-RecursionError.msg").read_bytes())
            self.assertEqual(inbox.import_paths([p], problems), [])
        self.assertEqual(len(problems), 1)
        self.assertIn("kapot.msg", problems[0])


class RC7MsgDateOverflow(unittest.TestCase):
    """fuzz #15 (6x): OverflowError 'date value out of range' in msg._date on a corrupt PT_SYSTIME value. The
    mail itself is fine; the date now falls back to the Date: header / empty instead of losing the mail."""

    def test_corrupt_timestamp_is_ignored(self):
        sent = datetime(2026, 10, 24, 9, 0, tzinfo=timezone.utc)
        data = make_msg("Factuur 77", "Karton BV", "info@karton.example", f"Betaal naar {IBAN_A}", sent)
        ft = int((sent - datetime(1601, 1, 1, tzinfo=timezone.utc)).total_seconds() * 10_000_000)
        good = struct.pack("<IIQ", (0x0039 << 16) | 0x0040, 6, ft)
        self.assertEqual(data.count(good), 1)
        data = data.replace(good, struct.pack("<IIQ", (0x0039 << 16) | 0x0040, 6, 2**64 - 1))
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "m.msg"
            p.write_bytes(data)
            parts = msglib.read_msg_parts(p)
        self.assertEqual(parts["subject"], "Factuur 77")
        self.assertEqual(parts["date"], "")

    def test_shrunk_fuzz_file_reads(self):
        parts = msglib.read_msg_parts(DATA / "048-OverflowError.msg")
        self.assertIsInstance(parts["subject"], str)


class RC8HarnessArtefacts(unittest.TestCase):
    """fuzz #4, #9-11, #14, #16-17, #19-108 (95 findings, all 116 failing valid:pdf passes) and #6 (61x): harness
    artefacts, not product bugs. Pinned here so the harness fixes cannot silently regress."""

    def test_pdf_text_on_a_valid_invoice_is_fast_and_finds_the_iban(self):
        import time
        t0 = time.monotonic()
        text, warning = attachments.pdf_text(make_pdf(["Factuur 2026-12", f"IBAN {IBAN_A}", "Totaal EUR 38,00"]))
        self.assertEqual(warning, "")
        self.assertIn(IBAN_A, attachments.parse_invoice(text)["ibans"])
        self.assertLess(time.monotonic() - t0, 5.0)

    def _properties_tree(self):
        return ast.parse((REPO / "fuzz" / "properties.py").read_text())

    def test_harness_preimports_vendored_libs_outside_the_timed_cases(self):
        # the HangTimeouts were pypdf's *import* (glyph/font tables) running under Hypothesis's line tracer
        # inside the 5s SIGALRM budget; properties.py must import pypdf + olefile at module level
        top_calls = [ast.unparse(n.value) for n in self._properties_tree().body
                     if isinstance(n, ast.Expr) and isinstance(n.value, ast.Call)]
        self.assertIn("_vendor_load('pypdf')", top_calls)
        self.assertIn("_vendor_load('olefile')", top_calls)

    def test_new_iban_property_excludes_the_history_iban(self):
        src = (REPO / "fuzz" / "properties.py").read_text()
        self.assertIn("assume(ibanlib.normalise(new_iban) != history_iban)", src)

    def _decide(self, payee_iban: str, history_iban: str) -> str:
        quote = f"Betaal 1,00 naar {payee_iban} t.n.v. Kartonfabriek Noord BV."
        message = {"subject": "Factuur", "body": quote, "from": "info@kartonfabriek-noord.example",
                   "from_name": "Kartonfabriek Noord BV", "date": "2026-10-24", "pdfs": []}
        proposal = {"action": "pay_invoice", "amount": "1.00", "currency": "EUR",
                    "payee": {"name": "Kartonfabriek Noord BV", "iban": payee_iban, "email": ""},
                    "invoice_ref": "", "order_ref": "", "evidence_quote": quote, "rationale": ""}
        history = [{"amount": "-10.00", "currency": "EUR", "date": "2026-01-01", "iban": history_iban,
                    "counterparty": "Someone Else BV", "email": "", "description": "", "reference": "", "status": ""}]
        result = checks.evaluate(proposal, message, history, checks.Policy())
        return checks.verdict("pay_invoice", result)

    def test_new_iban_is_never_ok_and_a_paid_before_iban_passes_c5(self):
        hist = ibanlib.make_nl("RABO", "0000000001")
        self.assertNotIn(self._decide(ibanlib.make_nl("RABO", "0000000002"), hist), ("ok", "none"))
        # the fuzz's shrunk "failure": the drawn IBAN WAS the history IBAN, so it is not new (C5 passes); since
        # it is a warning, not ok, because that IBAN was paid to "Someone Else BV" (C12, TestNameCheck)
        self.assertEqual(self._decide(hist, hist), "warn")


if __name__ == "__main__":
    unittest.main()
