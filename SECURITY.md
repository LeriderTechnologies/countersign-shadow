# Security policy

Countersign Shadow never moves money, but people may rely on its warnings. This project follows CRA-aligned
practices for open-source software (a published reporting channel, response targets, a stated support period and a
machine-readable SBOM). It does not claim compliance with any regulation.

## Reporting a vulnerability

Please e-mail **hello@lerider.com** with "SECURITY" in the subject, instead of opening a public issue.

Examples of what we want to hear about:
- a message that makes a check give the wrong answer: a fraud mail that is waved through, a changed IBAN that is
  not flagged, an official payee that is spoofed;
- a crash, hang or excessive memory use on an import file (mbox, .eml, .msg, PDF attachment, bank export);
- anything that lets the local review page (bound to 127.0.0.1) be reached or driven from another site or machine;
- a weakness in the vendored pypdf or olefile copies that affects this tool.

Use synthetic data only: never send a real mail, bank export or IBAN of a real person. A minimal reproducer (a
made-up message or file plus the command you ran) helps most.

## What happens next

| Step | Target |
|---|---|
| Acknowledge your report | within 5 working days |
| First assessment (confirmed or not, severity) | within 14 days |
| Fix or mitigation on `main` for a confirmed issue | within 30 days for issues that can wave a fraudulent payment through or run code; otherwise in the next release |
| Public disclosure | after the fix is released, or 90 days after your report, whichever comes first; earlier by agreement |

We credit you in the release notes if you like. These are targets for a small team, not a contractual service
level. Advisories for confirmed issues are published on this repository.

## Supported versions

Only the latest release on the `main` branch receives security fixes; there are no maintenance branches. See
[`SUPPORT.md`](SUPPORT.md) for the support period.

## Third-party components

The software bill of materials (CycloneDX 1.6, JSON) is in [`sbom/countersign.cdx.json`](sbom/countersign.cdx.json)
and is regenerated for every release by `tools/sbom.sh`. The core uses the Python standard library only; the
shipped third-party code is the vendored pypdf and olefile in `countersign_shadow/_vendor/`. We watch their
security advisories and update the vendored copies when a fix affects this tool. The optional `anthropic` package
(Claude API planner only) is not shipped and is not in the SBOM.
