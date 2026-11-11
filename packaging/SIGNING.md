# Code signing: what it costs and what it would remove

Nothing in the bundles is signed by us yet. Nothing has been bought. Prices checked 2026-11-11; confirm them
before paying.

## What a pilot user sees today (unsigned bundles from `build_bundles.py`)

| Platform | Prompt | Why | What signing would change |
|---|---|---|---|
| Windows | "Open File: Security Warning" / "Publisher could not be verified" → **Run** (once) | `Countersign starten.bat` carries the downloaded-from-internet mark | **Nothing.** A `.bat` file cannot carry an Authenticode signature. `python.exe`/`pythonw.exe` in the bundle are the python.org embeddable build, **already signed by the Python Software Foundation**, so no SmartScreen "unknown app" screen for them. Signing only matters if we replace the `.bat` with our own `.exe` launcher. |
| Windows | Blue SmartScreen screen "Windows protected your PC" (→ More info → Run anyway) | Mainly for unsigned `.exe` files without reputation; Windows 11 may also show it for a downloaded script, and Smart App Control (if on) can block unsigned scripts | Not seen in our wine test (wine has no SmartScreen); **verify on a real Windows 11 PC**. The LEES MIJ and guide describe the click-through. Signing would not help a `.bat` either way. |
| macOS ≤ 14 | "cannot be opened because the developer cannot be verified" → right-click → Open → Open | `.command` file from the internet, unsigned | Developer ID signing + notarization removes it, but only for a signed `.app` or a signed disk image, not for a plain `.command` script. |
| macOS 15+ | Same message, and right-click → Open no longer bypasses it: System Settings → Privacy & Security → **Open Anyway** | Sequoia removed the right-click bypass | Same as above. The `.command` clears the download mark on the bundled Python, so after this one step it runs. |

## Options

| Option | Price | Gives | Fit |
|---|---|---|---|
| **Windows: keep `.bat` + PSF-signed Python** (current) | €0 | One "Run" prompt, no SmartScreen | Good enough for a 3–10 person pilot. |
| SignPath Foundation (OSS) | €0 | Authenticode cert issued to *SignPath Foundation* (they are the publisher shown) | Needs OSI licence (Apache-2.0: yes), public repo (yes), MFA for all members, a published code-signing policy, signing from a CI build (GitHub Actions). Only useful once we ship an own `.exe`. Application, not a purchase. |
| Certum Open Source Code Signing (cloud, SimplySign) | ~€49/yr gross (card+reader set ~€69) | Authenticode cert in the developer's own name ("Open Source Developer, …") | Cheapest own-name option; SmartScreen reputation still builds up slowly for a new cert. |
| Azure Artifact Signing (was Trusted Signing), Basic | ~US$9.99/month (5,000 signatures) | Microsoft-issued cert, instant SmartScreen trust | Individual-developer onboarding was limited to a few countries (USA/Canada) in 2026; an organisation identity needs verifiable business history. Check the portal for NL before counting on it. Billing starts when the account is created. |
| OV/EV code signing from a commercial CA | ~€200–€500/yr, hardware token required | Own-name cert | Overkill for a pilot. |
| **Apple Developer Program** | €99/yr (incl. VAT) | Developer ID signing + notarization: no Gatekeeper prompt for a signed `.app`/`.dmg` | The only way to remove the macOS prompt. Also needs a small `.app` wrapper (Platypus-style shell app or a py2app build) instead of the `.command`. |

## Recommendation

1. Pilot (now): ship the unsigned bundles. Windows = one "Run" click; macOS = one trip to Privacy & Security,
   both described with screenshots-free plain steps in `LEES MIJ.txt` and `pilot/GIDS.md`.
2. If pilot users drop off at the Mac step: Apple Developer Program (€99/yr) + an `.app` wrapper. The maintainer decides
   and pays (no purchase made here).
3. Windows signing only when we ship an own `.exe` (e.g. a tray app); then apply to SignPath Foundation first (free),
   Certum open source (~€49/yr) as fallback.

Sources: shop.certum.eu (Open Source Code Signing pages), azure.microsoft.com/pricing/details/artifact-signing/,
learn.microsoft.com Q&A "can't create a new trusted signing individual identity" (2026-03), signpath.org/terms,
developer.apple.com/programs (fee).
