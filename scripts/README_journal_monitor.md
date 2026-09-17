# External Journal Monitor

[Projektuebersicht](../README.md) | [Family Scheduler](README_scheduler.md)

Der Journal Monitor ist ein hostseitiger Cron-Job. Er sucht in konfigurierten
Markdown-Dateien nach expliziten Hermes- und OpenCode-Auftraegen, startet das
jeweilige CLI und schreibt dessen endgueltige Antwort in die Quelldatei.

Der Monitor ist kein Bestandteil des Docker-Containers und liegt absichtlich
ausserhalb dieses Repositories. Diese Datei ist die Referenz fuer Entwicklung
und Betrieb; die ausfuehrbare Datei bleibt die kanonische Implementierung.

## Externe Implementierung

```text
/Users/david/Nextcloud_on_Prem/DMS Dokumente/PC/Davids_Admin_Pannel/journal_monitor.py
```

Isolierte Tests liegen daneben in `test_journal_monitor.py`.

## Installierter Crontab

Der aktuell installierte Host-Crontab startet den Monitor jede Minute:

```cron
*/1 * * * * /usr/bin/python3 "/Users/david/Nextcloud_on_Prem/DMS Dokumente/PC/Davids_Admin_Pannel/journal_monitor.py" >> /Users/david/Nextcloud_on_Prem/Brain/Brain_logging/journal_monitor.log 2>&1
```

Pruefen und bearbeiten:

```bash
crontab -l
crontab -e
```

Das Cron-Log kann Inhalte oder Fehlermeldungen aus persoenlichen Auftraegen
enthalten und darf nicht in dieses Repository kopiert werden.

## Trigger

| Trigger | Primaerer Kontext | Zusaetzlicher Kontext |
|---|---|---|
| `#Hermes` | Vollstaendige ausloesende Markdown-Datei | keiner |
| `#OpenCode` | Vollstaendige ausloesende Markdown-Datei | keiner |
| `#Hermes-Todo` | Aktueller Block bis zum naechsten Trigger, `---`, `___` oder Dateiende | `<user-ID>/notes/Hermes-Addon-Promt.md` |
| `#OpenCode-Todo` | Aktueller Block bis zum naechsten Trigger, `---`, `___` oder Dateiende | `<user-ID>/notes/OpenCode-Addon-Promt.md` |

### AI-Workflows

Die bisherigen append-only Sessions unter `<user-ID>/projects/` bleiben lesbar.
Neue `#ai-<workflow>`-Sessions liegen dagegen direkt in der autorisierten
Quelldatei. Der Monitor verarbeitet ausschließlich eine einzelne
`jt:agent-session-config`-Konfiguration mit aktive(m) `user-request`-Event und
ruft den hostseitig installierten `run-agent-session.sh` mit kanonischem
`--data-root`, `--session-id` und `--expected-revision` auf. Antworten tragen
`jt:agent-session {"origin":"agent"}` und dürfen daher niemals einen neuen
Turn auslösen. Pausierte und beendete Konfigurationen werden übersprungen.

Vor einem Start prüft der Monitor die ACL der Quelle und jeder freigegebenen
`context_files` erneut, einschließlich Pfad-Traversal und Symlink-Fluchten. Der
Container führt das Script nicht aus. Nach erfolgreichem atomarem Schreiben wird
der bestehende Index-Rebuild angestoßen; bei einem Revisionskonflikt bleibt die
Datei unverändert und der Turn erhält den Status `conflict`.

Ein namensgleiches Skript, zum Beispiel `/app/scripts/ai-einkaufsliste.py`, kann
weiterhin workflow-spezifisches `prepare` und `apply` uebernehmen. Ohne Skript
verwendet der Monitor Prompt und Kontext direkt aus dem
`jt:ai-session-config`-Block. Die Host-CLI muss Agent und Modell aus dieser
Konfiguration verwenden und ihren persistenten Session-Identifier im naechsten
Event dokumentieren.

Nach einer `permission-request` wartet der Monitor auf ein passendes
`permission-decision`-Event. Archivierte Dateien unter
`<user-ID>/projects/_Archive/` werden nicht mehr ausgefuehrt, bleiben aber fuer
Brain-Suche und Verlauf sichtbar.

Fehlt ein Addon-Prompt, wird der Auftrag trotzdem ausgefuehrt. Der Monitor
setzt dann einen sichtbaren Qualitaetshinweis an den Anfang der Antwort.

OpenCode-Trigger koennen optional das Profil `-GPT` oder `-privat` tragen,
beispielsweise `#OpenCode-GPT` oder `#OpenCode-Todo-privat`. Das aendert nur
das an `opencode run --model` uebergebene Modell; Datei-/Blockkontext,
Addon-Verhalten und Abschlussregeln bleiben gleich.

## Generierter Steuerprompt

`build_prompt()` erzeugt keinen separaten API-Systemprompt. Der Text wird als
Auftrag an `hermes chat` beziehungsweise `opencode run` uebergeben. Alle vier
Varianten enthalten denselben verbindlichen Abschluss:

```text
Deine gesamte Textausgabe wird als endgueltige Antwort direkt in die
ausloesende Markdown-Datei geschrieben. Antworte deshalb direkt an den User
und liefere ausschliesslich das verwendbare Endergebnis. Gib keine internen
Todos, Planungsnotizen, Statusmeldungen oder Anweisungen fuer weitere Agenten
aus. Erzeuge keinen neuen #Hermes-, #Hermes-Todo-, #OpenCode- oder
#OpenCode-Todo-Auftrag.
```

Normale Trigger erhalten danach den Abschnitt
`--- Vollstaendige ausloesende Datei ---`. Todo-Trigger erhalten getrennte
Abschnitte `--- Todo-Block ---` und `--- Addon-Prompt (...) ---`.

Prompt-Anpassungen gehoeren zentral in `build_prompt()`. Die sichtbare
Antwortkennzeichnung wird separat in `append_result()` erzeugt und sollte
nicht vom Modell abhaengig gemacht werden.

## User-Zuordnung

Der erste Pfadteil unter `_Archiv` ist die `<user-ID>`. Dateien unter `family/`
oder direkt unter `_Archiv` werden dem David-Account
`87743dc4-fb76-4842-a247-ea02da04cb15` zugeordnet.

Endgueltige Fehler werden im Tagesjournal des ermittelten Users abgelegt:

```text
<user-ID>/YYYY/MM/DD/Journal_YYYY-MM-DD.md
```

## Antwortformat und Schleifenschutz

Eine erfolgreiche Antwort wird mit Start- und Endzeit in den Journalverlauf
eingefuegt:

```markdown
___

- 14:09:43

<!-- JOURNAL_MONITOR_RESPONSE_START tool="Hermes" status="completed" request_id="..." -->

## Endgueltige Antwort von Hermes an den User

> Dies ist die endgueltige Rueckmeldung des KI-Tools an den User und kein neuer oder interner Auftrag.

[Antwort]

<!-- JOURNAL_MONITOR_RESPONSE_END -->

~~14:12:08~~

___
```

Der Parser ignoriert technische Antwortbereiche vollstaendig. Markertext aus
einer Tool-Ausgabe wird maskiert, damit die Ausgabe den Bereich nicht vorzeitig
schliessen und darin erwaehnte Trigger keine neue Schleife starten koennen.

## State, Retry und Lock

Der persistente State liegt in `Brain_logging/journal_monitor.json`. Er speichert
Dateihash und -identitaet sowie Request-Status; vorhandene Trigger werden bei
einer State-Migration als sichere Baseline nicht ausgefuehrt.

Vor dem Prozessstart wird genau dieser Request als `starting` gespeichert. Erst
nach erfolgreicher Prozesserzeugung wird der Versuch als `running` gezaehlt. Ein
erster Timeout, Exit-Code oder Start-/Schreibfehler wird als `retry_pending`
vorgemerkt. Erst der naechste Cron-Lauf fuehrt genau einen zweiten Versuch aus.
Nach dessen Fehlschlag wird der Fehlercode mit einer eindeutigen Log-ID im
User-Journal protokolliert und der Request nicht erneut automatisch gestartet.

Vor dem Anhaengen einer Antwort werden Hash und Dateiidentitaet erneut geprueft.
Wurde die Quelle waehrend des KI-Laufs bearbeitet, geloescht oder ersetzt, wird
keine Antwort in die veraenderte Datei geschrieben.

`journal_monitor.lock` ist eine persistente Lock-Datei. Entscheidend ist die
vom Betriebssystem gehaltene `flock`-Sperre: Solange ein Lauf aktiv ist,
ueberspringt ein paralleler Cron-Lauf die Ausfuehrung. Die leere Datei selbst
muss nach einem Lauf nicht geloescht werden.

## Manuelle Pruefung

Der Dry-Run startet kein KI-CLI und veraendert den State nicht:

```bash
/usr/bin/python3 "/Users/david/Nextcloud_on_Prem/DMS Dokumente/PC/Davids_Admin_Pannel/journal_monitor.py" --dry-run
```

Die isolierten Tests starten ebenfalls keine KI-Tools und lesen keine
Journal- oder Addon-Inhalte aus dem produktiven Archiv:

```bash
cd "/Users/david/Nextcloud_on_Prem/DMS Dokumente/PC/Davids_Admin_Pannel"
/usr/bin/python3 -m unittest -v test_journal_monitor.py
```

## Abgrenzung zum Family Scheduler

Der [Family Scheduler](README_scheduler.md) ist ein Compose-Service fuer
wiederkehrende Familienaufgaben. Der Journal Monitor ist dagegen ein
hostseitiger Cron-Job fuer explizite KI-Auftraege in Markdown-Dateien.
