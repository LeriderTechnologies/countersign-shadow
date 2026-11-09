# Privacy

Countersign Shadow is a local, shadow-mode tool: it reads your own mail and bank/PayPal exports on your own
computer to draft decision cards, and nothing it does can execute a payment. This page says plainly what
personal data the tool touches, where it goes, and how to get rid of it.

## What personal data the tool touches

When you import your own inbox or bank/PayPal export, the tool stores what it reads in plain JSON files under
`pilot-data/` (your own folder, next to the code, git-ignored so it is never committed): sender and recipient
e-mail addresses, IBANs, names (yours, your customers', your suppliers'), amounts, invoice numbers, and the
message/attachment text needed to draft and check each decision card (`messages.json`, `transactions.json`,
`cards.json`, `feedback.json`, an optional `policy.json`, and a random `pilot.json` pilot id — see the README's
"Workspace files" section). This is the same personal data already in your own inbox and bank export; the tool
does not collect anything beyond what you import into it.

## Where it goes: nowhere, by default

All of this stays on your own machine. By default (`--planner rules`, the offline rule-based planner) the tool
makes no network connection of any kind:

- `tests/test_shadow.py::TestNoMoneyPath::test_only_the_planner_module_can_open_connections` — an AST scan of
  every module (including the vendored pypdf/olefile) shows the only one that can import a networking library
  is `planners/llm.py`;
- `tests/test_shadow.py::TestNoMoneyPath::test_full_pipeline_runs_with_network_disabled` — the full import →
  draft → feedback → summary pipeline is run with `socket.socket.connect` and `socket.create_connection`
  patched to raise, and it still completes;
- the local review page binds to `127.0.0.1` only (`countersign_shadow/web.py`), so it is never reachable from
  another machine, and every write to it is CSRF-protected.

**The one exception, and it needs your own choice:** if you explicitly run with `--planner claude`, each message's
sender name and address, subject, date, body, and any extracted PDF-attachment text (invoices may carry IBANs) is
sent to Anthropic's API, under your own `ANTHROPIC_API_KEY` — see the README's "Planners" table. Anthropic's own
data terms apply. Lerider Technologies is not a party to that call and does not see it. A `--planner local` option
refuses any non-localhost URL unless you pass `--allow-remote`; if you do pass it, the same fields (sender name
and address, subject, date, body, extracted PDF-attachment text) are sent the same way to the OpenAI-compatible
endpoint you point it at yourself (for example a local Ollama/llama.cpp server, or a remote one you've opted
into).

Lerider Technologies, the organisation that publishes this tool, never receives, sees, or stores any of your
data by you running it. There is no server-side component, account, or sign-up.

## No telemetry

The tool has no analytics, crash reporting, update checker, or usage tracking of any kind. Nothing about how you
use it, or whether you use it at all, is sent anywhere.

## What you might choose to share

The only thing the tool is built to let you voluntarily share is a scrubbed summary:
`pilot-data/deel-mij/samenvatting.{md,json}` — counts per verdict/action/planner and three safety numbers, plus
only the feedback comments you opted in to, with e-mail addresses, IBANs, amounts, numbers and links removed
(no message text, subjects, names or amounts). You read it yourself and decide whether to send it to anyone;
the tool never sends it for you. See [`pilot/TOESTEMMING.md`](pilot/TOESTEMMING.md) (Dutch) for the consent text
covering that one file.

## Retention and deletion

Everything the tool stores is in plain files under `pilot-data/` (and `demo-data/`, `mijn-gegevens/`,
`deel-mij/` for demo/export variants) on your own machine, under your own control. There is nothing to ask
anyone to delete: stop using the tool and delete that folder (or the files inside it) the same way you would
delete any other file. No copy is kept anywhere else by this project.

## Contact

Questions about this policy, or about data handling in the tool: hello@lerider.com (the project's contact
address; see also [`SECURITY.md`](SECURITY.md) for reporting a check that gives the wrong answer).
