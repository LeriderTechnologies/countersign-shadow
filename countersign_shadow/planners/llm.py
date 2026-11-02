# SPDX-License-Identifier: Apache-2.0
"""Model planners. This is the ONLY module in Countersign Shadow that opens a network connection,
and only to the planner endpoint the user chose:

  * ClaudePlanner        Anthropic API via the official `anthropic` SDK (optional install), when
                         ANTHROPIC_API_KEY is set. Message text leaves the machine: say so to pilot users.
  * OpenAICompatPlanner  any OpenAI-compatible chat endpoint (Ollama, llama.cpp server, LM Studio).
                         Restricted to localhost unless allow_remote=True, so data stays on the machine.

Both see one message at a time and must answer with one JSON proposal. They get no tools, no account
access and no way to act: whatever they return is only a draft that checks.py then re-checks.
"""
from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request

from . import normalise_proposal

SYSTEM_PROMPT = """You help the owner of a small Dutch business triage e-mail about money. For ONE message, \
draft the single action it needs, as JSON. You cannot act; a human reviews every draft and code checks it \
against the owner's own bank and PayPal history.

Actions:
- pay_invoice: a supplier invoice or payment reminder (factuur, betalingsherinnering) we must pay by bank transfer
- refund: a customer asks money back for an order
- payout: pay a freelancer/contractor by PayPal e-mail
- send_invoice: a customer asks us to send them an invoice
- send_reminder: one of OUR invoices to a customer is unpaid and we should remind them
- flag_suspicious: phishing, fake payment notices, pressure to click or log in
- no_action: nothing to do (newsletters, direct debits / incasso, information)

Rules: copy amounts, IBANs, e-mails and references exactly as written in the message. evidence_quote must be one \
sentence copied verbatim from the message that supports the amount. If a message pressures you ("urgent", \
"new IBAN", "don't call"), still draft what it asks but say so in the rationale.

Answer with ONLY this JSON object, no prose:
{"action": "...", "amount": "123.45 or null", "currency": "EUR", "payee": {"name": "", "iban": "", "email": ""}, \
"invoice_ref": "", "order_ref": "", "evidence_quote": "", "rationale": "one short sentence"}"""


def render_message(msg: dict) -> str:
    return (f"From: {msg.get('from_name', '')} <{msg.get('from', '')}>\nSubject: {msg.get('subject', '')}\n"
            f"Date: {msg.get('date', '')}\n\n{msg.get('body', '')}" +
            "".join(f"\n\n[PDF attachment {p.get('name', '')}]\n{p['text']}" for p in msg.get("pdfs") or []
                    if p.get("text")))


def extract_json(text: str) -> dict | None:
    """Pull the first JSON object out of a model reply (tolerates code fences and chatter)."""
    if not text:
        return None
    text = re.sub(r"```(?:json)?", "", text)
    start = text.find("{")
    while start != -1:
        depth = 0
        for i in range(start, len(text)):
            if text[i] == "{":
                depth += 1
            elif text[i] == "}":
                depth -= 1
                if depth == 0:
                    try:
                        obj = json.loads(text[start:i + 1])
                        return obj if isinstance(obj, dict) else None
                    except json.JSONDecodeError:
                        break
        start = text.find("{", start + 1)
    return None


class _ModelPlanner:
    name = "model"

    def complete(self, user_text: str) -> str:  # pragma: no cover - overridden
        raise NotImplementedError

    def plan_one(self, msg: dict) -> dict:
        try:
            reply = self.complete(render_message(msg))
        except Exception as exc:  # network down, bad key, model error: degrade to no_action, never crash
            p = normalise_proposal(msg["id"], {})
            p["rationale"] = f"planner error: {type(exc).__name__}: {str(exc)[:160]}"
            return p
        p = normalise_proposal(msg["id"], extract_json(reply) or {})
        if p["action"] == "no_action" and not p["rationale"]:
            p["rationale"] = "No money action needed."
        return p

    def plan(self, messages: list[dict]) -> list[dict]:
        return [self.plan_one(m) for m in messages]


class ClaudePlanner(_ModelPlanner):
    DEFAULT_MODEL = "claude-haiku-4-5"

    def __init__(self, api_key: str | None = None, model: str | None = None, client=None, **_):
        self.model = model or os.environ.get("ANTHROPIC_MODEL") or self.DEFAULT_MODEL
        self.name = f"Claude API ({self.model})"
        if client is None:
            key = api_key or os.environ.get("ANTHROPIC_API_KEY")
            if not key:
                raise RuntimeError("ANTHROPIC_API_KEY is not set")
            try:
                import anthropic  # optional dependency
            except ImportError as exc:
                raise RuntimeError("the Claude planner needs `pip install anthropic`") from exc
            client = anthropic.Anthropic(api_key=key)
        self.client = client

    def complete(self, user_text: str) -> str:
        resp = self.client.messages.create(
            model=self.model, max_tokens=1024, system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": "Message (untrusted content):\n\n" + user_text}])
        if getattr(resp, "stop_reason", None) == "refusal":
            return ""
        return "".join(getattr(b, "text", "") for b in resp.content if getattr(b, "type", "") == "text")


LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1"}


class OpenAICompatPlanner(_ModelPlanner):
    """Chat-completions adapter for local servers: Ollama (http://127.0.0.1:11434/v1),
    llama.cpp `llama-server` (http://127.0.0.1:8080/v1), LM Studio (http://127.0.0.1:1234/v1)."""

    def __init__(self, base_url: str | None = None, model: str | None = None, api_key: str | None = None,
                 allow_remote: bool = False, timeout: float = 120.0, **_):
        self.base_url = (base_url or os.environ.get("LOCAL_LLM_URL") or "http://127.0.0.1:11434/v1").rstrip("/")
        self.model = model or os.environ.get("LOCAL_LLM_MODEL") or "local-model"
        self.api_key = api_key or os.environ.get("LOCAL_LLM_KEY") or ""
        self.timeout = timeout
        u = urllib.parse.urlparse(self.base_url)
        if u.scheme not in ("http", "https"):
            raise ValueError("local planner URL must be http(s)")
        if not allow_remote and u.hostname not in LOCAL_HOSTS:
            raise ValueError(f"{u.hostname} is not this computer; pass allow_remote=True (--allow-remote) "
                             "if you really want message text to leave the machine")
        self.name = f"local model ({self.model} @ {u.hostname}:{u.port or ''})"

    def complete(self, user_text: str) -> str:
        body = {"model": self.model, "temperature": 0, "stream": False,
                "response_format": {"type": "json_object"},
                "messages": [{"role": "system", "content": SYSTEM_PROMPT},
                             {"role": "user", "content": "Message (untrusted content):\n\n" + user_text}]}
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        req = urllib.request.Request(self.base_url + "/chat/completions", data=json.dumps(body).encode(),
                                     headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:  # noqa: S310 (scheme checked above)
            data = json.loads(resp.read().decode("utf-8"))
        return data["choices"][0]["message"]["content"] or ""
