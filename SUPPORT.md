# Support

## Support period

Countersign Shadow is released from the `main` branch; each release is identified by its date (the version in
[`sbom/countersign.cdx.json`](sbom/countersign.cdx.json)).

- **Security fixes:** the latest release on `main` receives security fixes **until at least 31 December 2027**.
  This date is extended here as long as the project is maintained. If support is going to end, that is announced in
  this file and in the README at least three months ahead.
- **Older releases:** not patched. Update to the latest `main` to get a fix.
- **Python:** 3.10 and newer. A Python version is dropped no earlier than its own end of life upstream.

How to report a vulnerability, and the response targets: [`SECURITY.md`](SECURITY.md).

## Questions and bugs

Ordinary bugs and questions: open an issue on this repository, using synthetic data only. For anything that could
help someone get a fraudulent payment past the checks, use the private channel in [`SECURITY.md`](SECURITY.md)
instead.
