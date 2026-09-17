# Ein Kurzbefehl für alles — iPhone und Mac

**Ziel:** ein einziger Kurzbefehl im Teilen-Menü. Bei jedem Druck fragt er über
einen Menübaum, *was* geteilt wird und *was damit passieren soll* — Screenshot,
URL, Datei, Sprachnachricht oder Text; wahlweise direkt ins Journal oder mit
einem Auftrag an einen Agenten.

Alles läuft über **denselben Endpunkt** `POST /api/share` und **einen** Schlüssel.

> **Ein fertig importierbarer `.shortcut` lässt sich hier nicht mitliefern.**
> Apple signiert Kurzbefehl-Dateien geräteseitig; eine im Repository abgelegte
> Datei würde beim Import abgewiesen. Auf dem Mac gebaut, synchronisiert sich
> der Kurzbefehl über iCloud aufs iPhone — also nur **einmal** anlegen.

## 1. Was der Endpunkt kann

Jeder Aufruf trägt **genau eine Hauptquelle** und beliebig viele Zusatzfelder:

| Hauptquelle | Feld | Ohne `workflow_tag` (nur ablegen) | Mit `workflow_tag` (Auftrag) |
|---|---|---|---|
| Text | `source_text` | wird der Eintrag | ist das Material für das Modell |
| URL | `url` | nur der Link wird abgelegt, **nichts abgerufen** | Seite wird geholt und verarbeitet |
| Bild | `file` | Bild hängt am Eintrag | Bild geht an ein bildfähiges Modell |
| Audio | `file` | landet als normale Sprachnachricht, die der **tägliche Whisper-Lauf** (11:00) transkribiert | wird sofort transkribiert und verarbeitet |
| Dokument | `file` | Datei hängt am Eintrag | Text wird lokal extrahiert und verarbeitet |

Zusatzfelder: `text`, `title`, `hashtags`, `entry_at`.

**Das Feld `text` ist der Schlüssel zum Screenshot-Fall:** ohne Workflow wird es
Teil des Journaleintrags, mit Workflow ist es die Anweisung an das Modell.

Antwort: direkte Ablage `201 Created` mit `status: completed`; ein Auftrag
`202 Accepted` mit `status_url` — der Kurzbefehl muss darauf nicht warten,
Journl schreibt das Ergebnis selbst ins Tagesjournal.

## 2. Texterkennung: auf dem Gerät, Bild kommt mit

Kurzbefehle haben seit iOS 15 / macOS Monterey die Aktion **Text aus Bild
extrahieren** (Apples Live Text). Für einen Terminal-Screenshot — scharfer
Monospace auf Kontrast — ist das der Idealfall: kein Modell, keine Wartezeit,
kein OCR-Dienst auf dem Server.

Damit trotzdem **das Bild selbst im Journal landet**, wird beides in einem
Aufruf geschickt:

- `file` = der Screenshot → hängt als Foto am Eintrag
- `text` = der erkannte Text → steht als Text im selben Eintrag

Das ist ein Aufruf, ein Eintrag, beides drin.

## 3. Schlüssel erzeugen

**Einstellungen → Share-API & AI-Workflows** → Bezeichnung eintragen (z. B.
„Kurzbefehl"), Rechte anhaken, *Schlüssel erzeugen*:

| Recht | Wofür |
|---|---|
| `upload` | Quelle übertragen — immer nötig |
| `write_journal` | Ergebnis ins Tagesjournal schreiben — immer nötig |
| `run_workflow` | nur für den Zweig „mit Auftrag" |

Der Schlüssel wird genau einmal angezeigt. Auf dem Mac gehört er in den
Schlüsselbund, nicht in eine Textdatei.

## 4. Grundgerüst des Kurzbefehls

App **Kurzbefehle** → **+**, Name z. B. **An Journl**. In den Details
**Im Share-Sheet anzeigen** aktivieren; erlaubte Eingaben: Bilder, Dateien,
URLs, Text.

Dann eine Aktion **Aus Menü wählen** mit fünf Einträgen — das ist die erste
Ebene des Menübaums:

1. `Screenshot`
2. `URL`
3. `Datei`
4. `Sprachnachricht`
5. `Text`

In **jedem** Zweig steht als Erstes ein zweites **Aus Menü wählen** mit zwei
Einträgen: `Nur ablegen` und `Mit Auftrag`. Das ist die zweite Ebene.

Für den Zweig `Mit Auftrag` kommt zusätzlich eine Aktion **Nach Text fragen**
(„Was soll damit passieren?") davor; ihr Ergebnis geht ins Feld `text`, und
`workflow_tag` wird mitgeschickt.

Alle Zweige enden auf derselben Aktion: **Inhalte von URL abrufen** mit

- URL: `https://JOURNL-HOST/api/share`
- Methode: **POST**
- Header `Authorization` = `Bearer jts_DEIN_SCHLUESSEL`
  (genau ein Leerzeichen nach `Bearer`, keine Anführungszeichen)

Es unterscheiden sich nur Anfragetext und Felder.

## 5. Die fünf Zweige im Einzelnen

### Screenshot

Aktion **Text aus Bild extrahieren** auf die **Kurzbefehlseingabe**, danach:

- Anfragetext: **Formular**

| Feld | Typ | Wert |
|---|---|---|
| `file` | Datei | Kurzbefehlseingabe (das Bild) |
| `text` | Text | Ergebnis von *Text aus Bild extrahieren* |
| `title` | Text | `Screenshot` |
| `hashtags` | Text | `screenshot` |

Mit Auftrag zusätzlich `workflow_tag` und die abgefragte Anweisung; dann gehören
erkannter Text und Anweisung zusammen ins Feld `text`.

### URL

- Anfragetext: **JSON**, Feld `url` = Kurzbefehlseingabe.

Ohne `workflow_tag` legt Journl nur den Link ab und ruft ihn **nicht** auf —
deshalb darf das auch eine Adresse im Heimnetz sein. Mit `workflow_tag` holt
Journl die Seite (HTML, Text oder PDF bis 8 MB) und lässt sie verarbeiten.

### Datei

- Anfragetext: **Formular**, Feld `file` vom Typ **Datei** = Kurzbefehlseingabe.

Unterstützt bis 80 MB: PDF, DOCX, ODT, XLSX, TXT, Markdown, CSV, JSON, HTML.
Alte `.doc`/`.xls` werden nicht gelesen — vorher umwandeln.

### Sprachnachricht

- Anfragetext: **Formular**, Feld `file` vom Typ **Datei** = Aufnahme
  (M4A, MP3, WAV, OGG, WebM, AAC).

Ohne Auftrag wird daraus eine ganz normale Sprachnachricht im Journal; das
Transkript liefert der **tägliche Whisper-Lauf um 11:00** nach, oder sofort über
**Einstellungen → Sprachnachrichten → Jetzt transkribieren**. Mit Auftrag wird
sofort transkribiert und das Transkript verarbeitet.

### Text

- Anfragetext: **JSON**, Feld `source_text` = Kurzbefehlseingabe.

## 6. Testen

Im Zweig, den du gerade baust, einmal **Ergebnis anzeigen** anhängen. Erfolg
sieht so aus:

```json
{ "ok": true, "status": "completed" }
```

oder bei einem Auftrag:

```json
{ "ok": true, "status": "preparing", "status_url": "/api/share/jobs/…" }
```

Danach die Testaktion wieder entfernen.

## 7. Dasselbe vom Mac-Terminal

Der Schlüssel kommt aus dem Schlüsselbund, damit er nicht in der Shell-History
landet:

```sh
# einmalig ablegen:
#   security add-generic-password -a "$USER" -s journl-share -w 'jts_DEIN_SCHLUESSEL'
KEY="$(security find-generic-password -a "$USER" -s journl-share -w)"
HOST=https://JOURNL-HOST

# Screenshot samt erkanntem Text (OCR über die Kurzbefehle-App oder Vorschau)
curl -f -X POST -H "Authorization: Bearer $KEY" \
  -F 'file=@/Users/DU/Desktop/Screenshot.png' \
  -F "text=$(cat /tmp/ocr.txt)" \
  -F 'title=Screenshot' -F 'hashtags=screenshot' \
  "$HOST/api/share"

# URL nur ablegen
curl -f -X POST -H "Authorization: Bearer $KEY" -H 'Content-Type: application/json' \
  -d '{"url":"https://example.org/artikel","title":"Später lesen","hashtags":["ablage"]}' \
  "$HOST/api/share"

# Datei mit Auftrag
curl -f -X POST -H "Authorization: Bearer $KEY" \
  -F 'workflow_tag=ai-wissen-kurz' -F 'file=@/Users/DU/Desktop/Bericht.pdf' \
  -F 'text=Fasse die Kernaussagen auf fünf Punkte zusammen.' \
  "$HOST/api/share"
```

## 8. Abgrenzung zum Screenshot-Eingang

Der ältere Weg
[`README-iPhone-Screenshot-Kurzbefehl.md`](README-iPhone-Screenshot-Kurzbefehl.md)
legt ein Bild **unverlinkt im Schreiben-Tab** ab, wo es bewusst von Hand
zugeordnet wird. Er bleibt daneben bestehen. Der hier beschriebene Weg schreibt
direkt ins Tagesjournal.

Vollständiger HTTP-Vertrag, Rechte, Formate und Statusabfrage:
[`features/share-api.md`](features/share-api.md).
