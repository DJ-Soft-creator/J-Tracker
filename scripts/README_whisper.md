# Lokales Whisper initialisieren und betreiben

Dieses Dokument ist das aktuelle Betriebs- und API-Runbook. Datierte frühere
Übergaben liegen ausschließlich im zentralen Archiv.

Whisper läuft als eigener CPU-Container mit einer API, die auch andere Dienste im
Heimnetz verwenden können. Ein separater Scheduler liest Journl-Audiodateien,
transkribiert täglich ab 11:00 Uhr Europe/Berlin und schreibt zwei Ergebnisse:

- `YYYY-MM-DD_HH-MM-SS_<media-id>_transcript.json` neben der dauerhaft gesendeten
  Audiodatei mit Sprachkennung sowie Segmenten mit Start- und Endzeit
- lesbaren Fließtext direkt beim Medieneintrag in der Journal-Markdown-Datei

## Einmalige Initialisierung

Nach dem Deployment des Codes genau einmal je Umgebung ausführen. Zuerst DEV:

```bash
cd /docker-storage/Journal-Tracker-DEV
sudo ./scripts/init-whisper.sh dev
```

Erst nachdem die Änderung nach PROD promotet wurde, im PROD-Checkout:

```bash
cd /docker-storage/Journal-Tracker
sudo ./scripts/init-whisper.sh prod
```

Das Skript:

1. kontrolliert, dass `dev` beziehungsweise `prod` nicht im falschen Checkout läuft,
2. ergänzt nur fehlende Whisper-Werte in der persistenten `.env`,
3. erzeugt bei Bedarf einen zufälligen 256-Bit-API-Key, ohne ihn auszugeben,
4. baut und startet Whisper-API und 11-Uhr-Scheduler,
5. prüft `/health` und lädt das Modell `base` in das Docker-Volume.

Mit `--no-model-download` kann der letzte Schritt ausgelassen werden. Das Modell
wird dann beim ersten Transkriptionsauftrag geladen. Bereits vorhandene Werte
werden nicht überschrieben; nur ein leerer oder als `<...>` markierter API-Key
wird ersetzt.

| Umgebung | persistente `.env` | API-Port | Modell-Volume |
|---|---|---:|---|
| DEV | `/docker-storage/my_Journal_data_DEV/data/journals/.env` | 8091 | `whisper-models-dev` |
| PROD | `/docker-storage/my_Journal_data/data/journals/.env` | 8090 | `whisper-models` |

## Konfiguration

```dotenv
WHISPER_API_KEY=<privater-zufallswert>
WHISPER_MODEL=base
WHISPER_MODELS=tiny,base,small,medium
WHISPER_COMPUTE_TYPE=int8
WHISPER_CPU_THREADS=4
WHISPER_PORT=8090
WHISPER_SCHEDULE_HOUR=11
WHISPER_LANGUAGE=de
```

DEV verwendet standardmäßig Port 8091. `WHISPER_MODEL` ist der Standard, den
Scheduler und Oberfläche ohne ausdrückliche Wahl verwenden; `WHISPER_MODELS`
legt fest, welche Qualitätsstufen die Oberfläche überhaupt anbietet. `base` ist
der vorsichtige Startwert für deutsche Sprache auf CPU.

| Modell | RAM (int8) | Deutsch |
|---|---:|---|
| `tiny` | ~75 MB | sehr schnell, ungenau |
| `base` | ~145 MB | schnell, brauchbar für kurze Notizen |
| `small` | ~500 MB | deutlich bessere deutsche Texte, etwa 3× langsamer als `base` |
| `medium` | ~1,5 GB | beste realistische Qualität, sehr langsam, RAM-knapp |
| `large-v3` | ~3 GB | auf diesem Server nicht empfohlen, nur über `WHISPER_MODELS` freischaltbar |

Der Dienst hält bewusst **genau ein Modell** im Speicher. Eine Anfrage mit einer
anderen Stufe entlädt die vorherige und lädt die gewünschte nach; der erste
Aufruf einer noch nicht heruntergeladenen Stufe dauert entsprechend länger
(`small` gemessen: rund 35 Sekunden Download und Laden auf dem DEV-Server).

Auf dem Zielserver wurden 4 vCPU und etwa 3,8 GiB RAM gesehen. Echte API-Läufe
mit `tiny` und einer einsekündigen Audiodatei waren erfolgreich; `small` belegte
geladen rund 870 MiB im Container. `medium` und stundenlange deutsche Aufnahmen
sind hinsichtlich Laufzeit und Speicherbedarf noch nicht gemessen.

## Bedienung in Journl

- Eine Aufnahme wird zunächst als Originaldatei gespeichert.
- Unter **Einstellungen → Sprachnachrichten** wird das Modell für alle
  Transkriptionen gewählt; **Jetzt transkribieren** plant damit einen Lauf für
  den angemeldeten Benutzer ein.
- Die Auswahl gilt geräteweit (`localStorage`) und wirkt auch auf die
  Modellauswahl direkt an einer Sprachnachricht im Schreiben-Tab sowie auf die
  Transkription, die eine KI-Anfrage für Sprachanhänge auslöst.
- Der tägliche 11-Uhr-Lauf ohne manuellen Anstoß nutzt weiterhin
  `WHISPER_MODEL`, weil er keiner Benutzerwahl zugeordnet ist.
- Ohne manuellen Start verarbeitet der Scheduler gesendete, noch nicht
  transkribierte Aufnahmen in den Tagesordnern täglich ab 11:00 Uhr lokaler Zeit.
- Nach erfolgreicher Transkription wird automatisch ein Brain-Rebuild eingeplant.

Der manuelle Knopf arbeitet asynchron: Er legt nur den Auftrag ab. Der Scheduler
prüft diese Aufträge etwa alle 30 Sekunden. Lange CPU-Transkriptionen dürfen danach
weiterlaufen, ohne dass der Browser geöffnet bleiben muss.

## API für andere Dienste

`GET /health`, `GET /v1/models` und `GET /openapi.json` sind ohne Schlüssel
lesbar. Modellladen und
Transkription benötigen `Authorization: Bearer ...` oder `X-API-Key`.

Beispiel für PROD, ohne den Schlüssel in die Shell-Historie zu schreiben:

```bash
WHISPER_KEY="$(sudo awk -F= '$1 == "WHISPER_API_KEY" {sub(/^[^=]*=/, ""); print; exit}' /docker-storage/my_Journal_data/data/journals/.env)"
curl http://127.0.0.1:8090/health
curl http://127.0.0.1:8090/v1/models
curl -X POST -H "Authorization: Bearer $WHISPER_KEY" -F model=small http://127.0.0.1:8090/v1/models/load
curl -X POST -H "Authorization: Bearer $WHISPER_KEY" -F file=@aufnahme.m4a -F language=de -F model=small -F response_format=verbose_json http://127.0.0.1:8090/v1/audio/transcriptions
unset WHISPER_KEY
```

Der veröffentlichte Port ist für das private Heimnetz gedacht und darf nicht
ungeschützt per Portfreigabe oder Reverse Proxy ins Internet gestellt werden.

## Betrieb und Fehlersuche

Die folgenden PROD-Beispiele lassen sich für DEV mit Compose-Datei
`docker-compose.dev.yml`, DEV-`.env` und den Service-Namen mit `-dev` verwenden.

```bash
cd /docker-storage/Journal-Tracker
sudo docker compose --env-file /docker-storage/my_Journal_data/data/journals/.env -f docker-compose.yml --profile whisper ps
sudo docker compose --env-file /docker-storage/my_Journal_data/data/journals/.env -f docker-compose.yml --profile whisper logs --tail=100 whisper whisper-scheduler
sudo docker compose --env-file /docker-storage/my_Journal_data/data/journals/.env -f docker-compose.yml --profile whisper restart whisper whisper-scheduler
sudo docker compose --env-file /docker-storage/my_Journal_data/data/journals/.env -f docker-compose.yml --profile whisper stop whisper whisper-scheduler
```

Modelle bleiben beim Container-Neubau im benannten Docker-Volume erhalten. Das
Löschen dieses Volumes entfernt den Modelldownload und ist deshalb kein normaler
Wartungsschritt.

Bei einer fehlgeschlagenen Transkription bleibt das Audiooriginal erhalten. Der
Status und eine gekürzte Fehlermeldung stehen in `metadata.json`; ein späterer
manueller oder täglicher Lauf versucht den Eintrag erneut. Browserseitig fordert
Journl während sichtbarer Aufnahme einen Wake Lock an und lädt ungefähr alle zehn
Sekunden einen Chunk hoch. iOS kann eine Webaufnahme bei manuellem Sperren oder im
Hintergrund trotzdem unterbrechen; ein echter Langzeittest auf dem iPhone bleibt
deshalb erforderlich.
