# SPDX-License-Identifier: Apache-2.0
"""Start window: a plain-language Dutch page in the browser, so a pilot user never needs a terminal.

    python -m countersign_shadow launch      (what the double-click files in the installer bundles run)

The page (http://127.0.0.1:8765/, this computer only) offers four buttons: try the demo, open the folder
mijn-gegevens, start with my own data, stop. Reading mail and drafting cards runs in the background with a
progress page; then the browser goes to the review page (http://127.0.0.1:8766/). Every POST carries a CSRF
token. Like the rest of Countersign, nothing here can pay or send anything.
"""
from __future__ import annotations

import contextlib
import io
import os
import secrets
import subprocess
import sys
import threading
import traceback
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer  # noqa: F401
from pathlib import Path

from .web import LocalServer, e, make_server, page

LAUNCH_PORT = 8765
REVIEW_PORT = 8766

EXTRA_CSS = """<style>.steps{counter-reset:s}.step{display:flex;gap:14px;align-items:flex-start}
.step .n{flex:0 0 32px;height:32px;border-radius:50%;background:var(--acc);color:#fff;font-weight:650;
display:flex;align-items:center;justify-content:center}.step h2{font-size:18px;margin:2px 0 4px}
.step p{margin:4px 0}.big{font-size:17px;padding:10px 18px}button[disabled]{opacity:.45;cursor:not-allowed}
.found{font-weight:600}.log{font:13px/1.4 ui-monospace,monospace;background:var(--chip);border-radius:8px;
padding:8px 10px;white-space:pre-wrap;max-height:260px;overflow:auto}</style>"""


def count_input(folder: Path) -> tuple[int, int]:
    """(mail files, bank files) in the folder: what the start page shows before the user starts."""
    from .__main__ import MAIL_EXT, TX_EXT, _starts_with_from
    if not folder.exists():
        return 0, 0
    mail = tx = 0
    for p in folder.rglob("*"):
        if not p.is_file() or p.name.startswith("."):
            continue
        suf = p.suffix.lower()
        if suf in MAIL_EXT or (suf == "" and _starts_with_from(p)):
            mail += 1
        elif suf in TX_EXT and p.name.lower() != "lees-mij.txt" and _is_bank_file(p):
            tx += 1
    return mail, tx


def _is_bank_file(p: Path) -> bool:
    """Only files the transaction importer recognises count as bank files (a notes.txt does not)."""
    from .importers import transactions as tx_imp
    try:
        if p.stat().st_size > 20_000_000:
            return False
        tx_imp.import_file(p, "auto", [])
        return True
    except Exception:  # unknown layout, unreadable, ...: not a bank file we can use
        return False


def report_lines(log: str) -> list[str]:
    """The Dutch lines of an import log that a user should see (what was read, chosen and skipped)."""
    out = []
    for line in log.splitlines():
        for tag in ("[inbox] ", "[transactions] "):
            if line.startswith(tag):
                text = line[len(tag):]
                if " messages read, " in text or text.startswith("waarschuwing"):
                    continue
                if " rows, " in text and " new, " in text:
                    name, rest = text.split(": ", 1)
                    rows = rest.split(", ")[1].split(" ")[0]
                    text = f"{name}: {rows} transactie{'s' if rows != '1' else ''} ingelezen"
                out.append(text.split(" (unknown transaction export")[0])  # the English detail stays in Details
    return out


def open_folder(path: Path) -> None:
    """Show the folder in Explorer / Finder / the file manager."""
    if sys.platform.startswith("win"):
        os.startfile(str(path))  # type: ignore[attr-defined]
    elif sys.platform == "darwin":
        subprocess.Popen(["open", str(path)])
    else:
        subprocess.Popen(["xdg-open", str(path)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


class Launcher:
    """State shared by the start page's handler threads."""

    def __init__(self, root: Path, input_dir: Path, review_port: int = REVIEW_PORT, open_dir=open_folder,
                 data_dir: Path | None = None):
        self.root, self.input_dir, self.review_port, self.open_dir = root, input_dir, review_port, open_dir
        self.data_dir = data_dir  # None = the CLI default (pilot-data/ or $COUNTERSIGN_DATA)
        self.token = secrets.token_urlsafe(24)
        self.lock = threading.Lock()
        self.state = "idle"        # idle | busy | ready | empty | error
        self.mode = ""             # demo | mine
        self.log = ""
        self.review = None         # the running review server
        self.home_url = ""
        self.stopped = threading.Event()

    # -- work ---------------------------------------------------------------------------------------------
    def start_job(self, mode: str) -> bool:
        with self.lock:
            if self.state == "busy":
                return False
            self.state, self.mode, self.log = "busy", mode, ""
        threading.Thread(target=self._job, args=(mode,), daemon=True).start()
        return True

    def _job(self, mode: str) -> None:
        from . import __main__ as cli
        from .planners import make_planner
        from .store import Workspace
        buf = io.StringIO()
        try:
            with contextlib.redirect_stdout(buf):
                if mode == "demo":
                    ws = Workspace(self.root / "demo-data")
                    if not ws.messages:
                        ws = cli.demo_workspace(ws.root)
                else:
                    self.input_dir.mkdir(exist_ok=True)
                    ws = Workspace(self.data_dir or cli.DEFAULT_DATA)
                    cli.import_folder(ws, self.input_dir)
                if ws.messages:
                    cli.cmd_draft(ws, make_planner("rules"))
            if not ws.messages:
                with self.lock:
                    self.state, self.log = "empty", buf.getvalue()
                return
            self._serve_review(ws)
            with self.lock:
                self.state, self.log = "ready", buf.getvalue()
        except Exception:  # shown on the page in plain words; the details help the maintainer
            with self.lock:
                self.state, self.log = "error", buf.getvalue() + "\n" + traceback.format_exc(limit=3)

    def _serve_review(self, ws) -> None:
        if self.review is not None:
            self.review.shutdown()
            self.review.server_close()
        try:
            self.review = make_server(ws, self.review_port, self.home_url)
        except OSError:  # port taken (e.g. an old window still open): any free port will do
            self.review = make_server(ws, 0, self.home_url)
        self.review_port = self.review.server_address[1]
        threading.Thread(target=self.review.serve_forever, daemon=True).start()

    @property
    def review_url(self) -> str:
        return f"http://127.0.0.1:{self.review_port}/"

    # -- pages --------------------------------------------------------------------------------------------
    def _form(self, action: str, label: str, cls: str = "primary big", disabled: bool = False) -> str:
        return (f'<form method="post" action="{action}" style="display:inline">'
                f'<input type="hidden" name="csrf" value="{self.token}">'
                f'<button class="{cls}"{" disabled" if disabled else ""}>{e(label)}</button></form>')

    def home_page(self) -> bytes:
        mail, tx = count_input(self.input_dir)
        if mail:
            found = (f'<p class="found">In de map staan nu {mail} mailbestand{"en" if mail != 1 else ""} en {tx} '
                     f'bankbestand{"en" if tx != 1 else ""}.</p>')
        else:
            found = '<p class="found">Er staan nog geen e-mails in de map.</p>'
        if mail and not tx:
            found += ('<p class="mute">Nog geen bankbestand dat Countersign herkent. Zonder werkt het ook, maar dan '
                      'kan Countersign niet zien welke rekeningnummers je al kent en welke facturen al betaald zijn.'
                      '</p>')
        inner = f"""{EXTRA_CSS}
<h2 style="font-size:22px;margin:18px 0 4px">Welkom</h2>
<p>Countersign leest je zakelijke e-mails en de transacties van je bank, en laat per mail zien of het een betaalverzoek zou
tegenhouden en waarom. Jij zegt per mail of dat klopt. <b>Countersign kan zelf niets betalen of versturen, en je
gegevens blijven op deze computer.</b></p>
<div class="steps">
<section class="box step"><div class="n">1</div><div><h2>Probeer de demo</h2>
<p>Twee minuten, met verzonnen mails van een verzonnen webwinkel. Zo zie je hoe het werkt.</p>
{self._form("/demo", "Bekijk de demo")}</div></section>
<section class="box step"><div class="n">2</div><div><h2>Zet je eigen mails en transacties klaar</h2>
<p>Klik op de knop: de map <b>mijn-gegevens</b> gaat open. Zet daarin:</p>
<ul><li><b>Je e-mails.</b> Gebruik je Gmail? Dan mag je je hele mailbox in één keer exporteren via
<a href="https://takeout.google.com/" target="_blank" rel="noopener noreferrer">takeout.google.com</a> (alleen
de mail aanvinken; de stappen staan in de handleiding). Sleep het .zip-bestand dat je krijgt uit je map
Downloads naar de map mijn-gegevens; uitpakken hoeft niet. Countersign kiest zelf de mails over geld uit de
laatste 6 maanden (hooguit 50). Losse mails (.eml of .msg) of een .mbox mogen ook.</li>
<li><b>Je transacties</b> van de laatste 3 tot 6 maanden, als download van je bank: een .csv- of .txt-bestand,
of MT940 of CAMT.053 (zo heten die bestanden bij je bank; kies wat je bank aanbiedt). <b>Geen PDF-afschrift</b>: dat kan Countersign niet lezen.</li></ul>
<p>{self._form("/open-folder", "Open de map mijn-gegevens", "big")}</p>{found}
<p><a href="/">Opnieuw kijken of de bestanden er staan</a></p>
</div></section>
<section class="box step"><div class="n">3</div><div><h2>Start met mijn gegevens</h2>
<p>Countersign leest je mails en transacties in en maakt per mail een kaart. Bij een grote Gmail-export kan dat
een paar minuten duren. Je hoeft niet alle kaarten te beoordelen: 30 is genoeg.</p>
{self._form("/mine", "Start met mijn gegevens", disabled=not mail)}</div></section>
</div>
<section class="box"><p style="margin:0">Klaar? Klik op {self._form("/stop", "Countersign stoppen", "")}.
Daarna kun je dit tabblad sluiten. Je oordelen zijn al bewaard. Later verder? Dubbelklik weer op
"Countersign starten".</p></section>
<p class="mute">Tip: zet de map van Countersign niet in OneDrive, Dropbox of iCloud (zoals Bureaublad of
Documenten als die worden gesynchroniseerd); dan gaan je mails alsnog naar de cloud. Downloads is prima.</p>"""
        return page("Countersign Shadow: start", inner)

    def _report(self, log: str) -> str:
        lines = report_lines(log)
        if not lines:
            return ""
        return ("<p><b>Wat Countersign las:</b></p><ul>" + "".join(f"<li>{e(x)}</li>" for x in lines) + "</ul>")

    def busy_page(self) -> bytes:
        with self.lock:
            state, mode, log = self.state, self.mode, self.log
        what = "de demo" if mode == "demo" else "je mails en transacties"
        details = (f'<details><summary>Technische details (voor Lerider)</summary><div class="log">{e(log)}</div>'
                   f'</details>' if log.strip() else "")
        back = '<p><a href="/">← Terug naar het startscherm</a></p>'
        if state == "busy":
            inner = (f'<meta http-equiv="refresh" content="2"><section class="box"><h2>Even geduld…</h2>'
                     f'<p>Countersign leest {what} in. Bij een grote export kan dat een paar minuten duren. '
                     f'Deze pagina ververst vanzelf.</p></section>')
        elif state == "ready":
            wait = 1 if mode == "demo" else 6
            inner = (f'<meta http-equiv="refresh" content="{wait};url={self.review_url}"><section class="box">'
                     f'<h2>Klaar</h2>{self._report(log) if mode != "demo" else ""}'
                     f'<p><a href="{self.review_url}"><b>Ga naar de kaarten</b></a> (dat gebeurt zo vanzelf)</p>'
                     f'</section>')
        elif state == "empty":
            if "mails bekeken" in log:
                why = ("Countersign vond wel mails, maar geen enkele over geld uit de laatste 6 maanden. Zet er een "
                       "paar losse mails over facturen of betalingen bij (.eml of .msg) en probeer het opnieuw.")
            else:
                why = ("Countersign vond geen mails die het kan lezen in de map <b>mijn-gegevens</b> (open hem via de "
                       "knop op het startscherm). Zet daar "
                       ".eml-, .msg- of .mbox-bestanden of je Gmail-export (.zip) in en probeer het opnieuw.")
            inner = (f'{EXTRA_CSS}<section class="box"><h2>Geen bruikbare e-mails gevonden</h2><p>{why}</p>'
                     f'{self._report(log)}{back}{details}</section>')
        elif state == "error":
            inner = (f'{EXTRA_CSS}<section class="box"><h2>Er ging iets mis</h2><p>Dat ligt niet aan jou. Probeer '
                     f'het nog eens. Lukt het weer niet? Klik op <b>Technische details</b>, maak een schermafdruk en mail die '
                     f'naar hello@lerider.com. Kijk eerst even of je dat goed vindt: er kunnen bestandsnamen in '
                     f'staan.</p>{self._report(log)}{back}{details}</section>')
        else:
            inner = '<meta http-equiv="refresh" content="0;url=/">'
        return page("Countersign Shadow", inner)


def make_handler(app: Launcher):
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _send(self, code: int, body: bytes, ctype="text/html; charset=utf-8", extra: dict | None = None):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Security-Policy", "default-src 'none'; style-src 'unsafe-inline'; "
                             "form-action 'self'")
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            path = urllib.parse.urlparse(self.path).path
            if path == "/":
                return self._send(200, app.home_page())
            if path == "/bezig":
                return self._send(200, app.busy_page())
            self._send(404, "Pagina niet gevonden.".encode(), "text/plain; charset=utf-8")

        def do_POST(self):
            n = int(self.headers.get("Content-Length") or 0)
            if n > 4000:
                return self._send(413, "Te veel tekst in één keer.".encode(), "text/plain; charset=utf-8")
            form = {k: v[0] for k, v in urllib.parse.parse_qs(self.rfile.read(n).decode("utf-8", "replace")).items()}
            if not secrets.compare_digest(form.get("csrf", ""), app.token):
                return self._send(403, b"Verouderde pagina: laad de pagina opnieuw.", "text/plain; charset=utf-8")
            path = urllib.parse.urlparse(self.path).path
            if path in ("/demo", "/mine"):
                app.start_job(path[1:])
                return self._send(303, b"", extra={"Location": "/bezig"})
            if path == "/open-folder":
                app.input_dir.mkdir(exist_ok=True)
                from .__main__ import LEES_MIJ
                readme = app.input_dir / "LEES-MIJ.txt"
                if not readme.exists():
                    readme.write_text(LEES_MIJ, encoding="utf-8")
                try:
                    app.open_dir(app.input_dir)
                except OSError:
                    pass
                return self._send(303, b"", extra={"Location": "/"})
            if path == "/stop":
                self._send(200, page("Gestopt", '<section class="box"><h2>Countersign is gestopt</h2>'
                                                '<p>Je kunt dit tabblad nu sluiten. Je oordelen zijn bewaard. Later verder? Dubbelklik weer op '
                                                '"Countersign starten".</p>'
                                                '</section>'))
                app.stopped.set()
                return None
            self._send(404, "Pagina niet gevonden.".encode(), "text/plain; charset=utf-8")
    return H


def make_launcher_server(app: Launcher, port: int = LAUNCH_PORT) -> ThreadingHTTPServer:
    srv = LocalServer(("127.0.0.1", port), make_handler(app))
    app.home_url = f"http://127.0.0.1:{srv.server_address[1]}/"
    return srv


def main(root: Path, input_dir: Path, port: int = LAUNCH_PORT, open_browser: bool = True) -> int:
    url = f"http://127.0.0.1:{port}/"
    app = Launcher(root, input_dir)
    try:
        srv = make_launcher_server(app, port)
    except OSError:  # port taken: double-clicked twice, the start window is already running; just show it
        if open_browser:
            webbrowser.open(url)
        return 0
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    print(f"Countersign Shadow: het startscherm staat in je browser ({url}).\n"
          "Stoppen: klik op 'Countersign stoppen' in je browser, of sluit dit venster.")
    if open_browser:
        threading.Timer(0.5, lambda: webbrowser.open(url)).start()
    try:
        app.stopped.wait()
    except KeyboardInterrupt:
        pass
    srv.shutdown()
    srv.server_close()
    if app.review is not None:
        app.review.shutdown()
        app.review.server_close()
    return 0
