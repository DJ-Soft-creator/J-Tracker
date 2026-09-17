# Screenshot-Upload vom iPhone

Die Upload-API legt Bilder zunächst unverlinkt im Tagesordner
`YYYY/MM/DD/media/Screenshot-Inbox/` ab. Im Schreiben-Tab erscheint für heutige
Uploads ein dezenter Hinweis. Über das Punkte-Menü lassen sich außerdem offene
Screenshots eines anderen Datums auswählen. Erst ein erfolgreich gesendeter
Journaleintrag entfernt die Inbox-Kopie.

## Upload-Schlüssel

Unter **Einstellungen → Screenshot-Upload vom iPhone** kann ein persönlicher
Schlüssel erzeugt oder widerrufen werden. Journl speichert nur dessen SHA-256-
Hash; der vollständige Schlüssel wird genau nach der Erzeugung angezeigt. Ein
neu erzeugter Schlüssel widerruft den bisherigen sofort.

Der Schlüssel darf ausschließlich für HTTPS-Verbindungen verwendet und wie ein
Passwort behandelt werden. Seine Berechtigung ist auf neue Screenshot-Uploads
beschränkt; Journale oder vorhandene Medien können damit weder gelesen noch
verändert werden.

## HTTP-Aufruf

```text
POST https://JOURNL-HOST/api/screenshot-inbox/upload
Authorization: Bearer jti_...
Content-Type: multipart/form-data
Formularfeld: file
```

Unterstützt werden PNG, JPEG und WebP bis 40 MB. Alternativ darf die Bilddatei
als roher Request-Body gesendet werden; der optionale Header `X-Filename` setzt
dann den angezeigten Originalnamen. `X-Captured-At` beziehungsweise das
Formularfeld `captured_at` kann einen ISO-8601-Aufnahmezeitpunkt übergeben. Die
Ablage erfolgt unabhängig davon immer unter dem serverseitig aktuellen Tag.

Beispiel:

```sh
curl -f -X POST \
  -H 'Authorization: Bearer jti_DEIN_SCHLUESSEL' \
  -F 'file=@Screenshot.png' \
  https://JOURNL-HOST/api/screenshot-inbox/upload
```

Für einen iPhone-Kurzbefehl wird die Aktion **Inhalte von URL abrufen** mit der
angezeigten Upload-URL, Methode `POST`, dem Authorization-Header und einem
Formularfeld `file` verwendet. Der Header-Name ist exakt `Authorization`, der
Header-Wert lautet `Bearer DEIN_SCHLÜSSEL` mit genau einem Leerzeichen zwischen
`Bearer` und dem Schlüssel. Den gesamten Ausdruck nicht als Header-Namen
eintragen und keine Anführungszeichen oder zusätzlichen Zeilenumbrüche
übernehmen. Der Kurzbefehl kann anschließend im Share Sheet für Bilder
aktiviert werden.
