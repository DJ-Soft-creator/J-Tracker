# Synthetische DB-Migrations-Fixtures (Phase 0A)

Diese Dateien sind ausschliesslich Parser-/Migrationsfaelle. Sie enthalten keine
echten Journale, Namen, Secrets, Medien oder Datenroot-Pfade. Alle UUIDs nutzen den
Dokumentationsbereich `00000000-0000-4000-8000-0000000000xx`.

| Fixture | Benutzerinhalt, der erhalten bleibt | Technische Metadaten | Spaetere Behandlung |
|---|---|---|---|
| `current-day-journal.md` | beide Eintraege, Hashtag, Medienlink | keine | Tagesjournal normal importierbar |
| `historical-day-journal.md` | historischer Eintrag | keine | Tagesjournal normal importierbar |
| `quick-note.md` | gesamte Schnellnotiz | keine | nur Dokumentrevision, kein Zeitblock behaupten |
| `hashtag-footer-valid.md` | Journaltext oberhalb Footer | `jt:hashtag-index` | erst nach gueltigem Footer-Parsing auslagern |
| `hashtag-footer-malformed.md` | gesamter Inhalt | unvollstaendiger Footer | blockieren, nichts entfernen |
| `media-transcript-valid.md` | Label, Link, Transkript | `jt:media`, `jt:transcript` | Metadaten nur bei gueltigem JSON auslagern |
| `media-transcript-malformed.md` | gesamter Inhalt inkl. Text | defektes Medien-/Transkript-JSON | blockieren |
| `ai-session-valid.md` | Anfrage und sichtbarer Eventbody | `jt:ai-session-config`, `jt:ai-session-event` | erst mit Sessionuebernahme bereinigen |
| `ai-session-malformed.md` | gesamter Inhalt | defekte AI-Konfiguration | blockieren |
| `agent-session-valid.md` | Anfrage, Eventbody, Agentenantwort | `jt:agent-session-config`, `jt:agent-session-event`, `jt:agent-session` | sichtbare Antwort immer erhalten |
| `agent-session-malformed.md` | gesamter Inhalt | doppelte/defekte Agentenmarker | blockieren |
| `unknown-marker.md` | gesamter Inhalt | unbekanntes `jt:` | blockieren, nie raten |
| `normal-comment.md` | Kommentar und Text | keine | Kommentar bleibt unveraendert |
| `edge-cases.md`, `edge-cases-crlf.md` | sichtbarer Text zwischen Bloecken | keine | Unicode, LF und echte CRLF-Bytes sowie doppelte Zeitanker pruefen; doppelte Anker nicht automatisch zuordnen |
| `Journal_2026-08-11.md.sync-conflict-synthetic` | beide Konfliktseiten | keine | als externe Konfliktdatei inventarisieren, nicht materialisieren |
