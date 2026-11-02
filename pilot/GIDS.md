# Gids: Countersign Shadow in 30 minuten

Je hebt een map **countersign-shadow** gekregen (meestal als .zip). Je hoeft geen programmeur te zijn.
**Countersign kan niets betalen of versturen, en je gegevens blijven op je computer.**

| Stap | Wat | Tijd |
|---|---|---|
| 1 | Python installeren (als je dat nog niet hebt) | 5–10 min |
| 2 | De demo proberen | 2 min |
| 3 | Je e-mails en bankexport in één map zetten | 10 min |
| 4 | Starten en beoordelen | 10–15 min |
| 5 | Samenvatting bekijken en (als je wilt) delen | 2 min |

## 1. Python installeren

Countersign is geschreven in Python, een gratis programmeertaal. Je hoeft er niets van te weten.

- **Windows:** ga naar **python.org/downloads**, klik op de gele knop "Download Python 3.x" en open het bestand.
  **Vink onderaan "Add python.exe to PATH" aan** en klik op "Install Now".
- **Mac:** ga naar **python.org/downloads**, download de macOS-installer en klik je door de stappen.
- **Linux:** Python 3 staat er meestal al op. Typ `python3 --version` in een terminal om het te controleren.

Je hebt Python 3.10 of nieuwer nodig.

Countersign leest ook PDF-facturen in bijlagen en Outlook-mails (.msg); daarvoor hoef je niets extra te
installeren.

## 2. De demo proberen (verzonnen gegevens)

Pak de .zip uit (rechtsklik → "Alles uitpakken" op Windows; dubbelklik op een Mac).

- **Windows:** dubbelklik op **start-demo-windows.bat**. Krijg je een blauw scherm "Windows heeft uw pc beschermd"?
  Klik op "Meer info" → "Toch uitvoeren".
- **Mac:** rechtsklik op **start-demo-mac.command** → "Open" → "Open". (De eerste keer moet het via rechtsklik,
  omdat het bestand niet uit de App Store komt.)
- **Linux:** `./start-linux.sh --demo`

Er gaat een zwart venster open (laat dat open staan) en je browser toont 21 voorbeeldkaarten van een verzonnen
webwinkel, "Studio Klei". Kijk even rond. Sluit daarna het zwarte venster.

## 3. Je eigen gegevens in één map zetten

Maak in de map countersign-shadow een map **mijn-gegevens** (of start één keer stap 4; dan wordt hij voor je
gemaakt). Zet daarin:

**a. 20 tot 50 zakelijke e-mails over geld**: facturen, betalingsherinneringen, vragen om terugbetaling,
bestellingen, en als je die hebt ook een paar verdachte mails uit je spammap. **Facturen als PDF-bijlage zijn
juist welkom**: bij het downloaden van een mail als .eml, .msg of .mbox gaat de bijlage vanzelf mee. Exporteren gaat per e-mailprogramma
anders (de menunamen kunnen per versie iets verschillen):

| Programma | Hoe |
|---|---|
| Gmail (web) | Open de mail → ⋮ (drie puntjes rechtsboven) → **Bericht downloaden**. Je krijgt een .eml-bestand. |
| Outlook op het web / nieuwe Outlook | Open de mail → ··· → **Opslaan als** of **Downloaden** → .eml |
| Klassieke Outlook (Windows) | Sleep de mail naar de map mijn-gegevens, of Bestand → **Opslaan als** → .msg. Countersign leest .msg. Lukt dat bij een mail niet, kies dan bij Opslaan als het type .eml (of download de mail via Outlook op het web). |
| Apple Mail | Selecteer mails → Archief → **Bewaar als…** → kies "Onbewerkte berichtbron", of selecteer een map → Postbus → **Exporteer postbus** (.mbox) |
| Thunderbird | Selecteer mails → rechtsklik → **Opslaan als** → .eml |

**b. Een export van je transacties**, bij voorkeur de laatste 3 tot 6 maanden. Countersign gebruikt die om te zien
welke rekeningnummers je al kent en welke facturen al betaald zijn.

| Bank | Hoe (ongeveer) | Bestand |
|---|---|---|
| ING | Mijn ING → je rekening → **Af- en bijschrijvingen downloaden** → formaat "Kommagescheiden CSV" | .csv |
| Rabobank | Rabo Online Bankieren → **Transacties downloaden** → formaat "CSV" | .csv |
| ABN AMRO | Internet Bankieren → **Mutaties downloaden** → formaat "TXT" | .txt |
| ASN Bank / SNS / RegioBank | Online bankieren → **Transacties downloaden/exporteren** → formaat "CSV" (of CAMT.053, werkt ook) | .csv / .xml |
| bunq, Knab, Triodos | Exporteer je rekening als **MT940** of **CAMT.053** (in de app of Mijn-omgeving, bij "downloads" of "exporteren") — de eigen CSV van deze drie banken kent Countersign nog niet, maar deze twee formaten wel | .sta / .xml |
| PayPal | Activiteit → **Rapporten** → **Activiteit downloaden** → "Alle transacties", CSV | .csv |

Andere bank? Neem contact op via hello@lerider.com; dan kijken we of dat formaat erbij kan.

## 4. Starten en beoordelen

- **Windows:** dubbelklik op **start-windows.bat**
- **Mac:** rechtsklik op **start-mac.command** → Open
- **Linux:** `./start-linux.sh`

Het zwarte venster laat zien hoeveel mails en transacties zijn ingelezen. Daarna opent je browser met één kaart per
mail. Per kaart zie je wat Countersign zou doen en waarom ("factuur al betaald", "nieuw rekeningnummer", "lijkt op
phishing", "op de PDF-factuur staat een ander IBAN dan in de mail", "bedrag op de PDF wijkt af"). Onder "Bericht en
controles tonen" staat ook de tekst die uit de PDF-bijlage is gelezen. Klik bij elke kaart op:

- **goed**: het oordeel klopt;
- **fout**: verkeerde actie, of onnodig tegengehouden;
- **gevaarlijk**: als je dit had goedgekeurd, had het schade kunnen doen.

Je mag een korte opmerking toevoegen. Vink **"mag geciteerd worden"** alleen aan als je het goed vindt dat die
opmerking (zonder namen, bedragen en rekeningnummers) in de samenvatting komt. Je kunt tussendoor stoppen; je oordelen
worden direct bewaard.

## 5. Samenvatting bekijken en delen

Klik bovenaan op **"Bekijk de samenvatting"** en daarna op **"Bewaar als bestand om te delen"**. In de map
`pilot-data/deel-mij/` staat dan `samenvatting.md` (leesbaar) en `samenvatting.json`. **Lees het bestand eerst
zelf.** Wil je meedoen met de aanvraag? Stuur het bestand en het ingevulde toestemmingsformulier
(TOESTEMMING.md) naar Lerider Technologies (hello@lerider.com). Niet gedeeld = niet gebruikt.

## Opruimen

Verwijder de map countersign-shadow. Daarmee zijn ook je ingelezen gegevens en oordelen weg (die staan in
`pilot-data/` en `mijn-gegevens/`). Python mag blijven staan of kun je verwijderen via je systeeminstellingen.

## Vraag: "Mijn bank doet toch al de IBAN-Naam-Check?"

Ja, en die helpt. Sinds 9 oktober 2025 controleert je bank bij elke overboeking in euro of de naam die jij
intikt bij het IBAN hoort. Tik je de naam van je vaste leverancier in bij de rekening van een oplichter, dan krijg
je een waarschuwing. Maar de check heeft grenzen:

- **Hij vergelijkt de naam die jij intikt.** Zet een oplichter "t.n.v. J. de Vries" op de factuur en staat de
  rekening echt op naam van J. de Vries (een "geldezel"), dan zegt je bank: naam klopt.
- **Je kunt elke waarschuwing wegklikken.** Doe je dat, dan draag je zelf het risico. Kifid: "Maakt u ondanks
  deze waarschuwing geld over, dan blijft u als klant verantwoordelijk voor de betaalopdracht." In
  [uitspraak 2026-0901](https://www.kifid.nl/media/cslixrxj/uitspraak-2026-0901-bindend.pdf) stond er in het
  adresboek van een klant ineens het rekeningnummer van een onbekende; de bank meldde dat naam en rekeningnummer
  niet overeenkwamen, hij betaalde toch € 1.420 en kreeg het niet vergoed.
- **Je bank weet niet wat normaal is voor jou:** welk rekeningnummer je leverancier altijd gebruikte, of je deze
  factuur al betaald hebt, of het bedrag op de PDF klopt.
- **Niet alles valt eronder:** zakelijke betaalbestanden (batches) mogen zonder check, en ook betalingen in andere
  valuta, buiten Europa en via PayPal.
- **Hij komt pas bij het betalen.** Countersign kijkt eerder: bij de mail en de factuur, tegen je eigen
  betaalgeschiedenis. Een "Belastingdienst"-rekeningnummer dat niet op de officiële lijst van de Belastingdienst
  staat, houdt Countersign tegen.

Countersign vervangt de IBAN-Naam-Check niet, het komt ervoor. Gebruik ze allebei.

## Problemen?

- *"Python niet gevonden"*: herhaal stap 1 en zet op Windows het vinkje "Add python.exe to PATH". Herstart de pc.
- *De browser opent niet*: open zelf **http://127.0.0.1:8766/**.
- *"0 messages"*: staan de mails als .eml, .msg of .mbox in de map mijn-gegevens? Zie je "overgeslagen: ... .msg"?
  Sla die mail in Outlook op als .eml en zet hem in mijn-gegevens.
- *"kan PDF niet lezen"*: de PDF is gescand (een foto van papier), beveiligd met een wachtwoord of kapot. Staat
  er "pypdf ontbreekt", dan is de map countersign-shadow niet compleet uitgepakt: pak de .zip opnieuw uit. Gescande PDF's leest Countersign niet (geen tekstherkenning). Controleer het IBAN
  op zo'n factuur dus zelf, en geef de kaart gerust "fout" met een opmerking.
- *"unknown transaction export"*: je bankbestand heeft een formaat dat Countersign (nog) niet kent. Laat het
  ons weten (hello@lerider.com); stuur het bestand niet op.
- Lukt het niet: we kunnen het samen doen via scherm delen. Jouw gegevens blijven dan op jouw computer.
