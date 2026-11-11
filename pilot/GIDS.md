# Gids: Countersign Shadow in 30 minuten

Je krijgt één bestand: **Countersign-Shadow-Windows.zip** of **Countersign-Shadow-Mac.zip**. Je hoeft geen
programmeur te zijn en je hoeft niets te installeren.
**Countersign kan niets betalen of versturen, en je gegevens blijven op je computer.**

| Stap | Wat | Tijd |
|---|---|---|
| 1 | Uitpakken en starten | 2 min |
| 2 | De demo proberen | 2 min |
| 3 | Je e-mails en transacties in de map mijn-gegevens zetten | 5–10 min (een Gmail-export maken kan langer duren: Google mailt je als hij klaar is) |
| 4 | Starten en beoordelen | 10–15 min |
| 5 | Samenvatting bekijken en (als je wilt) delen | 2 min |

## 1. Uitpakken en starten

Zet de map niet in OneDrive, Dropbox of iCloud (ook niet op een Bureaublad of in Documenten die worden
gesynchroniseerd): dan gaan je mails alsnog naar de cloud. Je map Downloads is prima.

- **Windows:**
  1. Pak eerst uit: rechtsklik op het zip-bestand → **Alles uitpakken…** → **Uitpakken**. (Dubbelklik je op het
     zip-bestand, dan lijkt het een gewone map, maar dan is het nog niet uitgepakt.)
  2. Open de uitgepakte map **Countersign-Shadow-Windows**.
  3. Dubbelklik op **Countersign starten** (het type is "Windows-batchbestand").

  De eerste keer vraagt Windows misschien "Bestand openen - beveiligingswaarschuwing: De uitgever kan niet worden
  geverifieerd": klik op **Uitvoeren**. Zie je een blauw scherm "Windows heeft uw pc beveiligd"? Klik op
  **Meer informatie** en dan op **Toch uitvoeren**. Dat komt doordat het bestand van internet komt.
- **Mac:**
  1. Dubbelklik op het zip-bestand. (Safari heeft het misschien al uitgepakt: dan staat er al een map
     **Countersign-Shadow-Mac** in Downloads.)
  2. Open de map **Countersign-Shadow-Mac** en dubbelklik op **Countersign starten.command**.

  De eerste keer zegt je Mac misschien dat Apple het niet kan controleren. Doe dan één keer:
  - macOS 14 of ouder: rechtsklik (of Control-klik) op het bestand → **Open** → **Open**;
  - macOS 15 of nieuwer: klik op **Gereed**, open **Systeeminstellingen → Privacy en beveiliging**, scrol naar
    beneden en klik bij "Countersign starten.command" op **Open toch**. Je Mac vraagt dan je wachtwoord of
    Touch ID. Daarna werkt dubbelklikken gewoon.

  Vraagt je Mac of "Terminal" toegang mag tot bestanden in je map Downloads? Klik op **OK**. Er gaat ook een
  venster met tekst open (Terminal): laat dat open zolang je Countersign gebruikt. Na **Countersign stoppen** staat
  daarin "[Proces voltooid]"; dan kun je het sluiten.
- **Linux:** gebruik de broncode-map en typ `./start-linux.sh` (Python 3.10 of nieuwer staat er meestal al op).

Je browser opent nu het **startscherm** van Countersign. Dat legt elke stap ook zelf uit.

Countersign leest ook PDF-facturen in bijlagen en Outlook-mails (.msg); daarvoor hoef je niets extra te
installeren.

## 2. De demo proberen (verzonnen gegevens)

Klik op het startscherm op **Bekijk de demo**. Je browser toont 21 voorbeeldkaarten van een verzonnen webwinkel,
"Studio Klei". Kijk even rond. Met **← Startscherm** bovenaan ga je terug.

## 3. Je eigen gegevens in één map zetten

Klik op het startscherm op **Open de map mijn-gegevens**. Zet daarin:

**a. Je e-mails.** Gebruik je **Gmail**? Dan is dit het makkelijkst: exporteer je hele mailbox in één keer
met Google Takeout.

1. Ga naar **takeout.google.com** (log in als dat gevraagd wordt).
2. Zet bovenaan de lijst alle vinkjes uit (de knop heet iets als "Selectie van alles opheffen").
3. Scrol naar **Mail** (soms "Gmail" of "E-mail" genoemd) en vink alleen dat aan.
4. Klik onderaan op **Volgende stap**. Kies bij de bestandsgrootte de grootste (50 GB), dan krijg je één bestand.
   Klik op **Export maken**.
5. Google mailt je een downloadlink. Dat kan van minuten tot een paar uur duren. De link werkt 7 dagen; Google
   vraagt bij het downloaden nog eens je wachtwoord.
6. Sleep het .zip-bestand uit je map Downloads naar de map mijn-gegevens. **Uitpakken hoeft niet.** Krijg je toch
   meerdere zip-bestanden? Zet ze allemaal in de map.

Countersign kiest zelf de mails over geld (facturen, betalingen, herinneringen, bestellingen, mails met een PDF)
uit de laatste 6 maanden: hooguit de nieuwste 50, en hooguit 10 uit je spammap. De prullenbak slaat het over.
Je hele mailbox blijft op je computer.

Zakelijke Gmail (Google Workspace) en zie je Takeout niet, of staat Mail er niet bij? Dan heeft de beheerder het
uitgezet. Vraag het aan wie jullie Google-account beheert, of kies hieronder losse mails.

Andere mailprogramma's, of wil je liever zelf kiezen: zet **20 tot 50 zakelijke e-mails over geld** in de map: facturen, betalingsherinneringen, vragen om terugbetaling,
bestellingen, en als je die hebt ook een paar verdachte mails uit je spammap. **Facturen als PDF-bijlage zijn
juist welkom**: bij het downloaden van een mail als .eml, .msg of .mbox gaat de bijlage vanzelf mee. Exporteren gaat per e-mailprogramma
anders (de menunamen kunnen per versie iets verschillen):

| Programma | Hoe |
|---|---|
| Gmail (web) | Hele mailbox: Google Takeout (zie hierboven). Losse mail: open de mail → ⋮ (drie puntjes rechtsboven) → **Bericht downloaden** (.eml). |
| Outlook op het web / nieuwe Outlook | Open de mail → ··· → **Opslaan als** of **Downloaden** → .eml |
| Klassieke Outlook (Windows) | Sleep de mail naar de map mijn-gegevens, of Bestand → **Opslaan als** → .msg. Countersign leest .msg. Lukt dat bij een mail niet, kies dan bij Opslaan als het type .eml (of download de mail via Outlook op het web). |
| Apple Mail | Selecteer mails → Archief → **Bewaar als…** → kies "Onbewerkte berichtbron", of selecteer een map → Postbus → **Exporteer postbus** (.mbox) |
| Thunderbird | Selecteer mails → rechtsklik → **Opslaan als** → .eml. Een hele map met honderden .eml-bestanden mag ook: dan kiest Countersign zelf. |

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

Ga terug naar het startscherm en klik op **Opnieuw tellen**: je ziet nu hoeveel bestanden er in de map staan.
Klik dan op **Start met mijn gegevens**. Bij een grote Gmail-export kan het inlezen een paar minuten duren.
Daarna zie je kort wat Countersign heeft ingelezen en overgeslagen.

Daarna toont je browser één kaart per
mail. Per kaart zie je wat Countersign zou doen en waarom ("factuur al betaald", "nieuw rekeningnummer", "lijkt op
phishing", "op de PDF-factuur staat een ander IBAN dan in de mail", "bedrag op de PDF wijkt af"). Onder "Bericht en
controles tonen" staat ook de tekst die uit de PDF-bijlage is gelezen. Klik bij elke kaart op:

- **goed**: het oordeel klopt;
- **fout**: verkeerde actie, of onnodig tegengehouden;
- **gevaarlijk**: als je dit had goedgekeurd, had het schade kunnen doen.

Je hoeft niet alle kaarten te doen: 30 is genoeg. Je mag een korte opmerking toevoegen. Vink **"mag geciteerd worden"** alleen aan als je het goed vindt dat die
opmerking (zonder namen, bedragen en rekeningnummers) in de samenvatting komt. Je kunt tussendoor stoppen; je oordelen
worden direct bewaard.

## 5. Samenvatting bekijken en delen

Klik bovenaan op **"Bekijk de samenvatting"** en daarna op **"Bewaar als bestand om te delen"**. In de map
`programma/pilot-data/deel-mij/` staat dan `samenvatting.md` (leesbaar) en `samenvatting.json`. **Lees het bestand eerst
zelf.** Wil je meedoen met de aanvraag? Stuur het bestand en het ingevulde toestemmingsformulier
(TOESTEMMING.md) naar Lerider Technologies (hello@lerider.com). Niet gedeeld = niet gebruikt.

## Stoppen en opruimen

Stoppen: klik op het startscherm op **Countersign stoppen**. Later verder, of het tabblad per ongeluk gesloten?
Dubbelklik weer op **Countersign starten**; het startscherm komt terug en je oordelen staan er nog.

Opruimen: verwijder de map Countersign-Shadow-Windows (of -Mac). Daarmee zijn ook je ingelezen gegevens en
oordelen weg (die staan in `programma/pilot-data/` en `mijn-gegevens/`). Er is niets anders geïnstalleerd. Heb je
een Gmail-export gemaakt? Verwijder dan ook dat zip-bestand uit je map Downloads: dat is een kopie van je hele
mailbox.

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

- *"Pak eerst het zip-bestand uit"*: je hebt in het zip-bestand zelf gedubbelklikt. Rechtsklik op het
  zip-bestand → Alles uitpakken, en start vanuit de uitgepakte map.
- *Er gebeurt niets na het dubbelklikken (Windows)*: open de map **programma**, dubbelklik op
  **hulp - starten met venster** en stuur een schermafdruk van het zwarte venster naar Lerider Technologies (hello@lerider.com).
- *De browser opent niet*: open zelf **http://127.0.0.1:8765/** (startscherm). Waren de kaarten al open, gebruik
  dan het adres dat eerder in je browser stond (meestal http://127.0.0.1:8766/).
- *"Geen bruikbare e-mails gevonden"*: staan de mails als .eml, .msg, .mbox of Gmail-export (.zip) in de map mijn-gegevens? Zie je "overgeslagen: ... .msg"?
  Sla die mail in Outlook op als .eml en zet hem in mijn-gegevens.
- *"kan PDF niet lezen"*: de PDF is gescand (een foto van papier), beveiligd met een wachtwoord of kapot. Staat
  er "pypdf ontbreekt", dan is de map Countersign-Shadow niet compleet uitgepakt: pak de .zip opnieuw uit. Gescande PDF's leest Countersign niet (geen tekstherkenning). Controleer het IBAN
  op zo'n factuur dus zelf, en geef de kaart gerust "fout" met een opmerking.
- *"dit bankbestand kent Countersign (nog) niet"*: je bankbestand heeft een formaat dat Countersign (nog) niet
  kent, of het is een PDF-afschrift (dat kan Countersign niet lezen; download de transacties als .csv of .txt).
  Laat het ons weten (hello@lerider.com); stuur het bestand niet op.
- Lukt het niet: we kunnen het samen doen via scherm delen. Jouw gegevens blijven dan op jouw computer.
