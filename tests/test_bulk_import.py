# SPDX-License-Identifier: Apache-2.0
"""Bulk mail import (importers/bulk.py): Gmail Takeout .zip / big .mbox / folder exports with many .eml files.

All mail here is synthetic, built in the test."""
from __future__ import annotations

import io
import tempfile
import unittest
import zipfile
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from pathlib import Path

from countersign_shadow.importers import bulk
from countersign_shadow.importers import inbox as inbox_imp

NOW = datetime(2026, 11, 1, 12, 0, tzinfo=timezone.utc)

MONEY = "Beste klant,\nIn de bijlage vindt u factuur F-{i}. Graag betalen op NL91ABNA0417164300, € {i},00.\n"
CHATTER = "Hoi,\nZin om zaterdag te lunchen? Groet, Sanne\n"


def make_mail(i: int, days_ago: int, money: bool, labels: str = "Inbox", subject: str | None = None,
              body_from_line: bool = False) -> bytes:
    subj = subject or (f"Factuur F-{i}" if money else f"Lunch {i}")
    body = (MONEY if money else CHATTER).format(i=i)
    if body_from_line:
        body += "From the desk of our director: nothing to pay.\n"
    return (f"From: Leverancier {i} <info{i}@example.org>\r\nTo: shop@example.nl\r\nSubject: {subj}\r\n"
            f"Date: {format_datetime(NOW - timedelta(days=days_ago))}\r\nMessage-ID: <m{i}@example.org>\r\n"
            f"X-Gmail-Labels: {labels}\r\nContent-Type: text/plain; charset=utf-8\r\n\r\n{body}").encode()


def make_mbox(mails: list[bytes]) -> bytes:
    out = io.BytesIO()
    for m in mails:
        out.write(b"From 1234567890@xxx Mon Jan  5 10:00:00 +0000 2026\n")
        for line in m.replace(b"\r\n", b"\n").split(b"\n"):
            out.write((b">" + line if line.startswith(b"From ") else line) + b"\n")
        out.write(b"\n")
    return out.getvalue()


def takeout_mails(n: int = 120) -> list[bytes]:
    """n mails: every 3rd about money; half of them older than 6 months; a few chat logs."""
    mails = []
    for i in range(n):
        money = i % 3 == 0
        days = 10 + i if i < n // 2 else 250 + i
        mails.append(make_mail(i, days, money))
    mails.append(make_mail(9001, 5, True, labels="Chat"))
    return mails


class TestMboxStream(unittest.TestCase):
    def test_split_and_unescape(self):
        raw = make_mbox([make_mail(1, 1, True, body_from_line=True), make_mail(2, 1, False)])
        parts = list(bulk.iter_mbox(io.BytesIO(raw)))
        self.assertEqual(len(parts), 2)
        self.assertIn(b"\nFrom the desk", parts[0])  # '>From ' in the body is restored, not a new message
        self.assertTrue(parts[1].startswith(b"From: Leverancier 2"))


class TestSelector(unittest.TestCase):
    def test_small_export_kept_whole(self):
        sel = bulk.Selector(now=NOW)
        for i in range(10):
            sel.feed(make_mail(i, 900, False), f"x{i}")  # old and not about money: still kept (hand-picked)
        self.assertFalse(sel.filtered)
        self.assertEqual(len(sel.finish()), 10)

    def test_big_export_filtered(self):
        sel = bulk.Selector(now=NOW)
        for i, m in enumerate(takeout_mails()):
            sel.feed(m, f"x{i}")
        self.assertTrue(sel.filtered)
        kept = sel.finish()
        self.assertEqual(len(kept), 20)          # i in 0..59 step 3 = 20 recent money mails
        self.assertEqual(sel.chat, 1)
        self.assertGreater(sel.old, 0)
        self.assertGreater(sel.unrelated, 0)
        self.assertIn("20 mails over geld", sel.summary_nl())

    def test_limit_keeps_newest(self):
        sel = bulk.Selector(limit=5, small=3, now=NOW)
        for i in range(30):
            sel.feed(make_mail(i, i + 1, True), f"x{i}")
        kept = sel.finish()
        self.assertEqual([o for _r, o in kept], ["x0", "x1", "x2", "x3", "x4"])
        self.assertIn("de nieuwste 5", sel.summary_nl())

    def test_pdf_attachment_counts_as_money(self):
        mail = (b"From: a@example.org\r\nSubject: Stukken\r\nDate: " + format_datetime(NOW).encode() +
                b"\r\nMIME-Version: 1.0\r\nContent-Type: multipart/mixed; boundary=b\r\n\r\n--b\r\n"
                b"Content-Type: text/plain\r\n\r\nZie bijlage.\r\n--b\r\nContent-Type: application/pdf\r\n"
                b"Content-Disposition: attachment; filename=stuk.pdf\r\nContent-Transfer-Encoding: base64\r\n\r\n"
                b"JVBERi0xLjQK\r\n--b--\r\n")
        self.assertTrue(bulk.is_money_mail(bulk.parse_raw(mail)))
        self.assertFalse(bulk.is_money_mail(bulk.parse_raw(make_mail(1, 1, False))))

    def test_no_date_is_kept(self):
        mail = b"From: a@example.org\r\nSubject: Factuur zonder datum\r\n\r\nGraag betalen.\r\n"
        sel = bulk.Selector(small=0, now=NOW)
        sel.feed(mail, "x")
        self.assertEqual(len(sel.finish()), 1)


class TestMoneyFilter(unittest.TestCase):
    """Typical Dutch money mails must pass the prefilter (a miss = a fraud mail the user never sees)."""

    def mail(self, subject: str, body: str) -> bytes:
        return (f"From: x@example.org\r\nSubject: {subject}\r\nDate: {format_datetime(NOW)}\r\n"
                f"Content-Type: text/plain; charset=utf-8\r\n\r\n{body}\r\n").encode()

    def test_dutch_money_mails_pass(self):
        for subject, body in [
                ("Uw abonnement", "Werk uw betaalgegevens bij om onderbreking te voorkomen."),
                ("Betaalverzoek", "Via deze betaallink kunt u het openstaande bedrag voldoen."),
                ("SEPA-machtiging", "Hierbij de machtiging met ons incassant-ID."),
                ("Declaratie", "Te voldoen: 2.420,-"),
                ("Kwitantie", "Hierbij de kwitantie voor uw administratie."),
                ("Storting", "Uw storting van 500 euro is ontvangen."),
                ("Spoedbetaling", "Kun je dit vandaag nog regelen?"),
                ("Ben je even beschikbaar?", "Vertrouwelijk: ik heb je hulp nodig, laat het niemand weten."),
                ("Is betaald", "De nota is betaald."),
                ("Ordernummer 12345", "Uw ordernummer staat hieronder.")]:
            self.assertTrue(bulk.is_money_mail(bulk.parse_raw(self.mail(subject, body))), subject)

    def test_chatter_does_not_pass(self):
        self.assertFalse(bulk.is_money_mail(bulk.parse_raw(self.mail("Lunch", "Zin om zaterdag te lunchen?"))))


class TestImportPaths(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_takeout_zip(self):
        z = self.dir / "takeout-20261101T000000Z-001.zip"
        with zipfile.ZipFile(z, "w") as zf:
            zf.writestr("Takeout/Mail/Alle e-mail inclusief spam en prullenbak.mbox", make_mbox(takeout_mails()))
            zf.writestr("Takeout/archive_browser.html", "<html></html>")
        info, problems = [], []
        msgs = inbox_imp.import_paths([z], problems, info, now=NOW)
        self.assertEqual(len(msgs), 20)
        self.assertEqual(problems, [])
        self.assertTrue(all(m["subject"].startswith("Factuur") for m in msgs))
        self.assertIn("121 mails bekeken; 20 mails over geld", info[0])

    def test_takeout_zip_with_eml_members(self):
        z = self.dir / "export.zip"
        with zipfile.ZipFile(z, "w") as zf:
            for i in range(3):
                zf.writestr(f"mails/{i}.eml", make_mail(i, 2, i == 0))
        self.assertEqual(len(inbox_imp.import_paths([z], now=NOW)), 3)  # small: all kept

    def test_broken_zip_is_reported(self):
        z = self.dir / "kapot.zip"
        z.write_bytes(b"PK\x03\x04 not really a zip")
        problems = []
        self.assertEqual(inbox_imp.import_paths([z], problems, now=NOW), [])
        self.assertIn("kapot.zip", problems[0])

    def test_big_mbox_and_thunderbird_style_file(self):
        (self.dir / "Inbox").write_bytes(make_mbox(takeout_mails()))   # Thunderbird: no extension
        (self.dir / "Inbox.msf").write_bytes(b"// index, not mail")
        info = []
        msgs = inbox_imp.import_paths([self.dir], None, info, now=NOW)
        self.assertEqual(len(msgs), 20)
        self.assertEqual(len(info), 1)

    def test_folder_export_of_many_eml(self):
        sub = self.dir / "export" / "Inbox"
        sub.mkdir(parents=True)
        for i, m in enumerate(takeout_mails()):
            (sub / f"{i:04d}.eml").write_bytes(m)
        info = []
        msgs = inbox_imp.import_paths([self.dir / "export"], None, info, now=NOW)  # recursive
        self.assertEqual(len(msgs), 20)
        self.assertIn("20 mails over geld", info[0])

    def test_start_flow_reads_zip_from_input_folder(self):
        from countersign_shadow.__main__ import import_folder
        from countersign_shadow.store import Workspace
        folder = self.dir / "mijn-gegevens"
        folder.mkdir()
        with zipfile.ZipFile(folder / "takeout.zip", "w") as zf:
            zf.writestr("Takeout/Mail/x.mbox", make_mbox([make_mail(i, 3, True) for i in range(5)]))
        ws = Workspace(self.dir / "ws")
        self.assertEqual(import_folder(ws, folder), (5, 0))

    def test_multi_part_takeout_is_one_selection(self):
        """Part 002 is small and old: it must not be kept whole just because that part alone is small."""
        with zipfile.ZipFile(self.dir / "takeout-001.zip", "w") as zf:
            zf.writestr("Takeout/Mail/a.mbox", make_mbox(takeout_mails()))
        with zipfile.ZipFile(self.dir / "takeout-002.zip", "w") as zf:
            zf.writestr("Takeout/Mail/b.mbox", make_mbox([make_mail(500 + i, 700, False) for i in range(30)]))
        info = []
        msgs = inbox_imp.import_paths([self.dir], None, info, now=NOW)
        self.assertEqual(len(msgs), 20)
        self.assertEqual(len(info), 1)
        self.assertIn("151 mails bekeken", info[0])

    def test_trash_skipped_and_spam_capped(self):
        mails = [make_mail(i, 1, True, labels="Prullenbak") for i in range(5)]
        mails += [make_mail(100 + i, 1, True, labels="Spam,Categorie: promoties") for i in range(30)]
        mails += [make_mail(200 + i, 2, True) for i in range(30)]
        sel = bulk.Selector(now=NOW)
        for i, m in enumerate(mails):
            sel.feed(m, f"x{i}")
        kept = [bulk.parse_raw(r)["Subject"] for r, _o in sel.finish()]
        self.assertEqual(sel.trash, 5)
        self.assertEqual(len(kept), 40)  # 30 inbox + at most 10 spam
        self.assertEqual(sum(1 for k in kept if int(k.split("-")[1]) >= 200), 30)
        self.assertIn("5 uit de prullenbak", sel.summary_nl())

    def test_default_limit_fits_a_short_review(self):
        self.assertLessEqual(bulk.LIMIT, 60)

    def test_bank_formats_in_input_folder(self):
        from countersign_shadow.__main__ import TX_EXT
        for ext in (".xml", ".sta", ".940", ".mt940", ".swi", ".csv", ".txt"):
            self.assertIn(ext, TX_EXT)

    def test_extensionless_check_reads_only_five_bytes(self):
        p = self.dir / "big"
        p.write_bytes(b"From x\n" + b"a" * 100)
        self.assertTrue(bulk.is_mbox_file(p))
        p2 = self.dir / "notes"
        p2.write_bytes(b"hello")
        self.assertFalse(bulk.is_mbox_file(p2))


if __name__ == "__main__":
    unittest.main()
