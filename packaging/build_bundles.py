#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Build the download-and-double-click bundles for pilot users: Python included, nothing to install, no PATH.

    python3 packaging/build_bundles.py                  # both bundles into packaging/dist/
    python3 packaging/build_bundles.py --only windows   # or: --only mac
    python3 packaging/build_bundles.py --cache DIR      # where the Python runtimes are kept (default: .cache/countersign-bundles in your home folder)

Output (one zip per platform; the user unzips it and double-clicks one file). The zips have no top folder of
their own: Windows "Alles uitpakken" and the Mac Archive Utility already make one named after the zip, so the
user does not land in a folder inside a folder.

    Countersign-Shadow-Windows.zip
      Countersign starten.bat     <- double-click; starts programma\\python\\pythonw.exe (no black window)
      LEES MIJ.txt
      mijn-gegevens\\              <- the user's own mails and bank export go here
      (programma\\HANDLEIDING.txt = pilot/GIDS.md as .txt: Windows has no default app for .md)
      programma\\                  <- Countersign + the official python.org "embeddable" Python (PSF-signed .exe)
    Countersign-Shadow-Mac.zip
      Countersign starten.command <- double-click (first time: see LEES MIJ.txt); picks Apple silicon or Intel
      LEES MIJ.txt, mijn-gegevens/, programma/ (Countersign + python-build-standalone for arm64 and x86_64)

The runtimes are downloaded once, checked against the pinned SHA-256 below, and cached. Nothing is signed: see
packaging/SIGNING.md for what signing would cost and what it would remove.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import shutil
import stat
import sys
import tarfile
import urllib.request
import zipfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
PY_VERSION = "3.13.17"
RUNTIMES = {
    # python.org embeddable package (signed by the Python Software Foundation). Hash recorded at first download
    # on 2026-11-11 from www.python.org (python.org publishes Sigstore signatures for it, not a SHA-256 list).
    "windows": ("https://www.python.org/ftp/python/3.13.17/python-3.13.17-embed-amd64.zip",
                "9e35319bffedab8b36ad44d8f377cb4f1733bcec257a0b6d04d94f01486ddb38"),
    # python-build-standalone release 20261021; hashes match that release's SHA256SUMS file.
    "mac-arm64": ("https://github.com/astral-sh/python-build-standalone/releases/download/20261021/"
                  "cpython-3.13.17%2B20261021-aarch64-apple-darwin-install_only.tar.gz",
                  "9c985cbf327425873469896ce748c7134b6deca8edb81e93c589ad2fc5aff445"),
    "mac-x86_64": ("https://github.com/astral-sh/python-build-standalone/releases/download/20261021/"
                   "cpython-3.13.17%2B20261021-x86_64-apple-darwin-install_only.tar.gz",
                   "b7d7c2dd63545cabe996191b6f20f0b652c7de123d9ca451dda7e5f1ebbcbbbc"),
}
APP_FILES = ["countersign_shadow", "pilot", "LICENSE", "PRIVACY.md", "README.md"]
SKIP_PARTS = {"__pycache__", "pilot-data", "demo-data", "mijn-gegevens", "deel-mij"}
# parts of the macOS runtime a pilot user never needs (saves ~60 MB per architecture)
MAC_PRUNE = ["include", "share", "lib/pkgconfig", "lib/python3.13/test", "lib/python3.13/idlelib",
             "lib/python3.13/tkinter", "lib/python3.13/turtledemo", "lib/python3.13/ensurepip",
             "lib/python3.13/lib2to3", "lib/itcl4.3.8", "lib/tcl9", "lib/tk9.0", "lib/thread3.0.1",
             "lib/python3.13/config-3.13-darwin"]

LEES_MIJ = """Countersign Shadow
==================

{steps}
Er opent een pagina in je browser: het startscherm. Dat legt stap voor stap uit wat je doet:
  1. de demo bekijken (verzonnen gegevens);
  2. je eigen mails en transacties in de map "mijn-gegevens" zetten;
  3. starten en per mail zeggen of Countersign het goed inschat.

Countersign kan niets betalen of versturen. Je gegevens blijven op deze computer.
Je hoeft niets te installeren: alles wat nodig is, staat in de map "programma".
Zet deze map niet in OneDrive, Dropbox of iCloud (ook niet op een Bureaublad of in Documenten die worden
gesynchroniseerd): dan gaan je mails alsnog naar de cloud. Downloads is prima.

Tabblad per ongeluk gesloten, of later verder? Dubbelklik weer op "{start}".

Opruimen: verwijder deze hele map. Heb je een Gmail-export gemaakt? Verwijder dan ook dat zip-bestand uit je
map Downloads: dat is een kopie van je hele mailbox.

Hulp: open de handleiding (programma/HANDLEIDING.txt) of mail naar hello@lerider.com.
"""
STEPS_WIN = """Starten:
  1. Pak eerst uit: rechtsklik op het zip-bestand > "Alles uitpakken..." > "Uitpakken".
     (Lees je dit in het zip-bestand zelf? Dan is het nog niet uitgepakt.)
  2. Open de uitgepakte map "Countersign-Shadow-Windows".
  3. Dubbelklik op "Countersign starten" (het type is "Windows-batchbestand").

De eerste keer vraagt Windows misschien "Bestand openen - beveiligingswaarschuwing: De uitgever kan niet worden
geverifieerd". Klik dan op "Uitvoeren". Zie je een blauw scherm "Windows heeft uw pc beveiligd"? Klik op
"Meer informatie" en dan op "Toch uitvoeren". Dat komt doordat het bestand van internet komt.
Gebeurt er na het dubbelklikken helemaal niets? Open de map programma, dubbelklik op
"hulp - starten met venster" en mail een schermafdruk van het zwarte venster naar hello@lerider.com.
"""
STEPS_MAC = """Starten:
  1. Dubbelklik op het zip-bestand (Safari heeft het misschien al uitgepakt: dan staat er al een map
     "Countersign-Shadow-Mac" in Downloads).
  2. Open de map "Countersign-Shadow-Mac" en dubbelklik op "Countersign starten.command".

De eerste keer zegt je Mac misschien dat het bestand niet geopend kan worden omdat Apple het niet kan
controleren. Doe dan dit (één keer):
  - macOS 14 of ouder: rechtsklik (of Control-klik) op het bestand, kies "Open" en dan nog eens "Open";
  - macOS 15 en nieuwer: klik op "Gereed", open Systeeminstellingen > Privacy en beveiliging, scrol naar
    beneden en klik bij "Countersign starten.command" op "Open toch". Je Mac vraagt dan je wachtwoord of
    Touch ID. Daarna werkt dubbelklikken gewoon.
Vraagt je Mac of "Terminal" toegang mag tot bestanden in je map Downloads? Klik op "OK".
Er gaat ook een venster met tekst open (Terminal): laat dat open zolang je Countersign gebruikt. Na
"Countersign stoppen" staat daarin "[Proces voltooid]"; dan kun je het sluiten.
"""

WIN_BAT = r"""@echo off
rem Countersign Shadow: dubbelklik om te starten. Niets kan betaald of verstuurd worden.
if not exist "%~dp0programma\python\pythonw.exe" (
  echo Pak eerst het zip-bestand uit: rechtsklik op het zip-bestand en kies "Alles uitpakken".
  echo Open daarna de uitgepakte map en dubbelklik daar op "Countersign starten".
  pause
  exit /b 1
)
set "COUNTERSIGN_INPUT=%~dp0mijn-gegevens"
cd /d "%~dp0programma"
start "" "%~dp0programma\python\pythonw.exe" -m countersign_shadow launch
"""
WIN_BAT_DEBUG = r"""@echo off
rem Alleen voor hulp bij problemen: zelfde als "Countersign starten", maar met een venster dat meldingen toont.
cd /d "%~dp0.."
set "COUNTERSIGN_INPUT=%CD%\mijn-gegevens"
cd /d "%~dp0"
"%~dp0python\python.exe" -m countersign_shadow launch
pause
"""
MAC_COMMAND = """#!/bin/sh
# Countersign Shadow (macOS): dubbelklik om te starten. Niets kan betaald of verstuurd worden.
HERE="$(cd "$(dirname "$0")" && pwd)"
# the zip came from the internet: clear the download mark on our own files so the bundled Python may run
xattr -dr com.apple.quarantine "$HERE" 2>/dev/null
if [ "$(uname -m)" = "arm64" ]; then PY="$HERE/programma/python-arm64/bin/python3"
else PY="$HERE/programma/python-x86_64/bin/python3"; fi
export COUNTERSIGN_INPUT="$HERE/mijn-gegevens"
cd "$HERE/programma" || exit 1
echo "Countersign Shadow start. Je browser opent zo het startscherm."
echo "Laat dit venster open zolang je Countersign gebruikt; stoppen kan met de knop in je browser."
"$PY" -m countersign_shadow launch || {
  echo; echo "Er ging iets mis. Maak een schermafdruk van dit venster en mail die naar hello@lerider.com."; read -r _; }
"""
INPUT_README = ("Zet hier je e-mails en je transacties.\n\nE-mails: je hele Gmail-export van Google Takeout "
                "(het .zip-bestand, niet uitpakken), losse .eml- of .msg-bestanden, of een .mbox.\n"
                "Transacties: de download van je bank (.csv of .txt, of een MT940- of CAMT.053-bestand). "
                "Geen PDF-afschrift: dat kan Countersign niet lezen.\n\n"
                "Deze map verlaat je computer niet.\n")


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def fetch(key: str, cache: Path) -> Path:
    url, want = RUNTIMES[key]
    out = cache / url.rsplit("/", 1)[-1].replace("%2B", "+")
    if not out.exists():
        cache.mkdir(parents=True, exist_ok=True)
        req = urllib.request.Request(url, headers={"User-Agent": "countersign-build/1.0"})
        with urllib.request.urlopen(req, timeout=120) as r, open(out.with_suffix(".part"), "wb") as fh:
            shutil.copyfileobj(r, fh)
        out.with_suffix(".part").rename(out)
    got = sha256(out)
    if got != want:
        out.unlink()
        raise SystemExit(f"checksum mismatch for {out.name}: {got} != {want} (file removed; not used)")
    return out


def _app_entries():
    """(relative path, source file) for every app file that goes into programma/."""
    for name in APP_FILES:
        src = REPO / name
        if src.is_file():
            yield name, src
            continue
        for f in sorted(src.rglob("*")):
            rel = f.relative_to(REPO)
            if f.is_file() and not SKIP_PARTS.intersection(rel.parts) and f.suffix != ".pyc":
                yield rel.as_posix(), f


class ZipOut:
    """Deterministic zip writer that keeps Unix modes (exec bits, symlinks) for the Mac bundle."""

    def __init__(self, path: Path, top: str = ""):
        self.zf = zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED, compresslevel=9)
        self.top = top.rstrip("/") + "/" if top else ""

    def _info(self, name: str, mode: int) -> zipfile.ZipInfo:
        zi = zipfile.ZipInfo(f"{self.top}{name}", date_time=(2026, 1, 1, 0, 0, 0))
        zi.external_attr = mode << 16
        zi.create_system = 3  # Unix, so the modes are honoured
        zi.compress_type = zipfile.ZIP_DEFLATED
        return zi

    def file(self, name: str, data: bytes, mode: int = 0o644) -> None:
        self.zf.writestr(self._info(name, stat.S_IFREG | mode), data)

    def symlink(self, name: str, target: str) -> None:
        self.zf.writestr(self._info(name, stat.S_IFLNK | 0o777), target)

    def dir(self, name: str) -> None:
        self.zf.writestr(self._info(name.rstrip("/") + "/", stat.S_IFDIR | 0o755), b"")

    def close(self) -> None:
        self.zf.close()


def _add_common(z: ZipOut, start: str, steps: str, newline: str) -> None:
    z.file("LEES MIJ.txt", LEES_MIJ.format(start=start, steps=steps).replace("\n", newline).encode("utf-8"))
    z.file("programma/HANDLEIDING.txt", (REPO / "pilot" / "GIDS.md").read_text(encoding="utf-8")
           .replace("\n", newline).encode("utf-8"))
    z.dir("mijn-gegevens")
    z.file("mijn-gegevens/LEES-MIJ.txt", INPUT_README.replace("\n", newline).encode("utf-8"))
    for rel, src in _app_entries():
        z.file(f"programma/{rel}", src.read_bytes())


def build_windows(runtime_zip: Path, out_dir: Path) -> Path:
    out = out_dir / "Countersign-Shadow-Windows.zip"
    z = ZipOut(out)
    z.file("Countersign starten.bat", WIN_BAT.replace("\n", "\r\n").encode("ascii"))
    _add_common(z, "Countersign starten", STEPS_WIN, "\r\n")
    z.file("programma/hulp - starten met venster.bat", WIN_BAT_DEBUG.replace("\n", "\r\n").encode("ascii"))
    pth_seen = False
    with zipfile.ZipFile(runtime_zip) as rz:
        for info in sorted(rz.infolist(), key=lambda i: i.filename):
            if info.is_dir():
                continue
            data = rz.read(info)
            if info.filename.endswith("._pth"):
                # the embeddable Python ignores PYTHONPATH and the current folder; '..' = programma\, where
                # the countersign_shadow package lives
                lines = [ln for ln in data.decode("utf-8").splitlines()]
                if ".." not in lines:
                    lines.insert(lines.index(".") + 1, "..")
                data = ("\r\n".join(lines) + "\r\n").encode("utf-8")
                pth_seen = True
            z.file(f"programma/python/{info.filename}", data)
    z.close()
    if not pth_seen:
        out.unlink()
        raise SystemExit("no ._pth file in the Windows runtime zip: not an embeddable package?")
    return out


def _add_mac_runtime(z: ZipOut, tgz: Path, folder: str) -> None:
    with tarfile.open(tgz) as tf:
        for m in sorted(tf.getmembers(), key=lambda m: m.name):
            if not m.name.startswith("python/"):
                continue
            rel = m.name[len("python/"):]
            if not rel or any(rel == p or rel.startswith(p + "/") for p in MAC_PRUNE) or "__pycache__" in rel:
                continue
            if m.issym():
                z.symlink(f"programma/{folder}/{rel}", m.linkname)
            elif m.isfile():
                z.file(f"programma/{folder}/{rel}", tf.extractfile(m).read(), m.mode & 0o777)


def build_mac(arm64: Path, x86_64: Path, out_dir: Path) -> Path:
    out = out_dir / "Countersign-Shadow-Mac.zip"
    z = ZipOut(out)
    z.file("Countersign starten.command", MAC_COMMAND.encode("utf-8"), 0o755)
    _add_common(z, "Countersign starten.command", STEPS_MAC, "\n")
    _add_mac_runtime(z, arm64, "python-arm64")
    _add_mac_runtime(z, x86_64, "python-x86_64")
    z.close()
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--only", choices=("windows", "mac"))
    ap.add_argument("--cache", default=str(Path.home() / ".cache" / "countersign-bundles"))
    ap.add_argument("--out", default=str(REPO / "packaging" / "dist"))
    a = ap.parse_args(argv)
    cache, out_dir = Path(a.cache), Path(a.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    built = []
    if a.only in (None, "windows"):
        built.append(build_windows(fetch("windows", cache), out_dir))
    if a.only in (None, "mac"):
        built.append(build_mac(fetch("mac-arm64", cache), fetch("mac-x86_64", cache), out_dir))
    for p in built:
        print(f"{p}  {p.stat().st_size / 1e6:.1f} MB  sha256 {sha256(p)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
