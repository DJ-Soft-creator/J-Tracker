# iPhone-Kurzbefehl: Screenshot an Journl senden

Mit diesem Kurzbefehl kannst du einen Screenshot über das iPhone-Teilen-Menü an
Journl senden. Er landet zunächst unverlinkt im Schreiben-Tab und wird erst nach
deiner Auswahl in einen Journaleintrag übernommen.

Für Text, URLs, Audio, Bilder oder Dateien, die automatisch durch einen
expliziten AI-Workflow als neuer Journaleintrag verarbeitet werden sollen, gibt
es zusätzlich die universelle [Share-API](features/share-api.md). Der hier
beschriebene Screenshot-Eingang bleibt bewusst der manuell kontrollierte Weg.

## 1. Upload-Zugang in Journl vorbereiten

1. Öffne in Journl **Einstellungen → Screenshot-Upload vom iPhone**.
2. Tippe auf **Schlüssel erzeugen**.
3. Kopiere den angezeigten Schlüssel sofort. Er wird nur einmal vollständig
   angezeigt.
4. Kopiere auch die dort angezeigte **Upload-URL**.

Der Schlüssel ist wie ein Passwort zu behandeln. Falls er verloren geht oder
weitergegeben wurde, kannst du ihn in Journl widerrufen und neu erzeugen.

## 2. Kurzbefehl auf dem iPhone anlegen

1. Öffne die App **Kurzbefehle** und erstelle mit **+** einen neuen Kurzbefehl.
2. Gib ihm beispielsweise den Namen **Screenshot an Journl**.
3. Öffne die Kurzbefehl-Details und aktiviere **Im Share-Sheet anzeigen**.
4. Beschränke die erlaubten Eingaben nach Möglichkeit auf **Bilder**.
5. Füge die Aktion **Inhalte von URL abrufen** hinzu – wichtig: nicht eine
   Webseiten-Aktion.
6. Setze als URL die zuvor aus Journl kopierte Upload-URL ein.
7. Klappe in der Aktion die erweiterten Einstellungen auf und wähle:

   - Methode: **POST**
   - Header: `Authorization`
   - Header-Wert: `Bearer DEIN_SCHLÜSSEL`
   - Anfragetext: **Formular**
   - Neues Formularfeld vom Typ **Datei**
   - Name des Formularfelds: `file`
   - Wert des Formularfelds: **Kurzbefehlseingabe**

Der Header muss aus dem Namen `Authorization` und dem Wert `Bearer
DEIN_SCHLÜSSEL` bestehen. Zwischen `Bearer` und dem Schlüssel muss genau ein
Leerzeichen stehen; nicht den gesamten Text als Header-Namen eintragen und
keine Anführungszeichen oder zusätzlichen Zeilenumbrüche übernehmen. Das
Formularfeld muss exakt `file` heißen.

Für den ersten Test kannst du danach die Aktion **Ergebnis anzeigen** ergänzen.
Bei einem erfolgreichen Upload enthält die Antwort `"ok": true`. Anschließend
kannst du diese Testaktion wieder entfernen.

## 3. Ausprobieren

1. Erstelle oder öffne einen Screenshot in der Fotos-App.
2. Tippe auf **Teilen**.
3. Wähle **Screenshot an Journl**.
4. Öffne Journl und gehe zum Tab **Schreiben**.
5. Tippe auf den dezenten Hinweis für den neuen Screenshot und anschließend auf
   **Aufnehmen**.
6. Sende den Eintrag wie gewohnt ab.

Standardmäßig zeigt Journl die Uploads des aktuellen Tages. Einen älteren
Screenshot findest du im Punkte-Menü unter **Hochgeladene Screenshots** über die
Datumsauswahl oder **Alle offenen**.

Unterstützt werden PNG, JPEG und WebP bis 40 MB. Die technische API-Beschreibung
steht unter [Screenshot-Upload-API](features/screenshot-upload-api.md).
