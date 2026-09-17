# Family Scheduler

Der Scheduler erzeugt aus den Plänen in `data/family/planner/recurring.md`
die jeweils fälligen Aufgaben in `data/family/Familien-Aufgaben.md`.

## Unterstützte Intervalle

- `once`: einmalig am Startdatum
- `daily`: täglich ab dem Startdatum
- `weekly_monday` bis `weekly_sunday`: am gewählten Wochentag
- `biweekly`: alle 14 Tage ab dem Startdatum
- `monthly_first`: jeweils am ersten Tag des Monats

Das Startdatum ist immer die früheste mögliche Ausführung. Ein Plan kann mit
`active: false` pausiert werden.

## Datenformat

Planner-Eintrag:

```markdown
- id: task-001 | title: Müll rausbringen | user: <uuid> | recurrence: weekly_monday | start_date: 2026-07-13 | active: true | created_at: 2026-07-13T10:00:00+02:00 | created_by: <uuid>
```

Erzeugte Aufgabe:

```markdown
- [ ] id: task-001-2026-07-13 | title: Müll rausbringen | user: <uuid> | target-date: 2026-07-13 | source: recurring | recurrence: weekly_monday | plan_id: task-001 | created_at: 2026-07-13T06:00:00+02:00 | created_by: <uuid>
```

Bestehende Einträge im früheren `- []`-Format werden weiterhin gelesen. Neue
Einträge werden im kanonischen Markdown-Checkboxformat `- [ ]` geschrieben.

## Ausführung

Einmalig:

```bash
DATA_DIR=/app/data python scripts/scheduler.py
```

Als täglich laufender Prozess, standardmäßig um 06:00 Uhr:

```bash
DATA_DIR=/app/data python scripts/scheduler.py --loop
```

Die Stunde lässt sich mit `--hour 7` oder `SCHEDULER_HOUR=7` ändern. Die
Zeitzone kommt aus `TZ` und ist standardmäßig `Europe/Berlin`.

## Docker Compose

`docker-compose.yml` enthält den Service `family-scheduler`. Er verwendet
dasselbe Daten-Volume wie die Web-App und startet den Scheduler direkt sowie
anschließend einmal täglich:

```bash
docker compose up -d --build
docker compose logs -f family-scheduler
```

Zusätzlich prüft die Web-App beim Öffnen des Familienbereichs, ob heute fällige
Aufgaben fehlen. Beide Wege sind durch dieselbe Dateisperre und Aufgaben-ID
idempotent; parallele Läufe erzeugen keine Duplikate.

## Abgrenzung

Der Family Scheduler läuft als Compose-Service. Der separate hostseitige
Cron-Monitor für explizite KI-Aufträge ist unter
[External Journal Monitor](README_journal_monitor.md) dokumentiert.
