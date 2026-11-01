# Share-API: Inhalte per AI-Workflow ins Tagesjournal

Die Share-API nimmt Text, URLs, Audio, Bilder und unterstützte Dateien entgegen.
Ohne `workflow_tag` legt sie die Quelle direkt als **neuen, eigenständigen
Journaleintrag** ab – ideal für eine URL mit `#ablage` oder einen späteren
Lesestapel. Mit einem persönlichen AI-Workflow bereitet Journl den Inhalt lokal
auf, führt den Workflow über den DB-nativen Pi-Host-Worker aus und schreibt
dessen Antwort ins Journal. Der Schreiben-Tab und Schreibsessions sind an
beiden Abläufen nicht beteiligt.

> Kompakte, direkt an einen externen Agenten übergebbare Fassung (Senden und
> Lesen): [`agent-journl-api.md`](agent-journl-api.md).

## 1. AI-Workflow einrichten

Unter **Einstellungen → Share-API & AI-Workflows → AI-Workflows gestalten**
wird ein persönlicher AI-Hashtag angelegt, zum Beispiel `#ai-wissen-kurz`.
Für die Share-API gelten:

- Agent: **Pi**
- Zielbereich: **Share-API → Tagesjournal**
- Modell: ein auf dem Host-Worker verfügbares Modell
- Prompt: verbindliche Aufgabe, Ausgabeform und gewünschte Länge
- erlaubte Eingaben: Text, URL, Audio, Bild und/oder Datei
- optionale Standard-Hashtags für den fertigen Journaleintrag

Ein geeigneter Prompt lautet beispielsweise:

```text
Erstelle aus der freigegebenen Quelle einen kompakten Wissenseintrag in
deutschem Markdown. Nenne Kernaussage, drei wichtige Punkte und eine kurze
Einordnung. Erfinde keine Fakten. Bei einer URL muss die Quelle erkennbar
bleiben. Gib ausschließlich den Inhalt des Eintrags aus, ohne Präambel.
```

Workflow-Änderungen wirken auf neue Aufträge. Bereits eingereihte Aufträge
behalten ihren unveränderlichen Kontext und den beim Einreihen gespeicherten
Workflow-Prompt.

## 2. API-Schlüssel und Rechte

Unter **Einstellungen → Share-API & AI-Workflows** werden persönliche Schlüssel
erzeugt. Journl speichert ausschließlich deren SHA-256-Hash; der vollständige
Schlüssel wird genau einmal angezeigt.

Ein Konto darf **mehrere Schlüssel** halten (höchstens zehn), jeder mit eigener
Bezeichnung und eigenen Rechten. So bekommt der iPhone-Kurzbefehl einen
Schlüssel zum Teilen und der MCP-Client einen rein lesenden — und ein einzelner
lässt sich widerrufen, ohne die anderen zu treffen. Widerrufen geschieht je
Schlüssel über *Widerrufen* in der Liste.

Die Rechte sind getrennt:

| Recht | Bedeutung |
|---|---|
| `upload` | neue Quellen übertragen |
| `run_workflow` | einen persönlichen AI-Workflow starten |
| `write_journal` | das Ergebnis automatisch ins persönliche Journal schreiben |
| `mcp` | lesender MCP-Zugriff auf die **eigenen Journale** (`/mcp/privat`) |
| `mcp_family` | lesender MCP-Zugriff auf die **Familiendokumente** (`/mcp/family`) |

`mcp` und `mcp_family` sind bewusst zwei Rechte: ein Familien-Assistent soll nie
in ein persönliches Journal sehen können. Details in
[`mcp-api.md`](mcp-api.md).

Eine direkte Ablage ohne `workflow_tag` benötigt nur `upload` und
`write_journal`. Ein AI-Auftrag benötigt zusätzlich `run_workflow`. Dadurch
kann ein eingeschränkter Schlüssel nicht versehentlich einen vollständigen
AI-zu-Journal-Auftrag ausführen. Schlüssel ausschließlich über HTTPS verwenden
und wie ein Passwort behandeln.

## 3. HTTP-Vertrag

```text
POST https://JOURNL-HOST/api/share
Authorization: Bearer jts_...
Content-Type: application/json oder multipart/form-data
```

Jeder Aufruf enthält genau **eine Hauptquelle**:

| Feld | Verwendung |
|---|---|
| `source_text` | geteilter Haupttext |
| `url` | öffentliche HTTP-/HTTPS-URL |
| `file` | Audio, Bild oder unterstützte Datei als Multipart-Dateifeld |

Zusätzliche Felder:

| Feld | Pflicht | Bedeutung |
|---|---:|---|
| `workflow_tag` | nein | konfigurierter AI-Workflow, mit oder ohne führendes `#`; fehlt er, wird direkt abgelegt |
| `text` | nein | Zusatztext; bei AI eine Anweisung/Info, bei direkter Ablage Teil des Journaleintrags |
| `title` | nein | feste Überschrift des Journaleintrags, maximal 160 Zeichen |
| `hashtags` | nein | Leerzeichen-/Komma-getrennte Hashtags oder JSON-Liste |
| `entry_at` | nein | `HH:MM`, `HH:MM:SS` oder ISO-8601-Zeitpunkt mit optionaler Zeitzone |

Nur eine Uhrzeit verwendet den serverseitig aktuellen Tag in `TZ`. Ein
vollständiger ISO-Zeitpunkt darf einen anderen Tag angeben; das Ergebnis landet
im Journal dieses Tages. Ohne `entry_at` gilt die aktuelle Serverzeit.

### URL direkt ablegen – ohne AI

Ohne `workflow_tag` wird keine URL abgerufen, kein AI-Worker gestartet und kein
Inhalt an ein Modell gesendet. Journl speichert nur den Link sowie Titel,
Zusatztext und Hashtags. Deshalb darf ein reiner Ablage-Link auch auf eine
private HTTP-/HTTPS-Adresse zeigen; Journl ruft ihn selbst nicht auf.

```sh
curl -f -X POST \
  -H 'Authorization: Bearer jts_DEIN_ABLAGE_SCHLUESSEL' \
  -H 'Content-Type: application/json' \
  -d '{
    "url": "https://example.org/artikel",
    "title": "Später lesen",
    "hashtags": ["ablage", "lesen"],
    "text": "Für den Wochenrückblick.",
    "entry_at": "18:42"
  }' \
  https://JOURNL-HOST/api/share
```

Die direkte Ablage antwortet sofort mit `201 Created` und `status: completed`.

### Text senden

```sh
curl -f -X POST \
  -H 'Authorization: Bearer jts_DEIN_SCHLUESSEL' \
  -H 'Content-Type: application/json' \
  -d '{
    "workflow_tag": "ai-wissen-kurz",
    "source_text": "Der zu verarbeitende Haupttext",
    "text": "Bitte auf die praktischen Konsequenzen eingehen.",
    "title": "Neue Erkenntnis",
    "hashtags": ["wissen", "praxis"],
    "entry_at": "18:42"
  }' \
  https://JOURNL-HOST/api/share
```

### URL abrufen und zusammenfassen

```sh
curl -f -X POST \
  -H 'Authorization: Bearer jts_DEIN_SCHLUESSEL' \
  -F 'workflow_tag=ai-wissen-kurz' \
  -F 'url=https://example.org/artikel' \
  -F 'text=Fasse den Nutzen für mein Projekt besonders knapp zusammen.' \
  -F 'hashtags=#wissen #quelle' \
  -F 'entry_at=2026-08-15T14:35:00+02:00' \
  https://JOURNL-HOST/api/share
```

Journl folgt nur sicheren HTTP-/HTTPS-Weiterleitungen, erlaubt ausschließlich
die Ports 80 und 443 und blockiert Loopback-, private, Link-Local- und andere
nicht öffentliche Zieladressen. Unterstützt werden HTML, Klartext und PDF bis
8 MB. Skripte und Styles einer Webseite werden nicht als Inhalt übernommen.

### Audio oder Datei senden

```sh
curl -f -X POST \
  -H 'Authorization: Bearer jts_DEIN_SCHLUESSEL' \
  -F 'workflow_tag=ai-wissen-kurz' \
  -F 'file=@Sprachnachricht.m4a' \
  -F 'text=Formuliere daraus einen Wissenseintrag.' \
  -F 'title=Gedanke unterwegs' \
  https://JOURNL-HOST/api/share
```

Audio wird über den privaten Whisper-Dienst transkribiert. Die Originaldatei,
das Transkript und der erzeugte Eintrag bleiben im persönlichen Journaldaten-
Bereich. Bilder werden dem konfigurierten bildfähigen Modell als visueller
Anhang übergeben. Textdateien und Office-Container werden lokal gelesen; es
werden keine Makros oder eingebetteten Programme ausgeführt.

Unterstützte Uploads bis 80 MB:

- Audio: M4A, MP3, WAV, OGG, WebM und AAC
- Bilder: JPEG, PNG und WebP
- Text: TXT, Markdown, CSV, JSON und HTML
- Dokumente: PDF, DOCX, ODT und XLSX

Alte binäre DOC- und XLS-Dateien werden nicht interpretiert. Sie sollten vor
dem Senden in DOCX, XLSX oder PDF umgewandelt werden. Der extrahierte
AI-Kontext ist zusätzlich auf 180.000 Zeichen begrenzt.

## 4. Asynchroner Status

Ein angenommener Auftrag antwortet mit HTTP `202 Accepted`:

```json
{
  "ok": true,
  "id": "0d37057f-1c86-4b40-906e-e081c92a8597",
  "status": "preparing",
  "status_url": "/api/share/jobs/0d37057f-1c86-4b40-906e-e081c92a8597"
}
```

Der Status ist mit demselben Bearer-Schlüssel abrufbar:

```sh
curl -f \
  -H 'Authorization: Bearer jts_DEIN_SCHLUESSEL' \
  https://JOURNL-HOST/api/share/jobs/0d37057f-1c86-4b40-906e-e081c92a8597
```

Mögliche Zustände sind `preparing`, `queued`, `running`, `delivery_pending`,
`completed` und `failed`. Bei Erfolg enthält die Antwort `journal_path`; bei
Fehlern enthält sie eine begrenzte, verständliche `error`-Meldung. Die
Zustellung ist idempotent: Ein Jobmarker verhindert bei Neustart oder erneutem
Statusabgleich einen doppelten Journaleintrag.

## 5. iPhone-Kurzbefehl

Für einen universellen Share-Sheet-Kurzbefehl:

1. In Kurzbefehle einen Kurzbefehl **An Journl senden** erstellen.
2. **Im Share-Sheet anzeigen** aktivieren und Text, URLs, Bilder, Medien und
   Dateien erlauben.
3. Optional nach Titel, Zusatztext oder Workflow fragen beziehungsweise feste
   Standardwerte hinterlegen. Bleibt `workflow_tag` leer, wird direkt abgelegt.
4. Die Aktion **Inhalte von URL abrufen** mit Methode `POST` verwenden.
5. Header `Authorization` auf `Bearer jts_DEIN_SCHLUESSEL` setzen.
6. Als Anfragetext **Formular** wählen: `workflow_tag`, optional `text`,
   `title`, `hashtags`, `entry_at` sowie abhängig von der Eingabe `url`,
   `source_text` oder das Dateifeld `file` setzen.
7. Für eine sofort sichtbare Bestätigung `id` und `status` aus der JSON-Antwort
   anzeigen. Optional kann ein zweiter Kurzbefehl die `status_url` abfragen.

Der bisherige Screenshot-Kurzbefehl und
`POST /api/screenshot-inbox/upload` bleiben unverändert verfügbar. Sie dienen
weiterhin dem manuellen Screenshot-Eingang über den Schreiben-Tab; die neue
Share-API ist der automatische Workflow-zu-Journal-Weg.

## 6. Datenschutz und Fehlerverhalten

- Upload-Schlüssel können weder Journale lesen noch beliebige vorhandene
  Dateien abrufen.
- Statuszugriffe sind auf den Besitzer des Schlüssels und die konkrete Job-ID
  beschränkt.
- Quelldateien werden vor der AI-Verarbeitung per SHA-256 erneut geprüft.
- Externe Inhalte gelten im Prompt als Referenzmaterial, nicht als Anweisung;
  eingebettete Prompt-Injection soll dadurch keine Workflow-Regeln ersetzen.
- Erst eine vollständige AI-Antwort wird atomar ins Journal geschrieben. Bei
  Fehlern entsteht kein halbfertiger Journaleintrag.
- Originaldateien bleiben privat unter dem jeweiligen Tagesordner. URL-Aufträge
  speichern die Quelle und den Abrufnachweis, aber keinen öffentlich lesbaren
  Cache.
