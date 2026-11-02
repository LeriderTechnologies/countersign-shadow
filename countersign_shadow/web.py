# SPDX-License-Identifier: Apache-2.0
"""Local review page (Dutch). stdlib http.server, bound to 127.0.0.1 only, CSRF token on every POST.

The page has exactly three kinds of buttons: goed / fout / gevaarlijk (feedback), and "save summary".
There is no approve, pay or send button, because shadow mode has nothing to approve.
"""
from __future__ import annotations

import html
import json
import secrets
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import feedback as fbmod
from .store import Workspace

CSS = """
:root{--bg:#f6f7f9;--card:#fff;--ink:#16202a;--mute:#5b6773;--line:#dde2e8;--ok:#127a3e;--bad:#b42318;
--warn:#a15c00;--acc:#0b57d0;--okbg:#e8f5ec;--badbg:#fdecea;--warnbg:#fff4e0;--chip:#eef1f5}
@media (prefers-color-scheme:dark){:root{--bg:#0f1418;--card:#171e24;--ink:#e6edf3;--mute:#9aa7b3;--line:#2a343d;
--ok:#4ac27a;--bad:#ff7b72;--warn:#e3a23b;--acc:#7aa7ff;--okbg:#14301f;--badbg:#3a1714;--warnbg:#3a2a10;--chip:#222b33}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:16px/1.5 system-ui,sans-serif}
header{padding:14px 16px;border-bottom:1px solid var(--line)}h1{font-size:20px;margin:0}
main{max-width:900px;margin:0 auto;padding:8px 16px 48px}.mute{color:var(--mute);font-size:14px}
.box{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:14px 16px;margin:14px 0}
.top{display:flex;gap:8px;align-items:baseline;flex-wrap:wrap}.top h2{font-size:17px;margin:0;flex:1 1 auto}
.badge{font-size:13px;font-weight:600;padding:2px 10px;border-radius:999px;background:var(--chip)}
.v-ok{color:var(--ok);background:var(--okbg)}.v-blocked,.v-flagged{color:var(--bad);background:var(--badbg)}
.v-warn{color:var(--warn);background:var(--warnbg)}.v-none{color:var(--mute)}
.amt{font-size:20px;font-weight:650}.reason{background:var(--badbg);color:var(--bad);border-radius:8px;padding:6px 10px;margin:6px 0;font-size:15px}
.reason.w{background:var(--warnbg);color:var(--warn)}.reason.g{background:var(--okbg);color:var(--ok)}
details{margin:6px 0}summary{cursor:pointer;color:var(--mute);font-size:14px}pre{white-space:pre-wrap;font:14px/1.45 system-ui,sans-serif;margin:6px 0}
ul.checks{list-style:none;padding:0;margin:6px 0;font-size:13px}.y{color:var(--ok)}.n{color:var(--bad)}
form.fb{display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin-top:10px;border-top:1px solid var(--line);padding-top:10px}
button{font:inherit;border-radius:8px;border:1px solid var(--line);padding:6px 14px;cursor:pointer;background:var(--card);color:var(--ink)}
button.right{border-color:var(--ok);color:var(--ok)}button.wrong{border-color:var(--warn);color:var(--warn)}
button.dangerous{border-color:var(--bad);color:var(--bad)}button.on{color:#fff}button.right.on{background:var(--ok)}
button.wrong.on{background:var(--warn)}button.dangerous.on{background:var(--bad)}button.primary{background:var(--acc);color:#fff;border-color:var(--acc)}
input[type=text]{font:inherit;flex:1 1 220px;min-width:0;padding:6px 8px;border-radius:8px;border:1px solid var(--line);background:var(--card);color:var(--ink)}
label{font-size:14px;color:var(--mute)}.stats{display:flex;gap:10px;flex-wrap:wrap}.stat{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:8px 14px}
.stat b{display:block;font-size:20px}a{color:var(--acc)}
"""

CHECK_SEV = {"block": "", "warn": " w"}


def e(x) -> str:
    return html.escape("" if x is None else str(x))


def _payee(c: dict) -> str:
    p = c.get("payee") or {}
    bits = [p.get("name"), p.get("iban"), p.get("email")]
    return " · ".join(e(b) for b in bits if b)


def render_card(c: dict, msg: dict | None, fb: dict | None, token: str) -> str:
    amount = f'<div class="amt">€ {e(c["amount"])}</div>' if c.get("amount") and c["action"] != "no_action" else ""
    reasons = "".join(
        f'<div class="reason{CHECK_SEV.get(ch["severity"], "")}">{e(ch["uitleg"] or ch["detail"])}</div>'
        for ch in c["checks"] if not ch["ok"]) + "".join(
        f'<div class="reason g">{e(ch["goed"])}</div>' for ch in c["checks"] if ch["ok"] and ch.get("goed"))
    checks = "".join(f'<li class="{"y" if ch["ok"] else "n"}">{"✓" if ch["ok"] else "✗"} {e(ch["rule"])}: '
                     f'{e(ch["detail"])}</li>' for ch in c["checks"])
    body = e(msg.get("body", "")) if msg else "(bericht niet gevonden)"
    for pdf in (msg or {}).get("pdfs") or []:
        body += f"\n\n--- PDF-bijlage {e(pdf.get('name', ''))} ---\n" + e(pdf.get("text") or
                                                                         f"(niet gelezen: {pdf.get('warning')})")
    mark = (fb or {}).get("mark")
    btn = "".join(f'<button class="{m}{" on" if mark == m else ""}" name="mark" value="{m}">{fbmod.MARK_NL[m]}</button>'
                  for m in fbmod.MARKS)
    return f"""<section class="box" id="{e(c['id'])}">
<div class="top"><h2>{e(c['action_nl'])}: {e(c['subject'])}</h2>
<span class="badge v-{e(c['verdict'])}">{e(c['verdict_nl'])}</span></div>
<div class="mute">Van {e(c['from'])} · {e(c['date'])} · planner: {e(c['planner'])}</div>
{amount}<div>{_payee(c)}</div>
<p class="mute">{e(c['rationale'])}</p>{reasons}
<details><summary>Bericht en controles tonen</summary><pre>{body}</pre><ul class="checks">{checks}</ul></details>
<form class="fb" method="post" action="/feedback">
<input type="hidden" name="csrf" value="{token}"><input type="hidden" name="card" value="{e(c['id'])}">
<span class="mute">Klopt dit oordeel?</span>{btn}
<input type="text" name="comment" placeholder="Opmerking (optioneel)" value="{e((fb or {}).get('comment', ''))}">
<label><input type="checkbox" name="may_quote" value="1"{" checked" if (fb or {}).get("may_quote") else ""}>
mag geciteerd worden (anoniem)</label></form></section>"""


def page(title: str, inner: str) -> bytes:
    return f"""<!doctype html><html lang="nl"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>{e(title)}</title><style>{CSS}</style></head>
<body><header><h1>Countersign Shadow</h1><div class="mute">Schaduwmodus: er kan niets betaald of verstuurd worden.
Alles blijft op deze computer.</div></header><main>{inner}</main></body></html>""".encode()


def make_handler(ws: Workspace, token: str):
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):  # keep the terminal quiet
            pass

        def _send(self, code: int, body: bytes, ctype="text/html; charset=utf-8", extra: dict | None = None):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("Content-Security-Policy", "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'")
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            path = urllib.parse.urlparse(self.path).path
            if path == "/":
                return self._send(200, self.index())
            if path == "/samenvatting":
                s = fbmod.summary(ws)
                md = fbmod.summary_markdown(s)
                inner = (f'<p><a href="/">← terug</a></p><section class="box"><pre>{e(md)}</pre>'
                         f'<form method="post" action="/samenvatting"><input type="hidden" name="csrf" value="{token}">'
                         f'<button class="primary">Bewaar als bestand om te delen</button></form></section>')
                return self._send(200, page("Samenvatting", inner))
            self._send(404, b"not found", "text/plain")

        def do_POST(self):
            n = int(self.headers.get("Content-Length") or 0)
            if n > 20000:
                return self._send(413, b"too large", "text/plain")
            form = {k: v[0] for k, v in urllib.parse.parse_qs(self.rfile.read(n).decode("utf-8", "replace")).items()}
            if not secrets.compare_digest(form.get("csrf", ""), token):
                return self._send(403, b"bad token: reload the page", "text/plain")
            path = urllib.parse.urlparse(self.path).path
            if path == "/feedback":
                card = next((c for c in ws.load("cards", []) if c["id"] == form.get("card")), None)
                if not card or form.get("mark") not in fbmod.MARKS:
                    return self._send(400, b"unknown card or mark", "text/plain")
                fbmod.record(ws, card, form["mark"], form.get("comment", ""), form.get("may_quote") == "1")
                return self._send(303, b"", extra={"Location": f"/#{card['id']}"})
            if path == "/samenvatting":
                out = save_summary(ws)
                return self._send(200, page("Opgeslagen", f'<p><a href="/">← terug</a></p><section class="box">'
                                  f'Opgeslagen in <b>{e(out)}</b>. Lees het na en deel het alleen als je dat wilt.'
                                  f'</section>'))
            self._send(404, b"not found", "text/plain")

        def index(self) -> bytes:
            cards = ws.load("cards", [])
            fb = ws.load("feedback", {})
            msgs = {m["id"]: m for m in ws.messages}
            done = sum(1 for c in cards if c["id"] in fb)
            order = {"blocked": 0, "flagged": 1, "warn": 2, "ok": 3, "none": 4}
            cards = sorted(cards, key=lambda c: (c["id"] in fb, order.get(c["verdict"], 9), c["date"]))
            head = (f'<div class="stats"><div class="stat"><b>{len(cards)}</b>kaarten</div>'
                    f'<div class="stat"><b>{done}</b>beoordeeld</div></div>'
                    f'<p class="mute">Kijk per kaart of Countersign het goed inschat. <b>Goed</b> = klopt; <b>fout</b> = '
                    f'verkeerde actie of onnodig tegengehouden; <b>gevaarlijk</b> = had schade kunnen doen als je het '
                    f'had goedgekeurd. Klaar? <a href="/samenvatting">Bekijk de samenvatting</a>.</p>')
            if not cards:
                head += '<p class="box">Nog geen kaarten. Lees eerst je e-mail en transacties in (zie de gids).</p>'
            return page("Countersign Shadow", head + "".join(
                render_card(c, msgs.get(c["source_id"]), fb.get(c["id"]), token) for c in cards))
    return H


def save_summary(ws: Workspace):
    s = fbmod.summary(ws)
    out = ws.root / "deel-mij"
    out.mkdir(exist_ok=True)
    (out / "samenvatting.json").write_text(json.dumps(s, ensure_ascii=False, indent=1), encoding="utf-8")
    (out / "samenvatting.md").write_text(fbmod.summary_markdown(s), encoding="utf-8")
    return out


def make_server(ws: Workspace, port: int = 8766) -> ThreadingHTTPServer:
    token = secrets.token_urlsafe(24)
    return ThreadingHTTPServer(("127.0.0.1", port), make_handler(ws, token))
