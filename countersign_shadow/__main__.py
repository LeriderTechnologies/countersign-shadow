# SPDX-License-Identifier: Apache-2.0
"""Countersign Shadow command line.

    python3 -m countersign_shadow start                 # double-click flow: import mijn-gegevens/, draft, open page
    python3 -m countersign_shadow start --demo          # same with the synthetic Studio Klei data
    python3 -m countersign_shadow launch                # plain-language Dutch start window in the browser
    python3 -m countersign_shadow import-inbox FILES...         (.eml, .msg, .mbox, Takeout .zip, or folders)
    python3 -m countersign_shadow import-transactions FILES...  (PayPal CSV, ING / Rabobank / ASN-SNS-RegioBank
                                                                  CSV, ABN AMRO TXT, CAMT.053 XML, MT940)
    python3 -m countersign_shadow draft [--planner rules|claude|local]
    python3 -m countersign_shadow serve [--port 8766]
    python3 -m countersign_shadow summary               # writes deel-mij/samenvatting.{md,json}
    python3 -m countersign_shadow compare --planners rules,local
    python3 -m countersign_shadow demo                  # headless run on the synthetic data, prints a table
"""
from __future__ import annotations

import argparse
import os
import sys
import tempfile
import threading
import webbrowser
from pathlib import Path

from . import feedback as fbmod
from .importers import inbox as inbox_imp
from .importers import transactions as tx_imp
from .planners import make_planner
from .shadow import run
from .store import Workspace

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DATA = Path(os.environ.get("COUNTERSIGN_DATA") or ROOT / "pilot-data")
INPUT_DIR = Path(os.environ.get("COUNTERSIGN_INPUT") or ROOT / "mijn-gegevens")  # bundles: next to the start file
MAIL_EXT = {".eml", ".msg", ".mbox", ".mbx", ".zip"}
TX_EXT = {".csv", ".txt", ".tab", ".xml", ".sta", ".940", ".mt940", ".swi", ".mta"}  # CSV/TXT, CAMT.053, MT940
LEES_MIJ = ("Zet hier je e-mails en je transactie-export.\n\n"
            "E-mails: losse .eml- of .msg-bestanden, een .mbox, of je hele Gmail-export van Google Takeout "
            "(het .zip-bestand mag zo blijven). Bij een grote export kiest Countersign zelf de mails over geld "
            "uit de laatste 6 maanden.\n"
            "Transacties: de .csv of .txt van PayPal, ING, Rabobank, ABN AMRO, ASN/SNS/RegioBank, "
            "of een MT940- of CAMT.053-bestand.\n\nDeze map verlaat je computer niet.\n")


def _planner_from_args(a) -> object:
    kind = a.planner or os.environ.get("COUNTERSIGN_PLANNER") or "rules"
    kw = {}
    if kind == "local":
        kw = {"base_url": a.url, "model": a.model, "allow_remote": a.allow_remote}
    elif kind == "claude":
        kw = {"model": a.model}
    return make_planner(kind, **kw)


def cmd_import_inbox(ws: Workspace, paths: list[str]) -> int:
    problems: list[str] = []
    info: list[str] = []
    msgs = inbox_imp.import_paths(paths, problems, info)
    for line in info:
        print(f"[inbox] {line}")
    added, total = ws.merge("messages", msgs)
    print(f"[inbox] {len(msgs)} messages read, {added} new, {total} in workspace")
    pdfs = [p for m in msgs for p in m.get("pdfs") or []]
    if pdfs:
        unread = [p for p in pdfs if p.get("warning")]
        print(f"[inbox] {len(pdfs)} PDF-bijlagen, {len(pdfs) - len(unread)} gelezen")
        for p in unread:
            print(f"[inbox] kan PDF niet lezen: {p['name']}: {p['warning']}")
    for msg in problems:
        print(f"[inbox] overgeslagen: {msg}")
    return added


def cmd_import_tx(ws: Workspace, paths: list[str], fmt: str = "auto") -> int:
    added_all = 0
    for p in paths:
        warnings: list[str] = []
        try:
            kind, rows = tx_imp.import_file(p, fmt, warnings)
        except tx_imp.ImportError_ as exc:
            print(f"[transactions] {Path(p).name}: dit bankbestand kent Countersign (nog) niet. Laat het ons weten; "
                  f"stuur het bestand niet op. ({exc})")
            continue
        added, total = ws.merge("transactions", rows)
        added_all += added
        print(f"[transactions] {Path(p).name}: {kind}, {len(rows)} rows, {added} new, {total} in workspace")
        for w in warnings:
            print(f"[transactions] waarschuwing: {w}")
    return added_all


def cmd_draft(ws: Workspace, planner) -> list[dict]:
    cards = run(ws, planner)
    counts: dict = {}
    for c in cards:
        counts[c["verdict"]] = counts.get(c["verdict"], 0) + 1
    print(f"[draft] {planner.name}: {len(cards)} cards " + ", ".join(f"{k}={v}" for k, v in sorted(counts.items())))
    return cards


def print_table(cards: list[dict]) -> None:
    for c in cards:
        amt = f"€{c['amount']}" if c.get("amount") and c["action"] != "no_action" else ""
        print(f"  {c['verdict']:<8} {c['action']:<16} {amt:>10}  {c['subject'][:52]}")
        for r in c["reasons"][:2]:
            print(f"           - {r[:110]}")


def serve(ws: Workspace, port: int, open_browser: bool = True) -> None:
    from .web import make_server
    srv = make_server(ws, port)
    url = f"http://127.0.0.1:{srv.server_address[1]}/"
    print(f"\nCountersign Shadow draait op {url}  (alleen op deze computer)\nStoppen: Ctrl+C of sluit dit venster.\n")
    if open_browser:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()


def _starts_with_from(p: Path) -> bool:
    from .importers.bulk import is_mbox_file
    return is_mbox_file(p)


def import_folder(ws: Workspace, folder: Path) -> tuple[int, int]:
    files = sorted(p for p in folder.rglob("*") if p.is_file() and not p.name.startswith("."))
    mail = [str(p) for p in files if p.suffix.lower() in MAIL_EXT or (p.suffix == "" and _starts_with_from(p))]
    txs = [str(p) for p in files if p.suffix.lower() in TX_EXT and p.name.lower() != "lees-mij.txt"]
    return (cmd_import_inbox(ws, mail) if mail else 0), (cmd_import_tx(ws, txs) if txs else 0)


def demo_workspace(path: Path) -> Workspace:
    from .samples import write_samples
    src = write_samples(path / "samples")
    ws = Workspace(path)
    cmd_import_inbox(ws, [str(src / "inbox")])
    cmd_import_tx(ws, [str(src / n) for n in ("paypal.csv", "ing.csv", "rabobank.csv", "abnamro.txt")])
    return ws


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="countersign_shadow", description="Countersign shadow mode (nothing executes)")
    ap.add_argument("--data", default=str(DEFAULT_DATA), help="workspace folder (default: pilot-data/)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("import-inbox")
    s.add_argument("paths", nargs="+")
    s = sub.add_parser("import-transactions")
    s.add_argument("paths", nargs="+")
    s.add_argument("--format", default="auto", choices=("auto",) + tx_imp.FORMATS)
    for name in ("draft", "start", "compare"):
        s = sub.add_parser(name)
        s.add_argument("--planner", choices=("rules", "claude", "local"))
        s.add_argument("--model", help="model id (claude: default claude-haiku-4-5; local: the server's model name)")
        s.add_argument("--url", help="local OpenAI-compatible endpoint, e.g. http://127.0.0.1:11434/v1 (Ollama)")
        s.add_argument("--allow-remote", action="store_true", help="allow a non-localhost --url (data leaves the machine)")
        if name == "start":
            s.add_argument("--demo", action="store_true", help="use the synthetic Studio Klei data")
            s.add_argument("--port", type=int, default=8766)
            s.add_argument("--no-browser", action="store_true")
        if name == "compare":
            s.add_argument("--planners", default="rules", help="comma list, e.g. rules,local,claude")
    s = sub.add_parser("serve")
    s.add_argument("--port", type=int, default=8766)
    s.add_argument("--no-browser", action="store_true")
    s = sub.add_parser("launch", help="plain-language Dutch start window in the browser (double-click files)")
    s.add_argument("--port", type=int, default=8765)
    s.add_argument("--no-browser", action="store_true")
    sub.add_parser("summary")
    sub.add_parser("demo")
    a = ap.parse_args(argv)

    if a.cmd == "demo":
        from .samples import EXPECTED
        with tempfile.TemporaryDirectory() as tmp:
            ws = demo_workspace(Path(tmp))
            cards = cmd_draft(ws, make_planner("rules"))
            print_table(cards)
            by_origin = {m["id"]: m["origin"] for m in ws.messages}
            hits = sum(1 for c in cards if EXPECTED.get(by_origin[c["source_id"]]) == (c["action"], c["verdict"]))
            print(f"\n[demo] rule planner matches the expected action+verdict on {hits}/{len(cards)} messages. "
                  "Nothing was executed; there is no code that could.")
        return 0

    if a.cmd == "launch":
        from . import launcher
        return launcher.main(ROOT, INPUT_DIR, a.port, not a.no_browser)

    if a.cmd == "start" and a.demo and a.data == str(DEFAULT_DATA):
        a.data = str(ROOT / "demo-data")  # keep the demo apart from the user's own pilot data
    ws = Workspace(a.data)
    if a.cmd == "import-inbox":
        cmd_import_inbox(ws, a.paths)
    elif a.cmd == "import-transactions":
        cmd_import_tx(ws, a.paths, a.format)
    elif a.cmd == "draft":
        print_table(cmd_draft(ws, _planner_from_args(a)))
    elif a.cmd == "compare":
        rows: dict = {}
        names = []
        for kind in a.planners.split(","):
            a.planner = kind.strip()
            planner = _planner_from_args(a)
            names.append(planner.name)
            for c in cmd_draft(ws, planner):
                rows.setdefault(c["source_id"], {"subject": c["subject"]})[planner.name] = f"{c['action']}/{c['verdict']}"
        same = sum(1 for r in rows.values() if len({r.get(n) for n in names}) == 1)
        for r in rows.values():
            print(f"  {r['subject'][:40]:<40} " + " | ".join(f"{r.get(n, '-'):<26}" for n in names))
        print(f"[compare] planners agree on {same}/{len(rows)} messages ({', '.join(names)})")
    elif a.cmd == "serve":
        serve(ws, a.port, not a.no_browser)
    elif a.cmd == "summary":
        from .web import save_summary
        out = save_summary(ws)
        print(fbmod.summary_markdown(fbmod.summary(ws)))
        print(f"[summary] saved to {out}/samenvatting.md and .json. Read it before you share it.")
    elif a.cmd == "start":
        if a.demo:
            if not ws.messages:
                ws = demo_workspace(ws.root)
        else:
            INPUT_DIR.mkdir(exist_ok=True)
            readme = INPUT_DIR / "LEES-MIJ.txt"
            if not readme.exists():
                readme.write_text(LEES_MIJ,
                                  encoding="utf-8")
            import_folder(ws, INPUT_DIR)
            if not ws.messages:
                print(f"\nNog geen e-mails gevonden in {INPUT_DIR}.\nZet je bestanden daar en start opnieuw, "
                      "of probeer eerst de demo (start-demo).")
                input("Druk op Enter om af te sluiten...") if sys.stdin.isatty() else None
                return 1
        cmd_draft(ws, _planner_from_args(a))
        serve(ws, a.port, not a.no_browser)
    return 0


if __name__ == "__main__":
    sys.exit(main())
