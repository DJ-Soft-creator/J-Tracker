# An einen Agenten: Journl lesen und schreiben

Diese Seite ist als Kontext für einen externen Agenten gedacht (System-Prompt,
Tool-Beschreibung oder README). Sie beschreibt beide Richtungen: Inhalte ins
Journal **senden** (Share-API) und Journale **lesen/durchsuchen** (MCP).

## Zugang

Beides nutzt **denselben Bearer-Schlüssel** aus **Einstellungen →
Share-API & AI-Workflows**, aber getrennte Rechte:

| Zweck | Recht | Endpunkt |
|---|---|---|
| Notiz senden (direkt) | `upload` + `write_journal` | `POST /api/share` |
| Notiz senden (AI-Workflow) | zusätzlich `run_workflow` | `POST /api/share` |
| eigene Journale lesen | `mcp` | `POST /mcp/privat` (Alias `/mcp`) |
| Familiendokumente lesen | `mcp_family` | `POST /mcp/family` |

Auth immer per `Authorization: Bearer jts_...`, nur über HTTPS, Schlüssel wie
ein Passwort behandeln. Für einen Agenten empfiehlt sich ein eigener Schlüssel
mit genau den benötigten Rechten.

## A. Schreiben – Share-API

Jeder Aufruf enthält genau **eine** Hauptquelle: `source_text` **oder** `url`
**oder** das Multipart-Dateifeld `file`.

| Feld | Pflicht | Bedeutung |
|---|---:|---|
| `source_text` | ja* | Haupttext |
| `url` | ja* | öffentliche HTTP-/HTTPS-URL |
| `file` | ja* | Audio, Bild oder Datei (Multipart) |
| `workflow_tag` | nein | persönlicher AI-Hashtag; fehlt er, wird direkt abgelegt |
| `text` | nein | Zusatztext/Anweisung |
| `title` | nein | Überschrift, max. 160 Zeichen |
| `hashtags` | nein | Liste oder leerzeichengetrennter String |
| `entry_at` | nein | `HH:MM`, `HH:MM:SS` oder ISO-8601 |

\* genau eines davon. `Content-Type: application/json` für Text/URL,
`multipart/form-data` für Dateien.

Antwort: direkt `201 completed`; AI-Workflow `202 preparing` mit `status_url`
(asynchron — Status mit demselben Schlüssel abfragen). Zustände: `preparing`,
`queued`, `running`, `delivery_pending`, `completed`, `failed`.

```sh
# Text direkt ablegen (ohne AI)
curl -f -X POST \
  -H 'Authorization: Bearer jts_DEIN_SCHLUESSEL' \
  -H 'Content-Type: application/json' \
  -d '{"source_text":"Einkaufsliste: Milch, Brot","title":"Notiz","hashtags":["ablage"]}' \
  https://JOURNL-HOST/api/share

# Text durch AI-Workflow aufbereiten lassen
curl -f -X POST \
  -H 'Authorization: Bearer jts_DEIN_SCHLUESSEL' \
  -H 'Content-Type: application/json' \
  -d '{"workflow_tag":"ai-wissen-kurz","source_text":"Rohtext...","text":"Kurz zusammenfassen."}' \
  https://JOURNL-HOST/api/share

# Datei senden
curl -f -X POST \
  -H 'Authorization: Bearer jts_DEIN_SCHLUESSEL' \
  -F 'file=@Sprachnachricht.m4a' -F 'title=Gedanke unterwegs' \
  https://JOURNL-HOST/api/share
```

Grenzen: Uploads bis 80 MB (Audio M4A/MP3/WAV/OGG/WebM/AAC, Bilder
JPEG/PNG/WebP, Text TXT/MD/CSV/JSON/HTML, Dokumente PDF/DOCX/ODT/XLSX),
`source_text` bis 180.000 Zeichen, `text` bis 10.000 Zeichen. URLs werden nur
im AI-Modus abgerufen (nur öffentliche Hosts, Ports 80/443).

## B. Lesen – MCP

Journl stellt zwei **rein lesende** MCP-Server bereit (Streamable HTTP,
JSON-RPC). Der Bereich wird ausschließlich vom Endpunkt bestimmt und kann nicht
über Argumente erweitert werden.

| Server | Inhalt | Recht | Werkzeuge |
|---|---|---|---|
| `POST /mcp/privat` | eigene Journale | `mcp` | `list_tags`, `list_days`, `get_blocks`, `get_day`, `search` |
| `POST /mcp/family` | Familiendokumente | `mcp_family` | `list_tags`, `get_blocks` |

Empfohlener Ablauf: `list_tags` (welche Hashtags gibt es?) → `list_days` (an
welchen Tagen?) → `get_blocks`/`get_day` (was steht da?). `get_day` liefert
auch ungetaggte Blöcke; `search` ist die Volltextsuche (nur wenn der
DB-Suchindex per `JOURNL_DB_ENABLED` aktiv ist, sonst nicht in `tools/list`).

Client-Konfiguration:

```jsonc
{
  "mcpServers": {
    "journl": {
      "type": "http",
      "url": "https://JOURNL-HOST/mcp/privat",
      "headers": { "Authorization": "Bearer jts_…" }
    },
    "journl-family": {
      "type": "http",
      "url": "https://JOURNL-HOST/mcp/family",
      "headers": { "Authorization": "Bearer jts_…" }
    }
  }
}
```

Aufruf:

```sh
curl -s -X POST https://JOURNL-HOST/mcp/privat \
  -H 'Authorization: Bearer jts_DEIN_SCHLUESSEL' \
  -H 'Content-Type: application/json' \
  -d '{"jsonrpc":"2.0","id":2,"method":"tools/call",
       "params":{"name":"get_blocks","arguments":{"tags":["focus"],
                 "start_date":"2026-08-01","end_date":"2026-08-31"}}}'
```

## Regeln für den Agenten

1. Pro Sende-Aufruf genau eine Hauptquelle.
2. Bei `4xx`/`5xx` den `error`-Text aus dem JSON-Body auswerten, nicht blind
   wiederholen.
3. MCP ist rein lesend; Schreiben ausschließlich über die Share-API.
4. `mcp` und `mcp_family` nie mischen — ein Familien-Schlüssel darf kein
   persönliches Journal sehen und umgekehrt.

Vollständige Details: Schreiben [`share-api.md`](share-api.md), Lesen
[`mcp-api.md`](mcp-api.md).
