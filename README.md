# Journl

![Journl Logo](Screenshots/neues_Logo_light_dark.png)

Eine selbst gehostete Web-App für ein digitales Journal: Markdown-Dateien als
Datenbestand, mehrere Eingabe-Templates, Familienplanung, lokale Volltextsuche
und optionale KI — mit lokalen Modellen statt Cloud-Zwang.

**Markdown ist die maßgebliche Datenquelle.** Alle Indizes, Hashtag-Kataloge und
Suchbeschleuniger sind jederzeit aus den Dateien neu aufbaubar. Wer die App
abschaltet, behält lesbare Textdateien in einer Ordnerstruktur nach Datum.

Kurze Demo: https://youtu.be/iT8kh9ieWuw

| Weiterführend | Inhalt |
|---|---|
| [docs/DESIGN.md](docs/DESIGN.md) | Farbschema und Komponentenspezifikation des Dark-Only-Designs |
| [docs/features/share-api.md](docs/features/share-api.md) | Share-API: HTTP-Vertrag, Rechte, Dateiformate, iPhone-Kurzbefehle |
| [docs/features/mcp-api.md](docs/features/mcp-api.md) | MCP-Server für Journale und Familiendokumente |
| [docs/features/brain-chat.md](docs/features/brain-chat.md) | Chat mit den eigenen Notizen |
| [docs/features/screenshot-upload-api.md](docs/features/screenshot-upload-api.md) | Screenshot-Eingang |

## Funktionen

### Schreiben und Lesen

- **Tägliche Journaleinträge** — Ablage unter `<user-id>/Jahr/Monat/Tag/Journal_YYYY-MM-DD.md`, Einträge durch `___` getrennt
- **Mehrere Templates** — Schnell, Aufgabe, Nebengedanke, Thema, Brainablage, Idee, Erkenntnis sowie eigene Formulare
- **Schreibsessions & Medien** — Geräteübergreifende Entwürfe, private Fotos und chunkweise hochgeladene Sprachnachrichten
- **Ansicht beim Start** — Schreibfeld, Lesefassung oder (nur Desktop) beides nebeneinander; die Wahl hängt am Gerät, nicht am Konto
- **Versionshistorie** — Frühere Fassungen eines Tages ansehen und wiederherstellen, inklusive der zugehörigen Medien
- **Dashboard „Heute"** — Alle Einträge des Tages mit Inline-Bearbeitung und Löschfunktion
- **Bildschirmschoner / Fokus-Kulisse** — Vollflächige Diashow aus dem Burger-Menü, wahlweise mit Schreibfeld über den Bildern. Die Bilder holt und cacht der Server (Wikimedia-Themen, eigene Ordner unter `DATA_DIR/screensaver/`, eigene Journalbilder), damit die CSP dicht bleibt und die Diashow offline weiterläuft

### Suche und Struktur

- **Brain** — Lokale Volltextsuche, Timeline, Aufgaben- und Tag-Explorer über die erlaubten Markdown-Quellen, mit Editor im selben Tab
- **Hashtags** — `#stichwort` mitten im Text, mit Katalog, Alias, Ersetzen und optionaler KI-Verschlagwortung (siehe unten)
- **Sammelmappen** — Themenmappen und Tagesablage über das Burger-Menü; das Einsammeln übernimmt `scripts/bucket-scheduler.py`
- **Lokales Whisper** — Optionaler CPU-Dienst mit API-Key und Zeitmarken; der Zeitplan ist in den Einstellungen umstellbar (stündlich, täglich zu einer Uhrzeit, nur manuell)

### Familie

- **Familien-Planung** — Einmalige und wiederkehrende Aufgaben mit Verantwortlichkeit, Startdatum, Vorschau, Pausieren und Bearbeiten
- **Board mit Buckets** — Aufgaben, Projekte, Notizen und Einkaufslisten als ein Dateityp, unterschieden durch `type` im Frontmatter
- **Daily Scheduler** — Idempotente Aufgabenerzeugung als Compose-Service, zusätzlich geprüft beim Öffnen des Familienbereichs

### KI (optional, bevorzugt lokal)

- **KI-Anbieter in der Oberfläche** — Endpunkte, Modelle und Standard-Agent unter Burger → AI-Worker → Anbieter. API-Schlüssel verlassen den Server nie; nach außen geht nur, *ob* ein Schlüssel hinterlegt ist
- **Schreib-Agent** — Arbeitet auf dem Text im Schreibfeld, erreichbar aus Schreiben, Heute und Brain. Mehrere Wissens-Hashtags, Schreibziele aus der `config.json`, Wahl des Agenten. **Er speichert nichts**: das Ergebnis kommt als Vorschlag und landet erst mit „Übernehmen" im Feld
- **Einkaufslisten-Agent** — Sortieren, doppelte Artikel zusammenfassen, ergänzen, umbenennen, abhaken. Sortiert nach dem eigenen Ladenaufbau, sobald eine Notiz hinter einem Knowledge-Hashtag liegt. Legt vor dem ersten Schreibzugriff einen Wiederherstellungspunkt an
- **Chat with your Notes** — Chatansicht im Brain-Tab, in der ein lokales Modell die eigenen Journale über dieselben Werkzeuge wie `/mcp/privat` beantwortet. Jeder Turn landet als `Chat_<datum>_<zeit>.md` im Tagesordner und ist über die Brain-Suche auffindbar
- **KI-Verschlagwortung** — Ein lokales Modell trägt Hashtags für alte Journale nach, ohne Blocktext zu verändern
- **Agent-Projekte („An Agent senden")** — Ein Projekt verbindet einen freigegebenen Linux-Ordner, den Modus Lesen/Schreiben/Ausführen, eine lokale Verbindung (LM Studio, Ollama, llama.cpp oder OpenAI-kompatibel) und optional Journal und gewählte Dateien als Kontext. `opencode run` oder Pi arbeiten über den Host-Worker
- **Share-API & AI-Workflows** — Text, URLs, Audio, Bilder und Dokumente direkt ablegen oder per persönlichem Workflow als Journaleintrag verarbeiten
- **Lange Läufe überleben ein gesperrtes Handy** — Verschlagwortung und Agentenaufträge laufen serverseitig; ihr Stand ist über `/api/runs*` jederzeit wieder abrufbar

### Plattform

- **Authentifizierung** — HTTP-only Session mit CSRF-Schutz und Login-Rate-Limiting
- **Responsive** — Getrennte Oberflächen für Desktop (mausoptimiert) und Mobil (touch-optimiert)
- **Dark Mode Only** — Durchgängiges „Midnight"-Design mit grünem Akzent

## Schnellstart

### Voraussetzungen

- Docker und Docker Compose
- Ein Verzeichnis für die Daten auf dem Host
- Optional: ein OpenAI-kompatibler LLM-Endpunkt im eigenen Netz (LM Studio, Ollama, llama.cpp)

### Starten

```bash
cp .env.example .env
$EDITOR .env          # mindestens SECRET_KEY und JOURNAL_DATA_DIR setzen
docker compose up -d --build
```

Die App läuft danach auf `http://localhost:4097` (Port über `PORT` anpassbar).

`docker-compose.yml` erwartet die Umgebungsdatei standardmäßig im Datenverzeichnis
des Betreibers. Für eine eigene Installation zeigt `JOURNL_ENV_FILE` auf die
eigene `.env`:

```bash
JOURNL_ENV_FILE=./.env JOURNAL_DATA_DIR=./data docker compose up -d --build
```

`config.json` muss vor dem ersten Start im Datenverzeichnis liegen — sie wird
bewusst nie automatisch erzeugt oder überschrieben, damit ein Deployment eine
bestehende Konfiguration nicht zerstören kann.

### Whisper aktivieren (optional)

Der Whisper-Dienst wird einmalig mit einem Initialisierungsskript aktiviert. Es
erzeugt den API-Key, startet API und Scheduler, prüft den Dienst und lädt das
Modell vor:

```bash
sudo ./scripts/init-whisper.sh dev     # oder: prod
```

Die OpenAI-ähnliche Transkriptions-API liegt auf Port 8091 (DEV) beziehungsweise
8090 (PROD) und akzeptiert `POST /v1/audio/transcriptions` nur mit
`Authorization: Bearer <key>`. Das maschinenlesbare Schema steht unter
`GET /openapi.json`. Betrieb, API und Fehlerbilder stehen in
[scripts/README_whisper.md](scripts/README_whisper.md).

### Ohne Docker entwickeln

```bash
pip install -r requirements.txt
cd app && python main.py       # http://localhost:4098
```

Die Testsuite läuft ausschließlich im DEV-Image — auf dem blanken Host fehlen
Flask, Pillow und psycopg, und übersprungene Tests melden trotzdem `OK`:

```bash
scripts/run-tests.sh                     # alles
scripts/run-tests.sh tests.test_brain_api # gezielt
scripts/run-tests.sh --db                # zusätzlich die PostgreSQL-Integrationstests
```

## Konfiguration

### Umgebungsvariablen (.env)

| Variable | Standard | Beschreibung |
|----------|----------|--------------|
| `SECRET_KEY` | — | Starker kryptographischer Key (erforderlich für Production) |
| `AUTH_USER` | `admin` | Optionaler Benutzername für die einmalige Account-Migration |
| `AUTH_PASS` | — | Optionales Passwort für die einmalige Account-Migration |
| `PORT` | `4097` | Externer Port des Docker-Containers |
| `TZ` | `Europe/Berlin` | Zeitzone für Server-Uhr und Journaleinträge |
| `JOURNL_ENV_FILE` | Datenverzeichnis des Betreibers | Pfad zur `.env`, die Docker Compose in die Container lädt |
| `JOURNAL_DATA_DIR` | `/docker-storage/my_Journal_data/data/journals/` | Host-Verzeichnis, das Docker Compose nach `/app/data` mountet |
| `DATA_DIR` | `/app/data` | Gemeinsames Datenverzeichnis für Journale, Familienaufgaben und Planner |
| `SCHEDULER_HOUR` | `6` | Lokale Ausführungsstunde des Scheduler-Services |
| `WHISPER_API_KEY` | — | Erforderlicher API-Key für den privaten Whisper-Dienst |
| `WHISPER_MODEL` | `base` | Mehrsprachiges lokales Whisper-Modell |
| `WHISPER_COMPUTE_TYPE` | `int8` | CPU-schonender Berechnungstyp |
| `WHISPER_CPU_THREADS` | `4` | CPU-Threads des Whisper-Dienstes |
| `WHISPER_PORT` | `8090` | Heimnetz-Port der geschützten Whisper-API |
| `WHISPER_SCHEDULE_HOUR` | `11` | Lokale Stunde für den täglichen Transkriptionslauf |
| `WHISPER_LANGUAGE` | `de` | Standardsprache für automatische Transkriptionen |
| `locale_LLM_IP` | — | IP des lokalen LLM (z. B. LM Studio). Ersetzt `{locale_LLM_IP}`-Platzhalter in `config.json` |
| `BRAIN_INDEX_INTERVAL_SECONDS` | `3600` | Brain-Indexintervall in Sekunden (mindestens 300) |
| `BRAIN_ARCHIVE_DIR` | `DATA_DIR/_Archiv/Projekte` | Optionaler read-only Pfad für externe Archivprojekte |

### KI-Anbieter

Anbieter werden in der Oberfläche unter **Burger → AI-Worker → Anbieter**
gepflegt. Geschrieben wird dabei ausschließlich die rohe `config.json`, nie das
Ergebnis mit eingesetzten Umgebungsvariablen — sonst wäre `{locale_LLM_IP}` beim
ersten Speichern durch eine feste IP ersetzt.

Ein Feld, das über die Umgebung gesetzt ist, bleibt in der Oberfläche gesperrt
und wird als solches benannt, weil die Umgebung beim Start ohnehin gewinnt:

```bash
AI_PROVIDER_0_API_URL=http://192.168.1.50:1234/v1/chat/completions
AI_KEY_LM_STUDIO=…                     # Schlüssel je Anbieter-ID
```

Ältere Installationen konfigurieren stattdessen `ai_templates`:

```bash
AI_TEMPLATE_0_API_URL=http://localhost:1234/v1/chat/completions
AI_TEMPLATE_0_MODEL=qwen3.6-35b-a3b
AI_TEMPLATE_0_MAX_TOKENS=5000
AI_TEMPLATE_0_TEMPERATURE=0.7
AI_TEMPLATE_0_SYSTEM_PROMPT="Du bist ein hilfreicher Assistent."
```

### config.json

Die Datei liegt persistent im Datenverzeichnis neben `users.json` (im Container
`/app/data/config.json`) und enthält Template-Definitionen sowie die
KI-Konfiguration:

- **templates** — Formular-Templates (einfache Textfelder oder strukturierte Formulare)
- **ai_templates** / **ai_providers** — KI-Endpunkte mit Modell und Prompt
- **ai_functions** — Schreibziele, die der Schreib-Agent anbietet

## Daten-Speicherung

Journaleinträge werden im Volume-Pfad gespeichert (standardmäßig `/app/data` im Container):

```
/app/data/
  users.json
  <user-id>/
    2026/06/06/Journal_2026-06-06.md
    notes/Pflanzen.md
    projects/Projekt.md
    write_sessions/<session-id>/session.json
    write_sessions/<session-id>/media/<media-id>/original.m4a
    .share-jobs/<share-id>/metadata.json
    2026/06/06/media/Sprachi/<zeit>_<share-id>_audio.m4a
```

Einträge sind durch `___` getrennt. Backups werden als `.md.bak` im jeweiligen Tages-Unterordner `_Backup/` erstellt.

Brain erzeugt daneben rebuildbare Dokument-Indizes unter
`<user-id>/indexes/brain_index/` sowie `family/indexes/brain_index/`. Manuelle
Brain-Tags, Prioritäten und Projektzuordnungen liegen ausschliesslich in
`<user-id>/brain_metadata.json` und bleiben bei einem Rebuild erhalten.
`<user-id>` ist dabei die ID aus `users.json`, nicht der sichtbare Benutzername.
Ein Benutzer kann dort mit `"admin": true` als Administrator markiert werden;
ohne Feld oder mit `false` besitzt er keine gemeinsamen Verwaltungsrechte.

## Hashtags

Ein Hashtag ist einfach `#stichwort` mitten im Text — im Journal, in Notizen,
Projekten und in Familiendokumenten. Journl liest sie beim Speichern aus, hängt
je Journaldatei einen technischen Index-Footer an (HTML-Kommentar, in der
Ansicht unsichtbar) und pflegt daraus `indexes/hashtag_index.json`. Der Footer
und der Index sind reine Beschleuniger: Maßgeblich bleibt der Markdown-Text,
beides ist jederzeit neu aufbaubar.

Groß-/Kleinschreibung und Umlaute werden normalisiert (`#Idee`, `#idee` und
`#IDEE` sind derselbe Tag), Leerzeichen werden zu `-`.

### Katalog: Vorschlag, freigegeben, blockiert

Jeder neue Hashtag landet zunächst als **Vorschlag** im Katalog. Unter
**Brain → Hashtag-Katalog** wird er freigegeben, mit einem Alias verbunden oder
blockiert.

| Zustand | Bedeutung |
|---|---|
| Vorschlag | automatisch beim ersten Auftauchen; **sofort such- und filterbar** |
| Freigegeben | bewusst ins Vokabular übernommen — steuert Einfärbung und Katalogpflege |
| Blockiert | bleibt aus Index, Suche und Vorschlagsliste heraus |

Eine Freigabe ist also kein Schalter für die Auffindbarkeit, sondern nur für die
Kuratierung: Ein frisch getippter Tag ist genau dann findbar, wenn man noch
überlegt, ob man ihn behalten will.

### Alias statt Umbenennen

Ändert sich der Sprachgebrauch, gibt es zwei Wege:

- **Alias** (`alt → freigegebenes Ziel`): Der alte Tag bleibt in allen Dateien
  stehen, Index, Suche und MCP zeigen aber nur noch das Ziel. Reversibel, keine
  einzige Datei wird angefasst. Wirkt ab dem nächsten Index-Rebuild.
- **Ersetzen** (**Brain → Hashtag ersetzen**): schreibt den Tag in Text *und*
  Footer der ausgewählten Dokumente wirklich um — mit Vorschau je Dokument.
  Gedacht für Tippfehler und echte Fehlvergaben.

Faustregel: Alias, wenn sich die Bedeutung nur verschoben hat; Ersetzen, wenn
der alte Name schlicht falsch war.

### Zwei Kataloge

Persönliche Journale und Familiendokumente führen getrennte Kataloge
(`<user-id>/hashtag_catalog.json` bzw. `family/hashtag_catalog.json`). Ein
Familien-Hashtag darf in einem persönlichen Journal vorkommen; umgekehrt bleiben
persönliche Tags aus dem Familienbereich heraus. Die beiden MCP-Server folgen
derselben Grenze (siehe [`docs/features/mcp-api.md`](docs/features/mcp-api.md)).

### KI-Verschlagwortung und Herkunft

**Brain → Hashtags nachtragen** lässt eine **lokale** LM-Studio-Instanz alte
Journale verschlagworten. Wichtig dabei:

- Die KI **verändert keinen Blocktext**. Ihre Zuordnung landet im technischen
  Footer unter `ai`, neben den Hashtags aus dem Text. Genau deshalb kann ein
  späterer Lauf seine eigenen Vorschläge ersetzen, ohne je einen selbst
  getippten Tag anzufassen — und deshalb braucht es kein Sonderpräfix wie
  `#_idee`: der Name bleibt derselbe, nur die Herkunft ist vermerkt.
- **Analysiert wird ein ganzer Tag auf einmal**, nicht jeder Eintrag einzeln:
  Alle Blöcke eines Journals gehen in einem Aufruf an das Modell, das für jeden
  Zeitstempel antworten muss. Passt die Antwort nicht exakt auf alle Blöcke des
  Tages, wird die Datei **gar nicht** geschrieben und als Fehler gemeldet — es
  gibt keine halb getaggten Tage.
- Der Lauf verarbeitet **ein Journal pro Anfrage** und ist jederzeit abbrechbar.
  Jede fertige Datei wird mit `ai_run` gestempelt, der nächste Start macht dort
  weiter. Rechenleistung, die nur zeitweise da ist, reicht also aus.
- **Reverse-Proxy-Timeout beachten:** Ein Tag kann das Modell mehrere Minuten
  beschäftigen (App-seitig sind 300 s erlaubt). Steht davor ein Proxy mit dem
  üblichen 60-s-Lesetimeout, bekommt der Browser einen 504, obwohl der Server
  weiterrechnet und die Datei fertig schreibt. In nginx entsprechend
  `proxy_read_timeout 300s;` setzen (Nginx Proxy Manager: *Advanced*). Passiert
  es doch, meldet die Oberfläche es als Zeitüberschreitung — einfach erneut
  starten, der Lauf setzt beim nächsten offenen Tag fort.
- Während des Laufs zeigt ein Protokoll je Tag, welcher Eintrag welche Hashtags
  bekommen hat, welche schon im Text standen und welche Tage fehlgeschlagen
  sind. **Nur ansehen** lässt das Modell laufen, ohne etwas zu schreiben.
- Drei Modi: *Nur noch nicht getaggte* (Wiederaufnahme), *Alles neu bewerten*
  und *Neu bewerten, wenn getaggt vor <Datum>* — letzteres, um alte Einträge auf
  ein inzwischen gewachsenes Vokabular zu heben.
- Das Modell bekommt die **freigegebenen** Tags als Vorgabe und darf neue
  vorschlagen; die landen als Vorschlag im Katalog.

Im **Brain-Tab** erscheinen diese Hashtags an der Karte wie alle anderen — nur
violett und mit einem `*` markiert, weil sie eben nicht im Text stehen
(„Von der KI vergeben"). Sie zählen auch für Tag-Filter und Trefferzahlen.
Sichtbar werden sie nach dem nächsten Index-Rebuild, den ein Tagging-Lauf selbst
anstößt.

Die Herkunft überlebt spätere Bearbeitungen des Tages und auch das Umbenennen
eines Hashtags. Über die MCP-Schnittstelle ist sie als `ai_tags` je Block
sichtbar.

### Besondere Präfixe

Hashtags, die mit `ai-` beginnen, können als AI-Workflow, Wissensquelle oder
Schreibziel konfiguriert werden — siehe
[`docs/features/share-api.md`](docs/features/share-api.md).

## Automatisierung

- [Family Scheduler](scripts/README_scheduler.md) — interner Compose-Service für wiederkehrende Familienaufgaben
- [Local Whisper](scripts/README_whisper.md) — privater CPU-Dienst, Initialisierung, API und täglicher Transkriptionslauf
- [External Journal Monitor](scripts/README_journal_monitor.md) — hostseitiger Cron-Job für explizite Agentenaufträge; nicht Bestandteil des Containers
- [Host-Worker auf einem zweiten Rechner](scripts/README_host_pi_worker.md) — führt Agentenaufträge außerhalb des Containers aus

## API-Überblick

Authentifizierung läuft über eine HTTP-only Session; schreibende Endpunkte
verlangen zusätzlich den Header `X-CSRF-Token`. Die Tabelle nennt die Gruppen
und je einen typischen Endpunkt — die vollständigen Verträge der öffentlichen
Schnittstellen stehen in [docs/features/](docs/features/).

| Gruppe | Beispiel | Zweck | Auth |
|---|---|---|---|
| Basis | `GET /api/health` | Health Check und Auth-Status | — |
| Anmeldung | `GET,POST /login`, `POST /logout` | Session beginnen und beenden | teilweise Session + CSRF |
| Konfiguration | `GET /api/config` | Template-Konfiguration laden | Session |
| Schreiben | `POST /api/submit` | Eintrag speichern | Session + CSRF |
| Heute | `GET /api/dashboard`, `POST /api/dashboard/edit` | Einträge des Tages lesen und ändern | Session (+ CSRF beim Schreiben) |
| Historie | `GET /api/journals/<datum>/history` | Frühere Fassungen ansehen und wiederherstellen | Session (+ CSRF beim Schreiben) |
| Brain | `GET /api/brain/search`, `POST /api/brain/index/rebuild` | Volltextsuche, Aufgaben, Tags, Index-Rebuild | Session (+ CSRF beim Schreiben) |
| Brain-Chat | `POST /api/brain/chat/ask` | Frage an die eigenen Journale | Session + CSRF |
| Familie | `GET /api/family/board`, `POST /api/family/project*` | Board, Buckets, Aufgaben, Einkaufslisten | Session (+ CSRF beim Schreiben) |
| Planner | `/api/family/planner` | Wiederkehrende Aufgaben; Format in [README_scheduler.md](scripts/README_scheduler.md) | Session (+ CSRF beim Schreiben) |
| Sammelmappen | `/api/buckets/*` | Themenmappen und Tagesablage | Session (+ CSRF beim Schreiben) |
| Schreib-Agent | `POST /api/write-agent/ask` | Vorschlag zum Text im Schreibfeld | Session + CSRF |
| Einkaufslisten-Agent | `POST /api/family/shopping-agent/<id>/ask` | Liste sortieren, zusammenfassen, ergänzen | Session + CSRF |
| Agent-Projekte | `POST /api/agent-projects/<id>/submit` | Projekt-Turn für `opencode run` oder Pi einreihen | Session + CSRF |
| Lange Läufe | `GET /api/runs/<id>` | Stand eines serverseitigen Laufs abfragen | Session |
| Schreibsessions | `/api/write-sessions*` | Entwürfe, Fotos und Sprachnachrichten | Session (+ CSRF beim Schreiben) |
| Bildschirmschoner | `GET /api/screensaver` | Bildquellen und Diashow | Session |
| Share | `POST /api/share` | Text, URLs, Medien ablegen; siehe [share-api.md](docs/features/share-api.md) | Bearer-Schlüssel |
| Screenshot-Eingang | `POST /api/screenshot-inbox` | siehe [screenshot-upload-api.md](docs/features/screenshot-upload-api.md) | Bearer-Schlüssel |
| MCP | `POST /mcp/privat`, `POST /mcp/family` | Lesende Werkzeuge auf Journale bzw. Familiendokumente | Bearer-Schlüssel mit `mcp` bzw. `mcp_family` |
| Konto | `GET /api/me/export`, `POST /api/me/delete` | Daten exportieren, Konto löschen | Session + CSRF |

## Sicherheit

- **Rate Limiting** — maximal 10 Login-Versuche pro IP innerhalb von 5 Minuten
- **CSRF** — schreibende Endpunkte verlangen `X-CSRF-Token`
- **CSP-Header** — Content Security Policy mit Tailwind-CDN-Allowlist
- **File Locking** — `fcntl.flock` für atomare Schreiboperationen
- **Non-root im Container** — die App läuft als `appuser`
- **Session-Cookies** — HTTP-only, `SameSite=Lax`, in Production `Secure`
- **Agent-Endpunkte nur im eigenen Netz** — Container und Host-Worker prüfen unabhängig voneinander, dass eine konfigurierte Verbindung auf eine Loopback- oder private Adresse auflöst. Damit wird eine benutzerverwaltete Verbindung kein SSRF-Kanal
- **API-Schlüssel bleiben auf dem Server** — nach außen geht nur, *ob* ein Schlüssel hinterlegt ist

Ein `Secure`-Cookie setzt TLS voraus: hinter einem Reverse Proxy muss dieser die
TLS-Terminierung übernehmen. Wer die App direkt über HTTP veröffentlicht, kann
sich in Production nicht zuverlässig anmelden.

## Design

Durchgängiges Dark-Only-Design mit Tailwind CSS. Farbschema und
Komponentenspezifikation stehen in [docs/DESIGN.md](docs/DESIGN.md).

## Lizenz

Apache License 2.0 — siehe `LICENSE` und `NOTICE`.
