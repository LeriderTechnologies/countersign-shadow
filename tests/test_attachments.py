# SPDX-License-Identifier: Apache-2.0
"""Tests: PDF invoice attachments (text layer -> IBAN / amount / invoice fields -> checks) and Outlook .msg import.

The PDFs and .msg files are synthetic and built by hand in tests/fixtures.py. pypdf and olefile are vendored in
countersign_shadow/_vendor, so nothing is skipped; the "library missing" paths are simulated with sys.modules.
"""
from __future__ import annotations

import contextlib
import io
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from email.message import EmailMessage
from pathlib import Path
from unittest import mock

try:
    from .fixtures import make_image_only_pdf, make_msg, make_pdf
except ImportError:  # run as a top-level module by `unittest discover -s tests`
    from fixtures import make_image_only_pdf, make_msg, make_pdf

from countersign_shadow import _vendor as vendor
from countersign_shadow import samples
from countersign_shadow.__main__ import demo_workspace, import_folder
from countersign_shadow.importers import attachments as att
from countersign_shadow.importers import inbox as inbox_imp
from countersign_shadow.planners import make_planner, normalise_proposal
from countersign_shadow.planners.llm import render_message
from countersign_shadow.shadow import draft_cards
from countersign_shadow.store import Workspace



def _loads(name: str) -> bool:
    try:
        vendor.load(name)
        return True
    except ImportError:
        return False


HAS_PYPDF, HAS_OLEFILE = _loads("pypdf"), _loads("olefile")
ROOT = Path(__file__).resolve().parent.parent
nl = samples.fmt_nl

KARTON_FROM = ("Kartonfabriek Noord BV", "debiteuren@kartonfabriek-noord.example")


def invoice_pdf(iban: str, total: str = "287,15", ref: str = "F-2026-0419", extra: list[str] = ()) -> bytes:
    return make_pdf(["Kartonfabriek Noord BV", "Industrieweg 12, 9700 AB Groningen",
                     "KvK 12345678 - BTW NL123456789B01", "FACTUUR", f"Factuurnummer: {ref}",
                     "Factuurdatum: 2026-10-20", "Dozen 40x30x30, 500 stuks", f"Totaal te betalen € {total}",
                     f"Graag overmaken naar {nl(iban)} t.n.v. Kartonfabriek Noord BV", *extra])


def eml(body: str, pdf: bytes | None = None, pdf_name: str = "factuur-F-2026-0419.pdf",
        subject: str = "Factuur F-2026-0419", sender=KARTON_FROM) -> EmailMessage:
    m = EmailMessage()
    m["From"] = f"{sender[0]} <{sender[1]}>"
    m["To"] = "sanne@studioklei.example"
    m["Subject"] = subject
    m["Date"] = "Thu, 22 Oct 2026 09:00:00 +0200"
    m["Message-ID"] = f"<{abs(hash((body, subject, pdf)))}@test.example>"
    m.set_content(body)
    if pdf is not None:
        m.add_attachment(pdf, maintype="application", subtype="pdf", filename=pdf_name)
    return m


class AttBase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        with contextlib.redirect_stdout(io.StringIO()):
            self.ws = demo_workspace(self.tmp / "ws")  # gives the Studio Klei payment history

    def tearDown(self):
        self._tmp.cleanup()

    def import_eml(self, msg: EmailMessage) -> dict:
        p = self.tmp / f"m{len(list(self.tmp.glob('*.eml')))}.eml"
        p.write_bytes(bytes(msg))
        return inbox_imp.import_paths([p])[0]

    def card(self, msg: dict, planner=None) -> dict:
        return draft_cards(self.ws, planner or make_planner("rules"), messages=[msg])[0]

    @staticmethod
    def failed(card) -> set[str]:
        return {c["rule"].split()[0] for c in card["checks"] if not c["ok"]}

    @staticmethod
    def check(card, rule) -> dict:
        return next(c for c in card["checks"] if c["rule"].startswith(rule))


@unittest.skipUnless(HAS_PYPDF, "pypdf not installed")
class TestPdfExtraction(AttBase):
    def test_fields_from_dutch_invoice(self):
        r = att.read_pdf_attachment("f.pdf", invoice_pdf(samples.FRAUD, total="1.234,56"))
        self.assertEqual(r["warning"], "")
        self.assertEqual(r["ibans"], [samples.FRAUD])
        self.assertEqual(r["total"], "1234.56")
        self.assertEqual((r["invoice_ref"], r["kvk"], r["btw"]), ("F-2026-0419", "12345678", "NL123456789B01"))
        self.assertEqual(r["supplier"], "Kartonfabriek Noord BV")

    def test_total_is_the_last_total_line_not_the_subtotal(self):
        r = att.read_pdf_attachment("f.pdf", make_pdf([
            "Glazuurhandel De Vries", "Factuurnummer: GH-7790", "Subtotaal € 1.000,00", "BTW 21% € 210,00",
            "Totaal incl. btw € 1.210,00", f"IBAN {samples.GLAZUUR}"]))
        self.assertEqual(r["total"], "1210.00")
        self.assertEqual(r["amounts"], ["1000.00", "210.00", "1210.00"])

    def test_bad_checksum_iban_is_kept_apart(self):
        r = att.read_pdf_attachment("f.pdf", invoice_pdf(samples.BROKEN))
        self.assertEqual((r["ibans"], r["bad_ibans"]), ([], [samples.BROKEN]))

    def test_scanned_pdf_and_garbage_give_dutch_warnings(self):
        self.assertIn("gescand", att.read_pdf_attachment("s.pdf", make_image_only_pdf())["warning"])
        self.assertIn("niet gelezen", att.read_pdf_attachment("x.pdf", b"%PDF-1.4 kapot")["warning"])
        self.assertIn("geen geldig PDF", att.read_pdf_attachment("x.pdf", b"MZ\x90\x00")["warning"])

    def test_eml_with_pdf_attachment(self):
        m = self.import_eml(eml("Zie bijlage.", invoice_pdf(samples.KARTON)))
        self.assertEqual(m["attachments"], ["factuur-F-2026-0419.pdf"])
        self.assertEqual(m["pdfs"][0]["ibans"], [samples.KARTON])
        self.assertEqual(m["body"], "Zie bijlage.")


class TestVendored(unittest.TestCase):
    def test_vendored_copies_are_used_and_licensed(self):
        for name, version in vendor.VERSIONS.items():
            mod = vendor.load(name)
            self.assertTrue(Path(mod.__file__).resolve().is_relative_to(Path(vendor.VENDOR)), mod.__file__)
            self.assertEqual(mod.__version__, version)
            lic = Path(vendor.VENDOR) / name
            self.assertTrue(any((lic / f).exists() for f in ("LICENSE", "LICENSE.txt")), name)

    def test_plain_pdf_and_msg_work_without_optional_dependencies(self):
        """Fresh interpreter, no site-packages, cryptography / pycryptodome / Pillow blocked."""
        code = (
            "import sys\n"
            "for m in ('cryptography', 'Crypto', 'PIL'): sys.modules[m] = None\n"
            "sys.path[:0] = ['tests', '.']\n"
            "from fixtures import make_pdf, make_msg\n"
            "from countersign_shadow.importers import attachments as att, msg\n"
            "from countersign_shadow.iban import make_nl\n"
            "r = att.read_pdf_attachment('f.pdf', make_pdf(['Factuurnummer: F-1', 'Totaal te betalen EUR 12,00',"
            " 'IBAN ' + make_nl('INGB', '0001234567')]))\n"
            "assert r['warning'] == '' and r['total'] == '12.00' and r['ibans'], r\n"
            "import pypdf, olefile\n"
            "assert '_vendor' in pypdf.__file__ and '_vendor' in olefile.__file__\n"
            "print('ok')\n")
        out = subprocess.run([sys.executable, "-S", "-c", code], cwd=ROOT, capture_output=True, text=True, timeout=60)
        self.assertEqual(out.stdout.strip(), "ok", out.stderr)


class TestPdfMissingLibrary(AttBase):
    def test_without_pypdf_the_attachment_gets_a_warning(self):
        with mock.patch.dict(sys.modules, {"pypdf": None}):
            r = att.read_pdf_attachment("f.pdf", invoice_pdf(samples.KARTON))
        self.assertIn("pypdf ontbreekt", r["warning"])
        self.assertEqual(r["ibans"], [])

    def test_unreadable_pdf_warns_on_the_card(self):
        with mock.patch.dict(sys.modules, {"pypdf": None}):
            m = self.import_eml(eml("Beste Sanne, bijgaand de factuur. Groet.", invoice_pdf(samples.FRAUD)))
        c = self.card(m)
        self.assertEqual(c["action"], "no_action")
        self.assertEqual(c["verdict"], "warn")
        self.assertIn("Kan PDF niet lezen", self.check(c, "W3")["uitleg"])


@unittest.skipUnless(HAS_PYPDF, "pypdf not installed")
class TestPdfChecks(AttBase):
    def test_swapped_iban_in_pdf_vs_mail_body(self):
        """The classic: the mail text shows the real IBAN, the attached PDF was edited to the fraudster's."""
        m = self.import_eml(eml(f"Beste Sanne,\n\nBijgaand factuur F-2026-0419. Te betalen: € 287,15 op "
                                f"{nl(samples.KARTON)}.\n\nGroet, Kartonfabriek Noord", invoice_pdf(samples.FRAUD)))
        c = self.card(m)
        self.assertEqual((c["action"], c["verdict"]), ("pay_invoice", "blocked"))
        self.assertIn("P1", self.failed(c))
        self.assertIn("vervalst", self.check(c, "P1")["uitleg"])
        both = self.check(c, "C5")["detail"] + " " + " ".join(x["detail"] for x in c["checks"] if x["rule"][:2] == "P2")
        self.assertIn("IBAN CHANGED", both)
        self.assertIn(samples.FRAUD, both)

    def test_iban_only_in_pdf_is_checked_against_history(self):
        m = self.import_eml(eml("Beste Sanne,\n\nIn de bijlage vindt u factuur F-2026-0419.\n\nGroet",
                                invoice_pdf(samples.FRAUD)))
        c = self.card(m)
        self.assertEqual((c["action"], c["verdict"]), ("pay_invoice", "blocked"))
        c5 = self.check(c, "C5")
        self.assertIn("IBAN CHANGED", c5["detail"])
        self.assertIn("PDF", c5["detail"])
        self.assertTrue(self.check(c, "V3")["ok"])  # the IBAN does appear: in the attachment
        self.assertNotIn("P1", {x["rule"][:2] for x in c["checks"]})  # nothing to compare in the mail text

    def test_planner_that_trusts_the_mail_iban_still_sees_the_pdf_iban(self):
        m = self.import_eml(eml(f"Factuur F-2026-0419, € 287,15 naar {nl(samples.KARTON)}.",
                                invoice_pdf(samples.FRAUD)))

        class MailOnly:
            name = "mail-only"

            def plan(self, msgs):
                return [normalise_proposal(msgs[0]["id"], {
                    "action": "pay_invoice", "amount": "287.15", "payee": {"iban": samples.KARTON},
                    "invoice_ref": "F-2026-0419", "evidence_quote": "Factuur F-2026-0419, € 287,15 naar"})]
        c = self.card(m, MailOnly())
        self.assertTrue(self.check(c, "C5")["ok"])
        p2 = self.check(c, "P2")
        self.assertFalse(p2["ok"])
        self.assertIn("IBAN CHANGED", p2["detail"])
        self.assertEqual(c["verdict"], "blocked")

    def test_amount_mismatch_is_a_warning(self):
        m = self.import_eml(eml(f"Beste Sanne,\n\nFactuur F-2026-0500. Bedrag: € 312,40, IBAN {nl(samples.KARTON)}."
                                "\n\nGroet", invoice_pdf(samples.KARTON, total="1.312,40", ref="F-2026-0500")))
        c = self.card(m)
        self.assertEqual(c["amount"], "312.40")
        self.assertEqual(self.failed(c), {"P4"})
        self.assertEqual(c["verdict"], "warn")
        self.assertIn("1312.40", self.check(c, "P4")["uitleg"])

    def test_matching_pdf_passes(self):
        m = self.import_eml(eml(f"Beste Sanne,\n\nFactuur F-2026-0500. Te betalen € 287,15 op {nl(samples.KARTON)}."
                                "\n\nGroet", invoice_pdf(samples.KARTON, ref="F-2026-0500")))
        c = self.card(m)
        self.assertEqual(self.failed(c), set(), c["reasons"])
        self.assertEqual(c["verdict"], "ok")
        self.assertTrue(self.check(c, "P1")["ok"] and self.check(c, "P4")["ok"] and self.check(c, "W3")["ok"])

    def test_bad_checksum_iban_in_pdf(self):
        m = self.import_eml(eml(f"Factuur F-2026-0500, te betalen € 287,15 op {nl(samples.KARTON)}.",
                                invoice_pdf(samples.KARTON, ref="F-2026-0500", extra=[f"Of: {samples.BROKEN}"])))
        c = self.card(m)
        self.assertIn("P3", self.failed(c))
        self.assertEqual(self.check(c, "P3")["severity"], "warn")

    def test_scanned_pdf_with_iban_in_mail(self):
        m = self.import_eml(eml(f"Factuur F-2026-0500, te betalen € 287,15 op {nl(samples.KARTON)}.",
                                make_image_only_pdf()))
        c = self.card(m)
        self.assertEqual(self.failed(c), {"W3"})
        self.assertEqual(c["verdict"], "warn")

    def test_llm_planner_sees_the_pdf_text(self):
        m = self.import_eml(eml("Zie bijlage.", invoice_pdf(samples.FRAUD)))
        self.assertIn(nl(samples.FRAUD), render_message(m))


@unittest.skipUnless(HAS_OLEFILE, "olefile not installed")
class TestOutlookMsg(AttBase):
    def write_msg(self, name="factuur.msg", pdf: bytes | None = None, body="Zie bijlage.") -> Path:
        atts = [("factuur-F-2026-0419.pdf", "application/pdf", pdf)] if pdf else []
        p = self.tmp / "in" / name
        p.parent.mkdir(exist_ok=True)
        p.write_bytes(make_msg("Factuur F-2026-0419", *KARTON_FROM, body, datetime(2026, 10, 22, 7, tzinfo=timezone.utc),
                               atts + [("logo.png", "image/png", b"\x89PNG" + b"\0" * 5000)]))
        return p

    def test_msg_fields(self):
        m = inbox_imp.import_paths([self.write_msg(body="Beste Sanne,\r\nFactuur F-2026-0419 € 287,15.")])[0]
        self.assertEqual((m["from"], m["from_name"]), (KARTON_FROM[1], KARTON_FROM[0]))  # SMTP, not the X.500 DN
        self.assertEqual((m["subject"], m["date"], m["origin"]), ("Factuur F-2026-0419", "2026-10-22", "factuur.msg"))
        self.assertEqual(m["body"], "Beste Sanne,\nFactuur F-2026-0419 € 287,15.")
        self.assertEqual(m["attachments"], ["logo.png"])

    @unittest.skipUnless(HAS_PYPDF, "pypdf not installed")
    def test_msg_with_swapped_iban_pdf_is_blocked(self):
        folder = self.write_msg(pdf=invoice_pdf(samples.FRAUD)).parent
        with contextlib.redirect_stdout(io.StringIO()) as out:
            ws = Workspace(self.tmp / "fresh")
            ws.save("transactions", self.ws.transactions)
            self.assertEqual(import_folder(ws, folder), (1, 0))
        self.assertIn("1 PDF-bijlagen, 1 gelezen", out.getvalue())
        c = draft_cards(ws, make_planner("rules"))[0]
        self.assertEqual((c["action"], c["verdict"]), ("pay_invoice", "blocked"))
        self.assertIn("IBAN CHANGED", self.check(c, "C5")["detail"])


class TestOutlookMsgProblems(AttBase):
    def test_without_olefile_msg_is_skipped_with_dutch_advice(self):
        p = self.tmp / "x.msg"
        p.write_bytes(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\0" * 504)
        problems: list[str] = []
        with mock.patch.dict(sys.modules, {"olefile": None}):
            self.assertEqual(inbox_imp.import_paths([p], problems), [])
        self.assertEqual(len(problems), 1)
        self.assertIn(".eml", problems[0])
        self.assertIn("Opslaan als", problems[0])

    @unittest.skipUnless(HAS_OLEFILE, "olefile not installed")
    def test_broken_msg_is_skipped(self):
        p = self.tmp / "kapot.msg"
        p.write_bytes(b"dit is geen outlook-bestand")
        problems: list[str] = []
        self.assertEqual(inbox_imp.import_paths([p], problems), [])
        self.assertIn("kapot.msg", problems[0])
        self.assertIn(".eml", problems[0])


if __name__ == "__main__":
    unittest.main()
