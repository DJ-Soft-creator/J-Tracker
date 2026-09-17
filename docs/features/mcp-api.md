# MCP-API: Journalsuche im LLM-Kontext

Journl stellt Model-Context-Protocol-Endpunkte bereit, die ein MCP-fähiger
Client (LM Studio, Claude Code, Claude Desktop, eigene Agenten) als lesende
Werkzeuge einbindet. Sie laufen im normalen App-Container auf dem bereits
veröffentlichten Port — es kommt kein zusätzlicher Dienst und kein weiterer Port
hinzu.

## 1. Zwei getrennte Server

| URL | Inhalt | Recht | Werkzeuge |
|---|---|---|---|
| `POST /mcp/privat` (Alias: `/mcp`) | nur die **eigenen Journale** | `mcp` | `list_tags`, `list_days`, `get_blocks`, `get_day`, `search` |
| `POST /mcp/family` | nur die **Familiendokumente** | `mcp_family` | `list_tags`, `get_blocks` |

Die Trennung ist beidseitig hart: Jeder Server löst ausschließlich Referenzen
seiner eigenen Quelle auf, und jeder verlangt sein eigenes Token-Recht. Ein
Schlüssel für den Familien-Assistenten kann damit kein persönliches Journal
lesen — auch dann nicht, wenn ein Journalblock einen Familien-Hashtag trägt.
Umgekehrt liefert der private Server keine Familiendokumente mehr.

Der Familien-Server bietet weder Volltextsuche noch die Tageswerkzeuge an: Der
DB-Suchindex enthält nur persönliche Journale, und Familienzeilen sind an
Zeilennummern verankert und tragen kein Datum — Datumsfilter bleiben dort
wirkungslos.

## 2. Schlüssel erzeugen

Unter **Einstellungen → Share-API & AI-Workflows** eine Bezeichnung eintragen
(z. B. „LM Studio"), das Recht **MCP privat (lesend)** und/oder **MCP Familie
(lesend)** anhaken und *Schlüssel erzeugen* klicken. Der Schlüssel wird nur
einmal angezeigt; gespeichert wird ausschließlich sein SHA-256-Hash.

Ein Konto darf mehrere Schlüssel halten. Für den MCP-Client also einen eigenen,
rein lesenden Schlüssel anlegen statt den des iPhone-Kurzbefehls
weiterzuverwenden — jeder ist einzeln widerrufbar.

## 3. Client verbinden

Der Endpunkt spricht Streamable HTTP und erwartet den Schlüssel als
`Authorization: Bearer <token>`:

```jsonc
{
  "mcpServers": {
    "journl": {
      "type": "http",
      "url": "https://<journl-host>/mcp/privat",
      "headers": { "Authorization": "Bearer jts_…" }
    },
    "journl-family": {
      "type": "http",
      "url": "https://<journl-host>/mcp/family",
      "headers": { "Authorization": "Bearer jts_…" }
    }
  }
}
```

Der Server ist bewusst *dual-era*: Er beantwortet sowohl die aktuelle Revision
`2026-07-28` (Protokollversion als Request-Metadaten, `server/discover`) als
auch den älteren `initialize`-Handshake bis `2024-11-05`. Clients beider
Generationen funktionieren damit gegen dieselbe URL.

## 4. Werkzeuge

Empfohlener Ablauf: `list_tags` (welche Vokabeln gibt es?) → `list_days` (an
welchen Tagen?) → `get_blocks` oder `get_day` (was steht da?).

### `list_tags` — verfügbare Hashtags

Nennt ohne Argumente alle Hashtags des Kontos — **freigegebene wie
vorgeschlagene** — je mit Geltungsbereich (`personal`/`family`), Freigabestatus
und der Anzahl Journalblöcke, die den Tag tragen (nach Trefferzahl sortiert).
Gedacht als Einstieg vor `get_blocks`, damit das Modell nicht raten muss.
Bewusst geblockte Tags fehlen, wie überall.

```jsonc
{"tags": [{"name": "jtracker", "scope": "personal", "approved": true, "blocks": 101}], "count": 1}
```

### `list_days` — Brücke zwischen Datum und Hashtag

Nennt je Journaltag die Hashtags mit Anzahl — **ohne Blocktext**. Damit
beantwortet ein Modell „was war am Wochenende los" mit einem Aufruf und holt
erst danach gezielt die interessanten Blöcke.

| Argument | Pflicht | Bedeutung |
| --- | --- | --- |
| `start_date` / `end_date` | nein | ISO-Datum; ohne Angabe der ganze Index |
| `tags` | nein | nur Tage, die **alle** diese Hashtags tragen |
| `limit` | nein | Standard 62, Maximum 366; beim Kürzen bleiben die **neuesten** Tage |

```jsonc
{"days": [{"date": "2026-08-03", "weekday": "Mo", "blocks": 4,
           "tags": {"jtracker": 2, "garten": 1}}], "count": 1, "truncated": false}
```

Mit `tags` wird daraus die Rückwärtssuche: „an welchen Tagen im letzten Jahr ging
es um `#server`" liefert die Datumsliste, ohne einen einzigen Block zu laden.

### `get_day` — ganze Journaltage

Liefert alle Blöcke eines Tages, **auch die ohne Hashtag** — genau die, die
`get_blocks` nie finden kann, weil der Hashtag-Index sie nicht kennt.

| Argument | Pflicht | Bedeutung |
| --- | --- | --- |
| `start_date` | ja | ISO-Datum |
| `end_date` | nein | ohne Angabe genau `start_date`; höchstens 7 Tage Spanne |
| `limit` | nein | Standard 100 Blöcke, Maximum 200 |

Die Spanne ist bewusst eng gedeckelt: Ein breiterer Bereich würde den
Modellkontext fluten. Sichtbarkeit und Inhalt kommen aus der aktuellen
Markdown-Datei, nicht aus einem Index.

### `get_blocks` — Journalblöcke nach Hashtag

Liefert die *einzelnen* Journalblöcke, die **alle** angegebenen Hashtags
tragen. Genau dafür gedacht, gezielt Journalinhalte in den Modellkontext zu
holen, statt ganze Tage zu laden.

| Argument | Pflicht | Bedeutung |
| --- | --- | --- |
| `tags` | ja | Liste von Hashtags ohne `#`; ein Treffer muss alle tragen |
| `start_date` / `end_date` | nein | ISO-Datum; für einen einzelnen Tag beide gleich setzen |
| `limit` | nein | Standard 50, Maximum 200 |

Auf dem Familien-Server bleiben Datumsfilter wirkungslos: Familienzeilen sind an
Zeilennummern verankert und tragen kein Datum.

Jeder Block nennt neben `tags` auch `ai_tags`: die Teilmenge, die eine KI
vergeben hat. Was der Nutzer selbst getippt hat, steht nie darin.

Erfasst sind alle Hashtags aus dem Hashtag-Index — freigegebene ebenso wie noch
vorgeschlagene; nur bewusst geblockte Tags bleiben draußen. Als Treffer zählt
jeder abgetrennte Journalblock mit eindeutigem Zeitstempel, gleich ob
`Datum & Uhrzeit:`-Eintrag oder Schnellnotiz.

### `search` — Volltextsuche

Deutsche PostgreSQL-Volltextsuche über die verwalteten Journale, mit Ranking
und hervorgehobenem Ausschnitt. Argumente: `query` (Pflicht), `tags`,
`start_date`, `end_date`, `limit` (max. 100), `offset`.

Dieses Werkzeug erscheint nur, wenn der DB-Suchindex aktiv ist
(`JOURNL_DB_ENABLED`, DEV). Ist er inaktiv, taucht `search` nicht in
`tools/list` auf; ein trotzdem abgesetzter Aufruf antwortet mit einer
Klartextmeldung statt eines Protokollfehlers. `get_blocks` ist davon
unabhängig und arbeitet immer datei-basiert.

## 5. Sicherheit

- Kein Cookie- und kein CSRF-Pfad: Ein eingeloggter Browser kann den Endpunkt
  nicht im Namen des Nutzers aufrufen, nur ein Träger des Schlüssels.
- Alle Werkzeuge sind **rein lesend** und immer auf den Besitzer des
  Schlüssels und den Bereich des Endpunkts eingegrenzt.
- Der Bereich wird ausschließlich vom Endpunkt gesetzt. Ein Client, der ihn über
  ein `sources`-Argument mitschicken will, wird mit einem Protokollfehler
  abgewiesen.
- Blockinhalte werden beim Abruf aus der aktuellen Markdown-Datei gelesen, nie
  aus dem Index. Ein veralteter Index kann also nichts sichtbar machen, was in
  der Quelle nicht mehr steht.
- Der `Origin`-Header wird geprüft (DNS-Rebinding-Schutz). Fremde Origins
  lassen sich per `JOURNL_MCP_ALLOWED_ORIGINS` (kommagetrennt) freigeben.
- Blocktexte werden bei 4000 Zeichen gekappt; gekappte Blöcke sind mit
  `truncated: true` markiert.

## Schnelltest

```bash
curl -s -X POST https://<journl-host>/mcp/privat \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}'

curl -s -X POST https://<journl-host>/mcp/privat \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":2,"method":"tools/call",
       "params":{"name":"get_blocks","arguments":{"tags":["focus"],
                 "start_date":"2026-08-01","end_date":"2026-08-31"}}}'
```
