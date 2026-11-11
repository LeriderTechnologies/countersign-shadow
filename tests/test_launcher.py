# SPDX-License-Identifier: Apache-2.0
"""Start window (launcher.py) and the bundle builder (packaging/build_bundles.py). Synthetic data only."""
from __future__ import annotations

import io
import re
import stat
import sys
import tarfile
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path

from countersign_shadow import launcher

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "packaging"))
import build_bundles as bb  # noqa: E402


class LauncherCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.opened: list[Path] = []
        self.app = launcher.Launcher(self.root, self.root / "mijn-gegevens", review_port=0,
                                     open_dir=self.opened.append, data_dir=self.root / "pilot-data")
        self.srv = launcher.make_launcher_server(self.app, 0)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.base = self.app.home_url

    def tearDown(self):
        self.srv.shutdown()
        self.srv.server_close()
        if self.app.review is not None:
            self.app.review.shutdown()
            self.app.review.server_close()
        self.tmp.cleanup()

    def get(self, path: str = "") -> str:
        return urllib.request.urlopen(self.base + path).read().decode()

    def post(self, path: str, token: str | None = None) -> str:
        tok = token if token is not None else re.search(r'name="csrf" value="([^"]+)"', self.get()).group(1)
        return urllib.request.urlopen(self.base + path, data=urllib.parse.urlencode({"csrf": tok}).encode()).read().decode()

    def wait(self) -> str:
        for _ in range(100):
            if self.app.state != "busy":
                break
            time.sleep(0.1)
        return self.app.state


class TestStartWindow(LauncherCase):
    def test_home_page_is_plain_dutch_and_mine_disabled_when_empty(self):
        h = self.get()
        for text in ("Welkom", "Bekijk de demo", "Open de map mijn-gegevens", "Start met mijn gegevens",
                     "Countersign stoppen", "kan zelf niets betalen of versturen", "takeout.google.com"):
            self.assertIn(text, h)
        self.assertRegex(h, r'action="/mine".*?disabled')
        for jargon in ("Python", "terminal", "PATH", "localhost", "CSV-parser"):
            self.assertNotIn(jargon, h)

    def test_csrf_required(self):
        with self.assertRaises(urllib.error.HTTPError) as cm:
            self.post("demo", token="wrong")
        self.assertEqual(cm.exception.code, 403)
        self.assertEqual(self.app.state, "idle")

    def test_demo_flow_reaches_review_page(self):
        self.post("demo")
        self.assertEqual(self.wait(), "ready")
        busy = self.get("bezig")
        self.assertIn(self.app.review_url, busy)
        review = urllib.request.urlopen(self.app.review_url).read().decode()
        self.assertEqual(review.count('<section class="box" id='), 21)
        self.assertIn("Startscherm", review)  # link back to the start window

    def test_open_folder_creates_it_with_readme(self):
        self.post("open-folder")
        self.assertEqual(self.opened, [self.root / "mijn-gegevens"])
        self.assertTrue((self.root / "mijn-gegevens" / "LEES-MIJ.txt").exists())

    def test_counts_and_mine_with_empty_mail(self):
        folder = self.root / "mijn-gegevens"
        folder.mkdir()
        from countersign_shadow.samples import write_samples
        src = write_samples(self.root / "s")
        (folder / "ing.csv").write_bytes((src / "ing.csv").read_bytes())
        (folder / "notities.txt").write_text("boodschappen: melk", encoding="utf-8")  # not a bank file
        (folder / "LEES-MIJ.txt").write_text("x", encoding="utf-8")
        self.assertIn("Er staan nog geen e-mails in de map", self.get())  # a bank file alone is no mail
        (folder / "a.eml").write_bytes(b"Subject: hallo\r\n\r\nniets\r\n")
        h = self.get()
        self.assertIn("1 mailbestand en 1 bankbestand", h)
        self.assertNotRegex(h, r'action="/mine".*?disabled')

    def test_mine_without_readable_mail_shows_plain_message(self):
        folder = self.root / "mijn-gegevens"
        folder.mkdir()
        (folder / "x.zip").write_bytes(b"not a zip")
        self.post("mine")
        self.assertEqual(self.wait(), "empty")
        self.assertIn("Geen bruikbare e-mails gevonden", self.get("bezig"))
        self.assertIn("kan niet worden geopend", self.get("bezig"))  # the reason is shown, not only under Details

    def test_mine_shows_what_was_read_and_filtered_empty_reason(self):
        sys.path.insert(0, str(REPO / "tests"))
        from test_bulk_import import make_mail, make_mbox
        folder = self.root / "mijn-gegevens"
        folder.mkdir()
        (folder / "old.mbox").write_bytes(make_mbox([make_mail(i, 2000, False) for i in range(80)]))
        self.post("mine")
        self.assertEqual(self.wait(), "empty")
        page = self.get("bezig")
        self.assertIn("geen enkele over geld", page)
        self.assertIn("80 mails bekeken", page)
        (folder / "new.mbox").write_bytes(make_mbox([make_mail(900 + i, 1, True) for i in range(3)]))
        self.app.state = "idle"
        self.post("mine")
        self.assertEqual(self.wait(), "ready")
        page = self.get("bezig")
        self.assertIn("Wat Countersign las", page)
        self.assertIn("mails over geld", page)

    def test_report_lines_are_dutch(self):
        log = ("[inbox] 300 mails bekeken; 50 mails over geld gekozen.\n[inbox] 50 messages read, 50 new, 50 in "
               "workspace\n[transactions] ing.csv: ing, 4 rows, 4 new, 4 in workspace\n[draft] rules: 50 cards")
        self.assertEqual(launcher.report_lines(log), ["300 mails bekeken; 50 mails over geld gekozen.",
                                                      "ing.csv: 4 transacties ingelezen"])
        odd = "[transactions] n.txt: dit bankbestand kent Countersign (nog) niet. (unknown transaction export: x)"
        self.assertEqual(launcher.report_lines(odd), ["n.txt: dit bankbestand kent Countersign (nog) niet."])

    def test_stop(self):
        stopped = self.post("stop")
        self.assertIn("Countersign is gestopt", stopped)
        self.assertIn("Dubbelklik weer", stopped)
        self.assertTrue(self.app.stopped.wait(3))

    def test_launch_subcommand_exists(self):
        from countersign_shadow.__main__ import main
        with self.assertRaises(SystemExit):
            main(["launch", "--help"])


def _fake_windows_runtime(path: Path) -> None:
    with zipfile.ZipFile(path, "w") as zf:
        for name in ("python.exe", "pythonw.exe", "python313.dll", "python313.zip", "LICENSE.txt"):
            zf.writestr(name, b"x")
        zf.writestr("python313._pth", "python313.zip\n.\n\n# Uncomment to run site.main() automatically\n#import site\n")


def _fake_mac_runtime(path: Path) -> None:
    with tarfile.open(path, "w:gz") as tf:
        def add(name, data=b"x", mode=0o644, link=None):
            ti = tarfile.TarInfo(name)
            if link:
                ti.type, ti.linkname = tarfile.SYMTYPE, link
                tf.addfile(ti)
            else:
                ti.size, ti.mode = len(data), mode
                tf.addfile(ti, io.BytesIO(data))
        add("python/bin/python3.13", mode=0o755)
        add("python/bin/python3", link="python3.13")
        add("python/lib/python3.13/os.py")
        add("python/lib/python3.13/test/test_x.py")
        add("python/include/python3.13/Python.h")


class TestBundles(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.d = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_windows_bundle_layout(self):
        rt = self.d / "rt.zip"
        _fake_windows_runtime(rt)
        out = bb.build_windows(rt, self.d)
        with zipfile.ZipFile(out) as zf:
            names = set(zf.namelist())
            top = ""
            for n in ("Countersign starten.bat", "LEES MIJ.txt", "mijn-gegevens/LEES-MIJ.txt",
                      "programma/python/pythonw.exe", "programma/countersign_shadow/__main__.py",
                      "programma/countersign_shadow/launcher.py", "programma/pilot/GIDS.md", "programma/LICENSE", "programma/HANDLEIDING.txt"):
                self.assertIn(top + n, names)
            self.assertFalse([n for n in names if "/tests/" in n or "__pycache__" in n or "pilot-data" in n
                              or n.endswith(".pyc")])
            pth = zf.read(top + "programma/python/python313._pth").decode().split("\r\n")
            self.assertEqual(pth[:3], ["python313.zip", ".", ".."])  # '..' = programma, where the app lives
            bat = zf.read(top + "Countersign starten.bat").decode("ascii")
            self.assertIn("\r\n", bat)
            self.assertIn(r'"%~dp0programma\python\pythonw.exe" -m countersign_shadow launch', bat)
            self.assertIn("Alles uitpakken", bat)          # run from inside the zip: plain instruction
            lees = zf.read(top + "LEES MIJ.txt").decode("utf-8")
            self.assertLess(lees.index("Alles uitpakken"), lees.index("Dubbelklik"))  # extract first
            self.assertIn("Toch uitvoeren", lees)
            self.assertIn("OneDrive", lees)
            self.assertIn("hello@lerider.com", lees)  # a contact that works in the public tree too
            dbg = zf.read(top + "programma/hulp - starten met venster.bat").decode("ascii")
            self.assertNotIn("..\\mijn-gegevens", dbg)
            self.assertIn("COUNTERSIGN_INPUT=%~dp0mijn-gegevens", bat)
            # top level stays tiny for a non-technical user
            tops = {n[len(top):].split("/")[0] for n in names}
            self.assertEqual(tops, {"Countersign starten.bat", "LEES MIJ.txt", "mijn-gegevens", "programma"})

    def test_windows_bundle_rejects_non_embeddable_zip(self):
        rt = self.d / "rt.zip"
        with zipfile.ZipFile(rt, "w") as zf:
            zf.writestr("python.exe", b"x")
        with self.assertRaises(SystemExit):
            bb.build_windows(rt, self.d)

    def test_mac_bundle_modes_symlinks_and_pruning(self):
        a, b = self.d / "a.tgz", self.d / "b.tgz"
        _fake_mac_runtime(a)
        _fake_mac_runtime(b)
        out = bb.build_mac(a, b, self.d)
        with zipfile.ZipFile(out) as zf:
            infos = {i.filename: i for i in zf.infolist()}
            top = ""
            cmd = infos[top + "Countersign starten.command"]
            self.assertTrue((cmd.external_attr >> 16) & stat.S_IXUSR)
            for arch in ("arm64", "x86_64"):
                py = infos[f"{top}programma/python-{arch}/bin/python3.13"]
                self.assertTrue((py.external_attr >> 16) & stat.S_IXUSR)
                link = infos[f"{top}programma/python-{arch}/bin/python3"]
                self.assertTrue(stat.S_ISLNK(link.external_attr >> 16))
                self.assertEqual(zf.read(link), b"python3.13")
            self.assertFalse([n for n in infos if "/test/" in n or "/include/" in n])
            text = zf.read(top + "Countersign starten.command").decode()
            self.assertIn("xattr -dr com.apple.quarantine", text)
            self.assertIn("uname -m", text)
            self.assertIn("Open toch", zf.read(top + "LEES MIJ.txt").decode())

    def test_fetch_rejects_wrong_checksum(self):
        cache = self.d / "cache"
        cache.mkdir()
        url, _ = bb.RUNTIMES["windows"]
        (cache / url.rsplit("/", 1)[-1]).write_bytes(b"tampered")
        with self.assertRaises(SystemExit):
            bb.fetch("windows", cache)
        self.assertFalse((cache / url.rsplit("/", 1)[-1]).exists())

    def test_runtime_pins_are_exact(self):
        for key, (url, digest) in bb.RUNTIMES.items():
            self.assertTrue(url.startswith("https://"), key)
            self.assertRegex(digest, r"^[0-9a-f]{64}$")
            self.assertIn(bb.PY_VERSION, url)


if __name__ == "__main__":
    unittest.main()
