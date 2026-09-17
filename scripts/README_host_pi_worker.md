# Agent-Projekt-Worker auf dem Docker-Host

## Zweck

Pi und `opencode run` bleiben auf dem Docker-Host. Der Docker-Container startet
keinen Agenten selbst und erhält weder Docker-Socket noch Host-Shell-Zugriff.

```text
Schreiben-Tab: Klick auf KI Senden oder Strg/Cmd+Enter
        │
        ▼
Docker-App erstellt einen privaten Job im gemeinsamen Datenvolume
        │
        ▼
Host-Dienst journl-agent-worker-<umgebung> erkennt den Job
        │
        ▼
run-agent-session.sh startet Pi oder opencode und protokolliert die Übergabe
        │
        ▼
Antwort und Status werden ins Datenvolume geschrieben
        │
        ▼
Der Browser übernimmt die Antwort in das Textfeld – niemals automatisch ins Journal
```

Ein Job entsteht ausschließlich durch **KI Senden** oder **Strg/Cmd+Enter**. Normales
Tippen und Entwurf-Autospeichern schicken keine Daten an Pi.

> Wichtig: Der Dienst läuft unter dem angegebenen Linux-Benutzer. Pi und/oder
> opencode müssen unter genau diesem Benutzer verfügbar sein (`pi --version`,
> `opencode --version`). Pi benötigt für seine Verbindung eine konfigurierte
> `models.json`; opencode erhält die im UI gewählte Verbindung pro Auftrag.

## Umgebungen

| Umgebung | Dienst | Datenvolume | Worker-Code |
|---|---|---|---|
| DEV | `journl-agent-worker-dev` | `/docker-storage/my_Journal_data_DEV/data/journals` | `/opt/journl-agent-worker` |
| PROD | `journl-agent-worker-prod` | `/docker-storage/my_Journal_data/data/journals` | `/opt/journl-agent-worker-prod` |

DEV und PROD sind zwei unabhängige systemd-Instanzen mit getrennten Prozessen,
Jobdateien, Audit-Logs **und getrennten Installationsverzeichnissen**. Das
`install_dir` aus `host_worker.json` muss je Umgebung verschieden sein; der
Installer bricht ab, wenn ein Verzeichnis bereits von der Unit einer anderen
Umgebung benutzt wird.

Beide Umgebungen teilten sich das Verzeichnis früher. Der Effekt war unsichtbar
und unangenehm: ein Installer-Lauf für DEV tauschte den Code aus, den PROD
ausführt, und PROD übernahm ihn irgendwann beim nächsten Neustart — ohne Anlass
und ohne Spur. Der PROD-Worker wird stattdessen bei der Promotion mit
aktualisiert (`deploy.sh promote`), aus dem PROD-Arbeitsverzeichnis.

## Persistente Host-Konfiguration

Die Betriebsparameter liegen nicht im Repository. Pro Umgebung befindet sich
neben `users.json` eine Datei `host_worker.json`:

```text
/docker-storage/my_Journal_data_DEV/data/journals/host_worker.json
/docker-storage/my_Journal_data/data/journals/host_worker.json
```

Beim ersten `install` oder `reinstall` erzeugt der Installer eine Vorlage und
beendet sich ohne einen Dienst zu verändern. Danach die Datei mit `sudoedit`
ausfüllen, insbesondere `run_user` auf den Linux-Benutzer mit der Pi-Anmeldung
setzen. Die übrigen Werte steuern Installationsort, Poll-Intervall,
`job_timeout_seconds` (60 bis 3600 Sekunden), Pi-PATH und das Startverhalten
nach einer Installation. Die Datei ist persistent im
Datenvolume; nur Code und diese Dokumentation liegen im Git-Repository.

### Projektwurzeln

Agent-Projekte wählen ihren Linux-Ordner ausschließlich unter diesen Wurzeln.
`external_read_roots` erlaubt den Modus **Lesen**; `external_write_roots`
erlaubt zusätzlich **Schreiben** und **Ausführen**. Nur der zugehörige
Host-Worker darf diese Verzeichnisse erreichen; die Docker-App erhält keinen
Mount der Projektordner.

Beispiel: Der **PROD**-Worker darf gezielt im DEV-Checkout arbeiten, der
DEV-Worker erhält keine umgekehrte Freigabe:

```json
{
  "external_write_roots": [
    {
      "id": "journal-tracker-dev",
      "label": "Journal Tracker DEV",
      "path": "/docker-storage/Journal-Tracker-DEV"
    },
    {
      "id": "anderes-dev-projekt",
      "label": "Anderes DEV-Projekt",
      "path": "/docker-storage/Anderes-DEV-Projekt"
    }
  ]
}
```

Die DEV-Datei behält hierfür `[]`, wenn dort keine Projektarbeit erlaubt sein
soll. Im UI wird kein absoluter Pfad eingegeben: der Ordnerbrowser navigiert
nur innerhalb dieser Wurzeln. Container und Host-Worker prüfen die Wurzel,
Auflösung und Symlinks unabhängig voneinander. Der systemd-Installer ergänzt
alle `external_write_roots` automatisch als `ReadWritePaths`, damit der
gewählte Schreibmodus nicht an `ProtectSystem=strict` scheitert.

### Lokale Modell-Verbindungen

Eine Verbindung wird im Schreiben-Tab angelegt, nicht in `config.json`: LM
Studio, Ollama, llama.cpp und sonstige OpenAI-kompatible Server werden über
eine lokale Basis-URL verbunden. Die Modell-Liste wird bei der Auswahl live
gelesen (`/v1/models`, bei Ollama `/api/tags`). Erlaubt sind ausschließlich
Loopback- und private Netzadressen; öffentliche Adressen, Credentials in URLs
und Pfade außer optional `/v1` werden abgewiesen.

Für **opencode run** injiziert der Worker den Endpunkt pro Auftrag als
ephemeren Provider und schränkt Werkzeuge pro gewähltem Modus ein:

| Modus | Dateien | Shell |
|---|---|---|
| Lesen | lesen | nein |
| Schreiben | lesen, ändern, anlegen | nein |
| Ausführen | lesen, ändern, anlegen | ja |

Die opencode-Session-ID wird in der Journl-Agent-Session gespeichert. Ein
Folgeauftrag setzt dieselbe Session fort. Bei **Pi** gilt die gleiche
Modusmatrix über eine strikte `--tools`-Allowlist; der gewünschte lokale
Provider muss dort zusätzlich für den Dienstbenutzer konfiguriert sein.

### Externe Knowledge-Lesewurzeln

`external_read_roots` hat dasselbe Objektformat, erteilt aber ausschließlich
Lesezugriff. Im Brain kann einem Knowledge-Hashtag anschließend ein kompletter
Ordner oder Unterordner zugeordnet werden:

```json
{
  "external_read_roots": [
    {
      "id": "private-docs",
      "label": "Private Dokumentation",
      "path": "/srv/private-docs"
    }
  ]
}
```

Der Ordnerinhalt wird nicht in den Prompt kopiert. Der Worker prüft Root,
Unterordner und Symlinks auf dem Host. Pi erhält nur drei begrenzte Werkzeuge
zum rekursiven Auflisten, wörtlichen Suchen (auch nach Hashtags) und gezielten
Lesen unterstützter UTF-8-Textdateien. Built-in-Shell-, Schreib- und
Dateiwerkzeuge bleiben deaktiviert; Skills erweitern diese Pfadfreigabe nicht.

## Worker verwalten

Das versionierte Hilfsskript `host-worker.sh` bündelt die üblichen Host-Aktionen
und ruft den bestehenden Installer mit den richtigen Umgebungswerten auf:

```bash
cd /docker-storage/Journal-Tracker-DEV/scripts

# Gemeinsame Codebasis aktualisieren und beide unabhängigen Dienste neu starten
./host-worker.sh reinstall all

# Einen Dienst verwalten oder seine letzten Logzeilen lesen
./host-worker.sh restart prod
./host-worker.sh logs dev --lines 150
./host-worker.sh status all
```

Ohne Parameter öffnet es ein interaktives Menü für Aktion und Umgebung:

```bash
./host-worker.sh
```

`install` und `reinstall` sind absichtlich gleich idempotent. Mit `--no-start`
werden die ausgewählten Dienste nur installiert, danach deaktiviert und
angehalten. Die vollständige Befehlsübersicht liefert `./host-worker.sh --help`.

## DEV installieren und starten

```bash
cd /docker-storage/Journal-Tracker-DEV/scripts
./host-worker.sh install dev
```

Falls die Vorlage erzeugt wurde:

```bash
sudoedit /docker-storage/my_Journal_data_DEV/data/journals/host_worker.json
# run_user von CHANGE_ME auf den Pi-Host-Benutzer setzen
./host-worker.sh install dev
```

Danach prüfen:

```bash
systemctl status journl-agent-worker-dev
journalctl -u journl-agent-worker-dev -f
```

Der Status muss `active (running)` sein. Nach einer Änderung an den Scripts den
Installer für **beide** Umgebungen erneut ausführen; er aktualisiert die gemeinsame
Codebasis und startet nur den DEV-Dienst neu.

## PROD vorerst deaktiviert lassen

Der PROD-Dienst ist vorbereitet, soll derzeit aber **nicht laufen**. Sein
Sollzustand ist daher: installiert, `disabled` und `inactive`. So verarbeitet
er weder vorhandene noch neue PROD-KI-Jobs.

Falls der Dienst bereits existiert, anhalten und bei künftigen Serverstarts
deaktivieren:

```bash
sudo systemctl disable --now journl-agent-worker-prod.service
```

Prüfen:

```bash
systemctl is-enabled journl-agent-worker-prod.service
systemctl is-active journl-agent-worker-prod.service
```

Das erwartete Ergebnis ist `disabled` und `inactive`.

## PROD später aktivieren

Den bereits installierten PROD-Dienst direkt starten und für zukünftige
Serverstarts aktivieren:

```bash
sudo systemctl enable --now journl-agent-worker-prod.service
```

Status und Live-Logs:

```bash
systemctl status journl-agent-worker-prod
journalctl -u journl-agent-worker-prod -f
```

Falls der Dienst noch nicht installiert wurde, installiert dieser Befehl ihn
mit derselben Codebasis, lässt ihn aber deaktiviert:

```bash
cd /docker-storage/Journal-Tracker-DEV/scripts
./host-worker.sh install prod --no-start
```

## Pi-Workflow konfigurieren

Im Brain-Hashtag-Katalog für den gewünschten `#ai-…`-Workflow einstellen:

- **Agent:** `Pi`
- **Modell:** eine von `pi --list-models` ausgegebene Kennung, beispielsweise
  `openai/gpt-5.6-luna`
- **Zielbereich:** `Schreiben-Tab (temporär)`
- **Standard-Anbieter:** `Host-Worker · Pi`
- **Prompt:** die Arbeitsanweisung für den Agenten

Ein API-Key oder OpenAI-kompatibler Anbieter ist für diesen Workflow nicht nötig.

## Audit-Log und Jobdateien

Nach einem expliziten Senden liegen die privaten Dateien hier:

```text
<data-root>/<user-id>/ai_jobs/
├── <job-id>.json             Status, Modell, Prompt und exakter Kontext
├── <job-id>.request.md       unveränderlicher Benutzerauftrag dieses Turns
├── <job-id>.prompt.md        Workflow-Prompt
├── <job-id>.knowledge-*.md   unveränderliche Knowledge-Snapshots dieser Anfrage
├── <job-id>.knowledge.json   Tags, Typen und Zweck der Knowledge-Snapshots
├── <job-id>.write-target.json Snapshot eines ausdrücklich freigegebenen Schreibziels
├── <job-id>.references.json  ausgewählte externe Knowledge-Ordner
├── <job-id>.references.resolved.json hostseitig erneut geprüfte Ordner
├── <job-id>.context.md       Snapshot des heutigen Journals, falls gewählt
└── audit.jsonl               vollständiges Audit-Protokoll des Host-Workers
```

Die fortlaufende, nutzerlesbare Session liegt daneben unter
`<data-root>/<user-id>/ai-agent/Agent-Session_YYYY-MM-DD_hh-mm-ss.md`; Pi hält
zu derselben Session-ID zusätzlich seinen Gesprächskontext. Jeder Turn enthält
den serverseitig ermittelten `username` und die `user_id`. Admins sehen die
Anfragen aller Nutzer im Brain unter **Agent-Audit**, normale Nutzer nur ihre
eigenen Dateien unter **AI-Agent**.

`audit.jsonl` enthält bewusst den vollständigen Prompt und Kontext, der an Pi
übergeben wurde. Die Datei ist daher vertraulich und darf nicht außerhalb des
geschützten Datenvolumes geteilt werden.

Ein Knowledge-Hashtag wird erst bei **KI Senden** serverseitig aufgelöst. Der
Worker erhält bei einzelnen Markdown-Quellen ausschließlich die zu diesem Job
gehörenden Snapshot-Dateien; die ursprünglichen Notizen oder Projekte werden
weder verändert noch als Arbeitsanweisung behandelt. Externe Ordner sind nur
über die oben beschriebenen Lese-Werkzeuge erreichbar.

## KI-Schreibziele

Ein `#schreibziel-…` ist eine im Hashtag-Katalog hinterlegte Ordnerfreigabe
unter `notes/` oder `projects/` (einschließlich Unterordnern). Die Katalogoption
beschränkt sie entweder auf `.md` oder erlaubt alle regulären Dateien. Letzteres
umgeht trotzdem keine Sicherheitsgrenzen: Pi erhält nur kleine UTF-8-Textdateien
aus dem Snapshot, niemals Symlinks oder Binärdateien.

Der Worker gibt dafür nur einen JSON-**Vorschlag** zurück (`status: proposed`).
Pi hat keine Schreibwerkzeuge und schreibt keine Zieldatei. Erst die zweite,
explizite Browseraktion **⋯ → Vorschlag anwenden** schreibt nach erneuter
Katalog-, Pfad- und SHA-256-Revisionsprüfung. Vor jeder Änderung wird neben der
Datei ein `*.write-ai-backup-<UTC>` Backup angelegt.

## Abbrechen und Rückgängig

Das verdeckte Drei-Punkte-Menü neben **KI Senden** erscheint nur für einen
aktiven oder gerade abgebrochenen Host-Job:

- **Abbrechen:** Ein wartender Job wird nicht gestartet. Bei einem laufenden
  Pi-Prozess fordert die Anwendung den Worker zum kontrollierten Beenden genau
  dieses Prozessbaums auf.
- **Rückgängig:** Ein Abbruch kann nur zurück in die Warteschlange gestellt
  werden, solange Pi noch nicht gestartet wurde. Bereits laufende oder
  abgeschlossene Antworten werden nicht wiederholt oder verändert.

## Fehlerdiagnose

| Beobachtung | Ursache / nächster Schritt |
|---|---|
| UI: „wartet auf den Host-Worker“ | Dienst starten: `sudo systemctl restart journl-agent-worker-dev` und Live-Log prüfen. |
| Job bleibt auf `queued` | Worker läuft nicht oder verwendet den falschen `--data-root`. |
| Job ist `error` | `journalctl -u journl-agent-worker-dev -n 100 --no-pager` und `audit.jsonl` ansehen. |
| Kein `audit.jsonl` | Der Worker hat den Job noch nicht übernommen; Pi wurde nicht gestartet. |
| `pi: command not found` | Pi für den beim Installer angegebenen Host-Benutzer installieren oder dessen Pfad prüfen. |
| Antwort erscheint nicht | Jobdatei und den dortigen `source_path` unter `ai-agent/` prüfen; bei `completed` muss dort ein Agentenantwort-Block stehen. |

## Nützliche Befehle

```bash
# Dienst neu starten
sudo systemctl restart journl-agent-worker-dev

# Letzte Log-Zeilen ansehen
journalctl -u journl-agent-worker-dev -n 100 --no-pager

# Jobwarteschlange ansehen
find /docker-storage/my_Journal_data_DEV/data/journals -path '*/ai_jobs/*.json' -type f
```

## Deinstallation

```bash
sudo systemctl disable --now journl-agent-worker-dev.service
sudo systemctl disable --now journl-agent-worker-prod.service
sudo rm -f /etc/systemd/system/journl-agent-worker-dev.service
sudo rm -f /etc/systemd/system/journl-agent-worker-prod.service
sudo rm -rf /opt/journl-agent-worker /opt/journl-agent-worker-prod
sudo systemctl daemon-reload
```

Ein Installationsverzeichnis darf erst entfernt werden, wenn der zugehörige
Dienst beendet ist. Die privaten Jobs und Audit-Logs im Datenvolume werden dabei
nicht gelöscht.
