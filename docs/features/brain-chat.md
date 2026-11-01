# Chat with your Notes

Im Brain-Tab liegt hinter dem „⋯“-Menü der Eintrag **Chat with your Notes**:
eine Chatansicht, in der ein lokales Sprachmodell die eigenen Journale in
natürlicher Sprache beantwortet. Die Inhalte holt sich das Modell selbst — über
dieselben Werkzeuge, die auch der MCP-Endpunkt `/mcp/privat` anbietet
(siehe [`mcp-api.md`](mcp-api.md)).

## 1. Kein Schlüssel nötig

Der Chat ist ein **App-interner MCP-Client ohne Token**: `brain_chat.py` ruft
die Runner aus `mcp_tools.py` direkt in der Sitzung des angemeldeten Benutzers
auf. Es muss also kein Share-API-Schlüssel erzeugt und kein Recht angehakt
werden — der Chat ist für jeden angemeldeten Benutzer sofort da.

Mehr sehen kann er dadurch nicht: `sources` steht fest auf `personal`, genau
wie am Endpunkt. Familiendokumente sind nicht enthalten, auch dann nicht, wenn
sie denselben Hashtag tragen. Geschrieben wird nie — alle Werkzeuge sind lesend.

### Projekte und Notes einbeziehen

Über der Eingabe liegen zwei Schalter, **Projekte einbeziehen** und **Notes
einbeziehen**. Beide sind standardmäßig aus und werden pro Konto in
`brain-chat.json` gespeichert (`include_projects`, `include_notes`); ein
`POST /api/brain/chat/settings` mit nur einem Schalter ändert nur diesen.

Der dritte Schalter **Bedeutungssuche** (`semantic_search`) entscheidet, ob der
Chat nur mit der Wortsuche arbeitet oder zusätzlich mit dem Embedding-Modell
sucht (`semantic_search`, `find_similar`, siehe [MCP-API](mcp-api.md)). Er ist
nur sichtbar, solange die semantische Suche für das Konto an ist. Damit Chat-
und Embedding-Modell gleichzeitig auf den Mac mini passen, im Chat
`ArmyKnife2-t` oder `bonsai-code-t` wählen (Hinweis in den Settings).

| Schalter | Zusätzliche Werkzeuge | Liefert |
|---|---|---|
| Projekte | `list_projects`, `get_project` | Titel, Quelle, offene/erledigte Aufgaben, Auszug; im Detail Freitext, Aufgaben mit Status/Zuständigem/Zieldatum und Kommentare |
| Notes | `list_notes`, `get_note` | Titel, Quelle, Auszug; im Detail der Volltext (gedeckelt) |

Gelesen werden die eigenen `projects/`- bzw. `notes/`-Dateien und die
Familiendateien unter `family/projects/` bzw. `family/notes/`, die der Nutzer
auch in der Brain-Ansicht sieht (dieselbe fail-closed-Prüfung: ungültiges
Frontmatter heißt unsichtbar). `*.lock` und `*.sync-conflict-*` werden
ignoriert. Jedes Ergebnis trägt `id`, `source` (Privat/Familie), `path` und
`title`; ist ein Schalter an, verlangt der System-Prompt, Titel und Quelle zu
nennen. Bei ausgeschalteten Schaltern sind Werkzeugliste und System-Prompt
unverändert.

## 2. Verbindung einrichten

**Einstellungen → Brain → Chat with your Notes**

| Feld | Bedeutung |
|---|---|
| Endpunkt | Basis-URL eines OpenAI-kompatiblen Servers, z. B. `http://192.168.1.50:8080` (llama-swap, llama.cpp, LM Studio, Ollama). Ohne `/v1` und ohne Pfad. |
| API-Schlüssel | Optional. Wird serverseitig gespeichert und **nie** an den Browser ausgeliefert. Ein leeres Feld lässt einen gespeicherten Schlüssel unverändert. |
| Modell | Kommt aus der **live gelesenen** Modell-Liste des Endpunkts (`/v1/models`), nicht aus einer Vermutung. Erst *Modelle laden*, dann wählen. |

Erlaubt sind wie bei den Agent-Projekten nur Endpunkte im lokalen Netz
(Loopback oder private IP-Bereiche). Journalinhalte verlassen den Host damit
nicht. Im Alltag genügt es, nach der Ersteinrichtung das Modell zu wechseln.

## 3. Bedienung

Oben der Verlauf, unten die Eingabe: Textfeld, **Senden**, daneben ein kleines
KI-Menü. Enter sendet, Shift+Enter macht eine neue Zeile. Desktop und Mobil
tragen denselben Menüeintrag und denselben Einstellungsabschnitt.

Das KI-Menü hält bewusst nur wenige Einträge — es ist der vorgesehene Platz für
spätere Chat-Funktionen:

- **Nachweise anzeigen** — blendet unter jeder Antwort ein, welche Werkzeuge mit
  welchen Argumenten liefen und welche Tage bzw. Hashtags sie trafen.
- **Verlauf leeren** — der Verlauf lebt nur in der geöffneten Ansicht; er wird
  nirgends gespeichert.
- **Verbindung & Modell** — springt in die Einstellungen.

## 4. Grenzen

- **Tool-Calling ist Voraussetzung.** Ein Modell ohne Werkzeugunterstützung kann
  den Chat nicht bedienen; meldet der Endpunkt das, zeigt die Ansicht es
  wortwörtlich an. Ein Modell, das Werkzeugaufrufe stattdessen als Fließtext
  halluziniert, bekommt keine Journalinhalte zu sehen und antwortet entsprechend
  leer — die Antwort wird nie aus dem Modellwissen ergänzt.
- **Die Recherche ist gedeckelt.** Nach `brain_chat.MAX_ROUNDS` Werkzeugrunden
  erzwingt der Server eine Antwort aus dem bereits Gelesenen und markiert sie in
  den Nachweisen als gekürzt. Die Mengengrenzen der Werkzeuge selbst
  (`limit`, Tagesspanne, Blocklänge) gelten unverändert.
- **Kein Verlauf auf Platte.** Es gibt keine durchsuchbare Chat-Ablage; jede
  Frage schickt den bisherigen Verlauf erneut mit.
