# Journaling Tracker

Eine lokale Web-App zum Führen eines digitalen Journals mit Markdown-Support, mehreren Template-Varianten und optionaler KI-Integration.

Architektur, Datenfluesse, Invarianten, bekannte Risiken und der LLM-Onboarding-Guide stehen in [PROJECT_CONTEXT.md](PROJECT_CONTEXT.md).

## Funktionen

- **Tägliche Journaleinträge** — Automatische Speicherung in `data/Jahr/Monat/Tag/Journal_YYYY-MM-DD.md`
- **Mehrere Templates** — Schnell, Aufgabe, Nebengedanke, Thema, Brainablage, Idee, Erkenntnis + benutzerdefinierte Formulare
- **KI-Integration** — OpenAI-kompatible API für KI-generierte Antworten (konfigurierbar über `config.json` oder Umgebungsvariablen)
- **Dashboard** — Alle Einträge des Tages mit Inline-Bearbeitung und Löschfunktion
- **Authentifizierung** — HTTP-only Session mit CSRF-Schutz und Login-Rate-Limiting
- **Responsive Design** — Desktop (mausoptimiert) und Mobile (touch-optimiert)
- **Dark Mode Only** — Durchgängiges "Midnight"-Design mit grünem Akzent
- **Familien-Planung** — Einmalige und wiederkehrende Aufgaben mit Verantwortlichkeit, Startdatum, Vorschau, Pausieren und Bearbeiten
- **Daily Scheduler** — Idempotente Aufgabenerzeugung per Compose-Service und zusätzlicher Prüfung beim Öffnen des Familienbereichs
- **Brain View** — Lokale Volltextsuche, Timeline, Aufgaben- und Tag-Explorer über erlaubte Markdown-Quellen mit In-Tab-Editor

## Schnellstart

### Voraussetzungen

- Docker & Docker Compose
- `.env` Datei (siehe unten)

### Mit Docker Compose starten

```bash
# .env aus der Vorlage erstellen und anpassen
cp .env.example .env

# Container bauen und starten
docker compose up -d --build
```

Die App ist dann unter `http://localhost:4097` erreichbar (Port über `PORT` in `.env` anpassbar).

### Server-Deployment

Der Server verwendet zwei getrennte Git-Arbeitsverzeichnisse und Docker-Compose-Projekte:

| Umgebung | Verzeichnis | Branch | Befehl |
|----------|------------|--------|--------|
| DEV | `/docker-storage/Journal-Tracker-DEV` | `dev-environment` | `scripts/deploy.sh dev` |
| PROD | `/docker-storage/Journal-Tracker` | `prod` | `scripts/deploy.sh prod` |

`scripts/deploy.sh promote` aktualisiert zuerst DEV, verschiebt `prod` ausschließlich
per Fast-Forward auf denselben Commit und deployt anschließend PROD. Die Promotion wird
abgebrochen, wenn versionierte lokale Änderungen bestehen oder PROD kein Vorfahr von DEV ist.
Die persistenten Umgebungsdateien liegen unter
`/docker-storage/my_Journal_data_DEV/data/journals/.env` (DEV) und
`/docker-storage/my_Journal_data/data/journals/.env` (PROD).

Lokale Änderungen können gezielt commitet und auf den zugehörigen Branch gepusht werden:

```bash
scripts/deploy.sh git-update dev
scripts/deploy.sh git-update prod --message "Describe the change"
scripts/deploy.sh git-update both --message "Describe the change"
```

Ohne `--message` fragt das Skript für jeden Commit nach einer Nachricht. Mit `both` und
`--message` wird dieselbe Nachricht für beide einzelnen Commits verwendet.

### Public-GitHub aktualisieren

Die freigegebenen Applikationsdateien können aus DEV in das lokale Public-
Repository `/public-git/J-Tracker` synchronisiert und nach einer ausdrücklichen
Bestätigung nach GitHub gepusht werden:

```bash
scripts/deploy.sh public-update
scripts/deploy.sh public-update --message "Synchronize application changes"
```

Dabei werden nur die notwendigen Applikationsdateien, Tests und ausgewählte
Scripts sowie `README.md` veröffentlicht. Interne Markdown-Dokumente,
Feature-Request-Archive, Screenshots und private Host-Scripts werden
übersprungen. Die Git-Historie von DEV wird nicht übernommen; das Public-
Repository behält seine eigene `.git`-Historie. Vor Commit und Push zeigt das
Skript eine Änderungsübersicht und fragt interaktiv nach Bestätigung.

Details zur Whitelist und zum Ablauf stehen in
[`scripts/deploy-sh_Readme.md`](scripts/deploy-sh_Readme.md).

### Lokal entwickeln

```bash
pip install -r requirements.txt
cd app
python main.py
```

Server startet auf `http://localhost:4098`.

## Konfiguration

### Umgebungsvariablen (.env)

| Variable | Standard | Beschreibung |
|----------|----------|--------------|
| `SECRET_KEY` | — | Starker kryptographischer Key (erforderlich für Production) |
| `AUTH_USER` | `admin` | Optionaler Benutzername für die einmalige Account-Migration |
| `AUTH_PASS` | — | Optionales Passwort für die einmalige Account-Migration |
| `PORT` | `4097` | Externer Port des Docker-Containers |
| `TZ` | `Europe/Berlin` | Zeitzone für Server-Uhr und Journaleinträge |
| `JOURNAL_DATA_DIR` | `/docker-storage/my_Journal_data/data/journals/` | Host-Verzeichnis, das Docker Compose nach `/app/data` mountet |
| `DATA_DIR` | `/app/data` | Gemeinsames Datenverzeichnis für Journale, Familienaufgaben und Planner |
| `SCHEDULER_HOUR` | `6` | Lokale Ausführungsstunde des Scheduler-Services |
| `locale_LLM_IP` | — | IP des lokalen LLM (z. B. LM Studio). Ersetzt `{locale_LLM_IP}`-Platzhalter in `config.json` |
| `BRAIN_INDEX_INTERVAL_SECONDS` | `3600` | Brain-Indexintervall in Sekunden (mindestens 300) |
| `BRAIN_ARCHIVE_DIR` | `DATA_DIR/_Archiv/Projekte` | Optionaler read-only Pfad für externe Archivprojekte |

### AI-Templates über Umgebungsvariablen

```bash
AI_TEMPLATE_0_API_URL=http://localhost:1234/v1/chat/completions
AI_TEMPLATE_0_MODEL=qwen3.6-35b-a3b
AI_TEMPLATE_0_MAX_TOKENS=5000
AI_TEMPLATE_0_TEMPERATURE=0.7
AI_TEMPLATE_0_SYSTEM_PROMPT="Du bist ein hilfreicher Assistent."
```

### config.json

Die Datei `config.json` liegt persistent im Datenverzeichnis neben `users.json`
(im Container: `/app/data/config.json`) und enthält die Template-Definitionen
und AI-Konfiguration. Sie muss vor dem Start der Anwendung dort vorhanden sein.

- **templates** — Formular-Templates (simple textareas oder strukturierte Formulare)
- **ai_templates** — KI-API Konfigurationen mit Model, Prompt und Endpunkt

## Daten-Speicherung

Journaleinträge werden im Volume-Pfad gespeichert (standardmäßig `/app/data` im Container):

```
/app/data/
  users.json
  <user-id>/
    2026/06/06/Journal_2026-06-06.md
    notes/Pflanzen.md
    projects/Projekt.md
```

Einträge sind durch `___` getrennt. Backups werden als `.md.bak` im jeweiligen Tages-Unterordner `_Backup/` erstellt.

Brain erzeugt daneben rebuildbare Dokument-Indizes unter
`<user-id>/indexes/brain_index/` sowie `family/indexes/brain_index/`. Manuelle
Brain-Tags, Prioritäten und Projektzuordnungen liegen ausschliesslich in
`<user-id>/brain_metadata.json` und bleiben bei einem Rebuild erhalten.
`<user-id>` ist dabei die ID aus `users.json`, nicht der sichtbare Benutzername.
Ein Benutzer kann dort mit `"admin": true` als Administrator markiert werden;
ohne Feld oder mit `false` besitzt er keine gemeinsamen Verwaltungsrechte.

## Automatisierung

- [Family Scheduler](scripts/README_scheduler.md) - interner Compose-Service fuer wiederkehrende Familienaufgaben.
- [External Journal Monitor](scripts/README_journal_monitor.md) - hostseitiger Cron-Job fuer explizite Hermes- und OpenCode-Auftraege; nicht Bestandteil des Containers.

## API Endpunkte

| Methode | Pfad | Beschreibung | Auth |
|---------|------|--------------|------|
| `GET` | `/` | Hauptseite (Desktop/Mobile) | — |
| `GET,POST` | `/login` | Login mit Username/Passwort | — |
| `POST` | `/logout` | Session beenden | Session + CSRF |
| `GET` | `/api/time` | Serverzeit (Europe/Berlin) | Session |
| `GET` | `/api/health` | Health Check + Auth Status | — |
| `GET` | `/api/config` | Template-Konfiguration laden | Session |
| `POST` | `/api/submit` | Eintrag speichern | Session + CSRF |
| `POST` | `/api/ai-submit` | KI-Anfrage stellen & Antwort speichern | Session + CSRF |
| `POST` | `/api/dashboard/init` | Today's Journal-Datei erstellen | Session + CSRF |
| `GET` | `/api/dashboard` | Alle heutigen Einträge lesen | Session |
| `POST` | `/api/dashboard/edit` | Eintrag bearbeiten | Session + CSRF |
| `POST` | `/api/dashboard/delete` | Eintrag löschen | Session + CSRF |
| `GET` | `/api/brain/search` | Lokale Brain-Volltextsuche bzw. Timeline | Session |
| `GET` | `/api/brain/tasks` | Sichtbare Markdown-Aufgaben | Session |
| `GET` | `/api/brain/tags` | Sichtbare Tag-Anzahlen | Session |
| `POST` | `/api/brain/task/toggle` | Original-Markdown-Aufgabe umschalten | Session + CSRF |
| `POST` | `/api/brain/index/rebuild` | Asynchronen Index-Rebuild einplanen | Session + CSRF |

Authentifizierung erfolgt über eine HTTP-only Session. Schreibende Endpunkte
erfordern zusätzlich den Header `X-CSRF-Token`.

Die Planner-Endpunkte liegen unter `/api/family/planner`. Details zur
Ausführung und zum Markdown-Format stehen in der
[Scheduler-Dokumentation](scripts/README_scheduler.md).

## Sicherheit

- **Rate Limiting** — Maximal 10 Login-Versuche pro IP innerhalb von 5 Minuten
- **CSP Headers** — Content Security Policy mit Tailwind CDN Allowlist
- **File Locking** — `fcntl.flock` für atomare Schreiboperationen
- **Non-root Docker User** — App läuft als `appuser` im Container
- **Session-Cookies** — HTTP-only, `SameSite=Lax` und in Production `Secure`

## Design

Die App verwendet ein durchgängiges Dark-Only Design mit Tailwind CSS (CDN). Das vollständige Farbschema und die Komponentenspezifikation finden sich in `DESIGN.md`.

## kurze Demo
https://youtu.be/iT8kh9ieWuw

## Lizenz

Eigenprojekt.
